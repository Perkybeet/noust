# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one way Noust talks to GitHub's REST API.

A small JSON client over :mod:`urllib`, on purpose: no new dependency, one
fixed host, a timeout on every request, and one error for every way a request
can fail. The host is a constructor argument only so the tests can point it at
a fake GitHub on loopback; nothing in Noust builds a client for another host,
and a path that is not a path on that host (an absolute URL a response handed
back, say) is refused rather than followed.

GitHub's own ``message`` is kept verbatim in the error's ``output``: an
operator reading "Resource not accessible by integration" knows which
permission the App lacks, and a paraphrase would hide exactly that.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from collections.abc import Callable, Mapping
from typing import IO, Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from noust import __version__
from noust.core.exceptions import IntegrationError

#: GitHub's REST API.
API_URL = "https://api.github.com"

#: GitHub's website, where Apps are created, installed and deleted.
WEB_URL = "https://github.com"

#: Seconds before a request to GitHub is abandoned. A deploy never waits on
#: GitHub for longer than this per call, and the console's request neither.
REQUEST_TIMEOUT = 15

#: The API version every request pins, so a change on GitHub's side cannot
#: silently change what a response means.
API_VERSION = "2022-11-28"

#: Items per page when listing; GitHub's maximum.
PAGE_SIZE = 100

#: Pages read at most when listing. Ten thousand repositories is more than
#: an installation for one server holds; a loop without a bound is not.
MAX_PAGES = 100

#: Bytes of a response read at most. GitHub's answers here are kilobytes.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

#: Anything with urlopen's calling convention: ``opener(request, timeout=...)``.
Opener = Callable[..., IO[bytes]]


def json_object(value: Any) -> dict[str, Any]:
    """
    Treat a field of GitHub's JSON that should be an object as one.

    Args:
        value: The field.

    Returns:
        The field when it is an object, else an empty one, so a payload
        missing a part reads as missing rather than raising.
    """
    return value if isinstance(value, dict) else {}


class GitHubAPIError(IntegrationError):
    """
    GitHub answered a request with an error status.

    Attributes:
        status: The HTTP status GitHub answered.
    """

    def __init__(self, message: str, *, status: int, details: str = "", output: str | None = None):
        """
        Args:
            message: What failed.
            status: GitHub's HTTP status.
            details: How to fix it.
            output: GitHub's own message, verbatim.
        """
        super().__init__(message, details, output=output)
        self.status = status


def _hint(status: int) -> str:
    """
    Say what an error status from GitHub usually means for this server.

    Args:
        status: The HTTP status.

    Returns:
        The hint shown above GitHub's own message.
    """
    if status == 401:
        return (
            "GitHub did not accept this server's credentials. If the App was deleted on "
            "GitHub, remove the integration and create it again."
        )
    if status == 403:
        return (
            "The App lacks a permission for this, or GitHub is rate limiting it. "
            "Check the App's permissions on GitHub and accept any pending permission request."
        )
    if status == 404:
        return (
            "GitHub does not show this to the App: the repository is not in the "
            "installation, or the installation or App no longer exists."
        )
    if status == 422:
        return "GitHub refused the request as invalid."
    return "Retry in a moment; if it persists, check https://www.githubstatus.com."


class GitHubClient:
    """
    JSON over HTTPS to one GitHub API host.

    Args:
        base_url: The API root. Always :data:`API_URL` in Noust; tests pass a
            loopback server.
        timeout: Seconds per request.
        opener: Replacement for :func:`urllib.request.urlopen`.
    """

    def __init__(
        self,
        base_url: str = API_URL,
        *,
        timeout: float = REQUEST_TIMEOUT,
        opener: Opener | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener: Opener = opener or urllib.request.urlopen

    def request(
        self,
        method: str,
        path: str,
        *,
        authorization: str | None = None,
        body: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
    ) -> Any:
        """
        Send one request and decode its JSON answer.

        Args:
            method: HTTP method.
            path: Path on the API host, starting with ``/``.
            authorization: The ``Authorization`` header value (``Bearer
                <jwt>`` or ``token <installation token>``), or None.
            body: JSON body, or None.
            query: Query string parameters.

        Returns:
            The decoded JSON, or None for an empty answer (204).

        Raises:
            IntegrationError: The path is not a path, GitHub could not be
                reached, or answered something that is not JSON.
            GitHubAPIError: GitHub answered an error status.
        """
        if not path.startswith("/") or path.startswith("//"):
            raise IntegrationError(
                f"Refusing to request {path!r} from GitHub",
                details="Only paths on the GitHub API host are requested.",
            )
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        data = json.dumps(body).encode() if body is not None else None
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": f"noust/{__version__}",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        if authorization:
            headers["Authorization"] = authorization
        request = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)

        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            raise self._error(method, path, exc) from None
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None) or exc
            raise IntegrationError(
                "GitHub could not be reached",
                details="Check this server's outbound HTTPS access to api.github.com.",
                output=str(reason),
            ) from None

        if len(raw) > MAX_RESPONSE_BYTES:
            raise IntegrationError(
                f"GitHub's answer to {method.upper()} {path} is too large",
                details=f"More than {MAX_RESPONSE_BYTES} bytes.",
            )
        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise IntegrationError(
                f"GitHub's answer to {method.upper()} {path} is not JSON",
                details=str(exc),
            ) from None

    def _error(self, method: str, path: str, exc: HTTPError) -> GitHubAPIError:
        """
        Turn an error status into the error Noust raises, GitHub's words kept.

        Args:
            method: The request's method.
            path: The request's path.
            exc: urllib's error, carrying the response.

        Returns:
            The error to raise.
        """
        message = ""
        try:
            raw = exc.read(64 * 1024)
            payload = json.loads(raw) if raw else {}
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            payload = {}
        if isinstance(payload, dict):
            message = str(payload.get("message") or "")
            errors = payload.get("errors")
            if isinstance(errors, list) and errors:
                detail = "; ".join(
                    str(e.get("message") or e.get("code") or e) if isinstance(e, dict) else str(e)
                    for e in errors
                )
                message = f"{message} ({detail})" if message else detail
        return GitHubAPIError(
            f"GitHub refused {method.upper()} {path} ({exc.code})",
            status=exc.code,
            details=_hint(exc.code),
            output=message or str(exc.reason),
        )

    def paginate(
        self,
        path: str,
        *,
        authorization: str | None = None,
        key: str | None = None,
        query: Mapping[str, Any] | None = None,
    ) -> list[Any]:
        """
        Read every page of a listing.

        Args:
            path: The listing's path.
            authorization: As for :meth:`request`.
            key: The field holding the items when the answer is an object
                (``repositories`` for ``/installation/repositories``); None
                when the answer is the list itself.
            query: Extra query parameters.

        Returns:
            Every item, in GitHub's order.

        Raises:
            IntegrationError: As :meth:`request` does, or the answer is not
                the listing it should be.
        """
        items: list[Any] = []
        for page in range(1, MAX_PAGES + 1):
            answer = self.request(
                "GET",
                path,
                authorization=authorization,
                query={**(query or {}), "per_page": PAGE_SIZE, "page": page},
            )
            batch = answer.get(key) if key and isinstance(answer, dict) else answer
            if not isinstance(batch, list):
                raise IntegrationError(
                    f"GitHub's answer to GET {path} is not a listing",
                    details="Expected a list of items.",
                )
            items.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
        return items


_lock = threading.Lock()
_client: GitHubClient | None = None


def get_client() -> GitHubClient:
    """
    Return the process-wide client.

    Returns:
        The client every GitHub call in Noust goes through.
    """
    global _client
    with _lock:
        if _client is None:
            _client = GitHubClient()
        return _client


def set_client(client: GitHubClient | None) -> None:
    """
    Replace the process-wide client (tests point it at a fake GitHub).

    Args:
        client: The client, or None for the real one on next use.
    """
    global _client
    with _lock:
        _client = client
