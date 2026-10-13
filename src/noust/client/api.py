# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A client of a Noust server's JSON API.

The one way a program other than the console talks to a server over HTTP: the
remote command line (``noust --context prod update shop``), manifests applied
to another server, anything a script wants. It is a client in the strict sense
of the 3.4 design (rule 3): it re-implements nothing the server does, so the
role the token holds, the sudo mode it lacks, the team it is bound to and the
audit trail the call leaves are the server's, exactly as for the console.

Deliberately small and with no dependency beyond the standard library:

- **TLS is verified** against the system's certificate authorities, or the one
  file the connection names. There is no switch that turns verification off. A
  plain ``http://`` address is accepted only for this machine, because a bearer
  token over a network in clear is a token given away.
- **A redirect is never followed**: it would carry the token to wherever the
  answer points. A server that moved is the operator's to re-address.
- **Retries are for what is safe to repeat**: a read is tried again on a
  server error, a gateway or a dropped connection; any call is tried again
  when the server said it was too busy (429), because that call was refused
  and not done. Three tries, honouring ``Retry-After``.
- **The token is never in an error message, a log line or an argument**: it is
  held in memory for the life of the client and written to one header.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from noust import __version__
from noust.client.errors import RemoteError

logger = logging.getLogger(__name__)

#: How long one request may take before it is given up on.
DEFAULT_TIMEOUT = 30.0
#: How many times a call is tried in all.
ATTEMPTS = 3
#: The longest a ``Retry-After`` is honoured, and the pause between plain retries.
MAX_RETRY_WAIT = 30.0
BACKOFF = 1.0
#: Largest answer read, so a hostile server cannot fill memory.
MAX_BODY = 64 * 1024 * 1024
#: Where the API lives; the unversioned address works on every 3.x server.
API_PREFIX = "/api"

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_RETRY_STATUSES = frozenset({500, 502, 503, 504})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse to follow a redirect: it would carry the token somewhere else."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        """
        Returns:
            Always None: the redirect is answered as an error by the caller.
        """
        return None


@dataclass(frozen=True)
class ApiResponse:
    """
    What the server answered.

    Attributes:
        status: The HTTP status.
        body: The decoded JSON body, or None when there was none.
        headers: The response headers, lower-cased names.
    """

    status: int
    body: Any
    headers: Mapping[str, str]

    @property
    def api_version(self) -> str | None:
        """The server's ``X-Noust-API-Version``, when it sent one."""
        return self.headers.get("x-noust-api-version")


def _is_loopback(host: str) -> bool:
    """
    Args:
        host: A host name or address.

    Returns:
        Whether it is this machine.
    """
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def normalise_url(url: str) -> str:
    """
    Check and tidy the address of a server.

    Args:
        url: What was typed, such as ``https://noust.acme.test``.

    Returns:
        ``scheme://host[:port]`` without a path, a query or a trailing slash.

    Raises:
        RemoteError: When it is not an HTTPS address (or an HTTP one for this
            machine), carries credentials, or names no host.
    """
    raw = (url or "").strip()
    if "://" not in raw:
        raw = "https://" + raw
    parts = urllib.parse.urlsplit(raw)
    host = parts.hostname or ""
    if parts.scheme not in ("http", "https") or not host:
        raise RemoteError(
            f"Not the address of a server: {url!r}",
            code="refused_locally",
            details="Write it as https://noust.example.com (add :8080 for a port).",
        )
    if parts.username or parts.password:
        raise RemoteError(
            "The address carries credentials",
            code="refused_locally",
            details="A token travels in its own header, never in the address: use --token-file.",
        )
    if parts.scheme == "http" and not _is_loopback(host):
        raise RemoteError(
            f"Refusing to send a token to {host} in clear text",
            code="refused_locally",
            details=(
                "Use https://. An http:// address is accepted for this machine only, such as "
                "through an SSH tunnel to http://localhost:8080."
            ),
        )
    netloc = parts.netloc.rsplit("@", 1)[-1]
    return f"{parts.scheme}://{netloc}"


class ApiClient:
    """
    Calls a Noust server's API with a bearer token.

    Args:
        url: The server's address.
        token: A Noust API token.
        timeout: Seconds one request may take.
        ca_file: A certificate authority bundle to trust instead of the system's.
        opener: Replaces the network, for tests.
        sleep: Replaces waiting, for tests.
    """

    def __init__(
        self,
        url: str,
        token: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        ca_file: str | None = None,
        opener: Callable[[urllib.request.Request, float], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.url = normalise_url(url)
        self._token = token.strip()
        if not self._token:
            raise RemoteError(
                "There is no token to call the server with",
                code="refused_locally",
                details="Give one with --token-file, --token-env or a context.",
            )
        self.timeout = timeout
        self._sleep = sleep
        self._opener = opener or self._network(ca_file)

    @staticmethod
    def _network(ca_file: str | None) -> Callable[[urllib.request.Request, float], Any]:
        """
        Args:
            ca_file: A bundle to trust, or None for the system's.

        Returns:
            A function that opens a request, never following a redirect.
        """
        context = ssl.create_default_context(cafile=ca_file) if ca_file else None
        handlers: list[Any] = [_NoRedirect()]
        if context is not None:
            handlers.append(urllib.request.HTTPSHandler(context=context))
        opener = urllib.request.build_opener(*handlers)
        return lambda request, timeout: opener.open(request, timeout=timeout)

    # ----------------------------------------------------------------- calls

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Any = None,
        raw: bool = False,
    ) -> ApiResponse:
        """
        Make one call.

        Args:
            method: The HTTP method.
            path: A path below the API, such as ``/apps``, or a full ``/api/...`` one.
            query: Query parameters; ``None`` values are left out.
            body: A JSON-serialisable body, or None.
            raw: Return the body as text instead of decoding JSON.

        Returns:
            The answer.

        Raises:
            RemoteError: When the server cannot be reached, answers something
                that is not the API's contract, or refuses the call (status
                400 and above), carrying its own error, hint and output.
        """
        verb = method.upper()
        target = self._target(path, query)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        for attempt in range(1, ATTEMPTS + 1):
            request = urllib.request.Request(target, data=data, method=verb)
            request.add_header("Authorization", f"Bearer {self._token}")
            request.add_header("Accept", "application/json")
            request.add_header("User-Agent", f"noust-client/{__version__}")
            if data is not None:
                request.add_header("Content-Type", "application/json")
            try:
                status, headers, payload = self._send(request)
            except RemoteError as exc:
                # A dropped connection: safe to repeat only what changes nothing.
                if exc.status == 0 and verb in _SAFE_METHODS and attempt < ATTEMPTS:
                    self._sleep(min(BACKOFF * attempt, MAX_RETRY_WAIT))
                    continue
                raise
            if self._should_retry(verb, status) and attempt < ATTEMPTS:
                self._sleep(self._wait(headers, attempt))
                continue
            return self._finish(status, headers, payload, raw)
        raise AssertionError("unreachable")  # the loop returns or raises on its last turn

    def get(self, path: str, **query: Any) -> Any:
        """
        Args:
            path: A path below the API.
            **query: Query parameters.

        Returns:
            The decoded body.
        """
        return self.request("GET", path, query=query).body

    def post(self, path: str, body: Any = None, **query: Any) -> Any:
        """
        Args:
            path: A path below the API.
            body: The JSON body.
            **query: Query parameters.

        Returns:
            The decoded body.
        """
        return self.request("POST", path, query=query, body=body if body is not None else {}).body

    def put(self, path: str, body: Any) -> Any:
        """
        Args:
            path: A path below the API.
            body: The JSON body.

        Returns:
            The decoded body.
        """
        return self.request("PUT", path, body=body).body

    def patch(self, path: str, body: Any) -> Any:
        """
        Args:
            path: A path below the API.
            body: The JSON body.

        Returns:
            The decoded body.
        """
        return self.request("PATCH", path, body=body).body

    def delete(self, path: str, **query: Any) -> Any:
        """
        Args:
            path: A path below the API.
            **query: Query parameters.

        Returns:
            The decoded body.
        """
        return self.request("DELETE", path, query=query).body

    # -------------------------------------------------------------- internals

    def _target(self, path: str, query: Mapping[str, Any] | None) -> str:
        """
        Args:
            path: A path below the API, or a full ``/api/...`` one.
            query: Query parameters.

        Returns:
            The address to call.
        """
        text = path if path.startswith("/") else "/" + path
        if not (text == API_PREFIX or text.startswith(API_PREFIX + "/")):
            text = API_PREFIX + text
        pairs = [
            (key, "true" if value is True else "false" if value is False else str(value))
            for key, value in (query or {}).items()
            if value is not None
        ]
        suffix = "?" + urllib.parse.urlencode(pairs) if pairs else ""
        return f"{self.url}{text}{suffix}"

    def _send(self, request: urllib.request.Request) -> tuple[int, dict[str, str], bytes]:
        """
        Args:
            request: The request.

        Returns:
            ``(status, headers, body)``.

        Raises:
            RemoteError: With status 0 when no answer arrived.
        """
        try:
            with self._opener(request, self.timeout) as response:
                return response.status, _lower(response.headers), _read(response)
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, _lower(exc.headers), _read(exc)
            finally:
                exc.close()
        except (urllib.error.URLError, http.client.HTTPException, OSError, ssl.SSLError) as exc:
            reason = getattr(exc, "reason", exc)
            raise RemoteError(
                f"Cannot reach {self.url}",
                code="unreachable",
                details=f"{type(reason).__name__}: {reason}",
            ) from exc

    @staticmethod
    def _should_retry(verb: str, status: int) -> bool:
        """
        Args:
            verb: The HTTP method.
            status: The status that came back.

        Returns:
            Whether the same call is worth making again: busy (429) for any
            call, a server or gateway error for one that changes nothing.
        """
        return status == 429 or (status in _RETRY_STATUSES and verb in _SAFE_METHODS)

    @staticmethod
    def _wait(headers: Mapping[str, str], attempt: int) -> float:
        """
        Args:
            headers: The answer's headers.
            attempt: Which try this was.

        Returns:
            Seconds to wait: the server's ``Retry-After`` or a growing pause.
        """
        try:
            asked = float(headers.get("retry-after", ""))
        except ValueError:
            asked = BACKOFF * attempt
        return max(0.0, min(asked, MAX_RETRY_WAIT))

    def _finish(
        self, status: int, headers: Mapping[str, str], payload: bytes, raw: bool
    ) -> ApiResponse:
        """
        Args:
            status: The HTTP status.
            headers: The answer's headers.
            payload: The body.
            raw: Keep the body as text.

        Returns:
            The answer when it is a success.

        Raises:
            RemoteError: For a status of 400 or more, or a body that is not JSON.
        """
        text = payload.decode("utf-8", errors="replace")
        decoded: Any = text if raw else None
        if not raw and text.strip():
            try:
                decoded = json.loads(text)
            except ValueError as exc:
                if status < 400:
                    raise RemoteError(
                        f"{self.url} did not answer with JSON",
                        status=status,
                        code="bad_response",
                        details="Is this the address of a Noust console (not a proxy's page)?",
                    ) from exc
                decoded = {"detail": text[:300]}
        if status >= 400:
            raise _refusal(status, decoded)
        if status >= 300:
            where = headers.get("location", "")
            raise RemoteError(
                f"The server answered {status}: a redirect, which is not followed",
                status=status,
                code="redirect_refused",
                details=(
                    "Following it would send the token to another address"
                    + (f" ({where})" if where else "")
                    + ". Use the address the server is really at."
                ),
            )
        return ApiResponse(status=status, body=decoded, headers=headers)


def _lower(headers: Any) -> dict[str, str]:
    """
    Args:
        headers: An HTTP message's headers.

    Returns:
        Them with lower-case names.
    """
    return {str(key).lower(): str(value) for key, value in headers.items()}


def _read(stream: Any) -> bytes:
    """
    Args:
        stream: A response.

    Returns:
        Its body, at most :data:`MAX_BODY` bytes.

    Raises:
        RemoteError: When it is longer.
    """
    data: bytes = stream.read(MAX_BODY + 1)
    if len(data) > MAX_BODY:
        raise RemoteError("The server's answer is too large", code="bad_response")
    return data


def _refusal(status: int, body: Any) -> RemoteError:
    """
    Build the error for a refused call from the API's own contract.

    Args:
        status: The HTTP status.
        body: The decoded error body.

    Returns:
        The error, carrying ``detail``, ``hint`` and ``output`` as sent.
    """
    contract: dict[str, Any] = body if isinstance(body, dict) else {}
    detail = contract.get("detail")
    if isinstance(detail, dict):  # a handler that raised HTTPException with the contract inside
        contract = detail
        detail = contract.get("detail")
    message = str(detail) if detail else f"The server answered {status}"
    hint = contract.get("hint")
    output = contract.get("output")
    return RemoteError(
        message,
        status=status,
        code=str(contract.get("error") or f"http_{status}"),
        details=str(hint) if hint else None,
        output=str(output) if output else None,
        body=contract,
    )
