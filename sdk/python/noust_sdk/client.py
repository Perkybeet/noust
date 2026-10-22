# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The client: one place that makes requests; every operation is a method generated from the schema.

Nothing but the standard library. The token travels in an ``Authorization`` header, is never part of
a URL or of an error message, and is refused a plain-HTTP connection to any host but loopback, so
it cannot be sent in the clear by a typo in an address.
"""

from __future__ import annotations

import http.client
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from noust_sdk._operations import Api
from noust_sdk.errors import (
    APIError,
    ConnectionFailed,
    ElevationRequired,
    JobFailed,
    RateLimited,
)

USER_AGENT = "noust-sdk/0.1"

#: A read is repeated after these answers; a change never is, since it may have been made.
RETRY_STATUSES = frozenset({429, 502, 503, 504})
MAX_RETRY_WAIT = 30.0

#: Hosts a token may be sent to over plain HTTP.
LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})

FINAL_JOB_STATES = frozenset({"succeeded", "failed", "cancelled"})


@dataclass
class Request:
    """
    One HTTP request, as a transport receives it.

    Attributes:
        method: The verb.
        url: The whole address.
        headers: The headers, token included.
        body: The encoded body, or None.
        timeout: Seconds to wait.
    """

    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None
    timeout: float


@dataclass
class Response:
    """
    One HTTP answer, as a transport returns it.

    Attributes:
        status: The status.
        headers: The headers, names lower-cased.
        body: The raw body.
    """

    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


Transport = Callable[[Request], Response]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the token to wherever it points: it is an error here."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _urllib_transport(context: ssl.SSLContext | None) -> Transport:
    """
    Args:
        context: The TLS settings, or None for the system's.

    Returns:
        A transport over ``urllib`` that follows no redirect.
    """
    opener = urllib.request.build_opener(
        _NoRedirect(), urllib.request.HTTPSHandler(context=context)
    )

    def send(request: Request) -> Response:
        prepared = urllib.request.Request(  # noqa: S310 - the scheme is checked by the client
            request.url, data=request.body, headers=request.headers, method=request.method
        )
        try:
            with opener.open(prepared, timeout=request.timeout) as answer:
                return Response(
                    answer.status,
                    {k.lower(): v for k, v in answer.headers.items()},
                    answer.read(),
                )
        except urllib.error.HTTPError as error:
            return Response(
                error.code, {k.lower(): v for k, v in error.headers.items()}, error.read()
            )
        except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
            raise ConnectionFailed(f"Cannot reach {request.url.split('?')[0]}: {error}") from error

    return send


class Client(Api):
    """
    A Noust server, as a Python object.

    Args:
        url: The console's address, such as ``https://noust.example.com``.
        token: An API token (``noust token create``). Read from ``NOUST_TOKEN`` when omitted.
        timeout: Seconds to wait for one answer.
        verify: ``False`` to accept any certificate (never), or the path of a CA file to trust
            instead of the system's, for a console on a private certificate.
        retries: How many times a read is repeated after a ``429`` or a gateway error.
        transport: Replaces the network, for tests: it receives a :class:`Request` and returns a
            :class:`Response`.

    Raises:
        ValueError: The address is not an http(s) one, or is plain HTTP to a host other than loopback.
    """

    def __init__(
        self,
        url: str,
        token: str | None = None,
        *,
        timeout: float = 60.0,
        verify: bool | str = True,
        retries: int = 2,
        transport: Transport | None = None,
    ) -> None:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"{url!r} is not an http(s) address")
        if parsed.scheme == "http" and parsed.hostname not in LOOPBACK:
            raise ValueError(
                "The token would cross the network in clear: use https:// "
                f"(plain http is only for {', '.join(sorted(LOOPBACK))})"
            )
        self._base = f"{parsed.scheme}://{parsed.netloc}"
        self._token = token if token is not None else os.environ.get("NOUST_TOKEN")
        self._timeout = timeout
        self._retries = max(0, retries)
        context: ssl.SSLContext | None = None
        if verify is False:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        elif isinstance(verify, str):
            context = ssl.create_default_context(cafile=verify)
        self._transport = transport or _urllib_transport(context)
        self._sleep = time.sleep
        super().__init__(self.request)

    @classmethod
    def from_environment(cls, **options: Any) -> Client:
        """
        Returns:
            A client for ``NOUST_URL`` with the token in ``NOUST_TOKEN``: how a pipeline holds both,
            never as arguments of a command.

        Raises:
            ValueError: ``NOUST_URL`` is not set.
        """
        url = os.environ.get("NOUST_URL")
        if not url:
            raise ValueError("NOUST_URL is not set")
        return cls(url, **options)

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Any = None,
        binary: bool = False,
    ) -> Any:
        """
        Make one request and decode the answer.

        Args:
            method: The verb.
            path: The route, from ``/api`` on.
            query: Query parameters; ``None`` values are left out, booleans are ``true`` and ``false``.
            body: A JSON-serialisable body, or None.
            binary: Return the raw bytes instead of decoding JSON.

        Returns:
            The decoded body, or its bytes for a file.

        Raises:
            APIError: The server refused the call (a subclass for sudo mode and for the rate limit).
            ConnectionFailed: The server could not be reached.
        """
        headers = {"Accept": "*/*" if binary else "application/json", "User-Agent": USER_AGENT}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        payload: bytes | None = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        pairs: list[tuple[str, str]] = []
        for key, value in (query or {}).items():
            if value is None:
                continue
            for item in value if isinstance(value, (list, tuple)) else [value]:
                pairs.append((key, "true" if item is True else "false" if item is False else str(item)))
        url = self._base + path + (f"?{urllib.parse.urlencode(pairs)}" if pairs else "")
        request = Request(method.upper(), url, headers, payload, self._timeout)
        attempts = 1 + (self._retries if method.upper() == "GET" else 0)
        for attempt in range(attempts):
            answer = self._transport(request)
            if answer.status in RETRY_STATUSES and attempt + 1 < attempts:
                self._sleep(min(MAX_RETRY_WAIT, self._wait(answer, attempt)))
                continue
            break
        if answer.status >= 400:
            raise self._refusal(answer)
        if binary:
            return answer.body
        if not answer.body.strip():
            return None
        try:
            return json.loads(answer.body)
        except ValueError as error:
            raise ConnectionFailed(f"{path} did not answer JSON: {answer.body[:80]!r}") from error

    @staticmethod
    def _wait(answer: Response, attempt: int) -> float:
        """
        Args:
            answer: A refusal worth repeating.
            attempt: How many times it has been tried.

        Returns:
            Seconds to wait: what the server asked, or a growing pause.
        """
        try:
            return float(answer.headers.get("retry-after", ""))
        except ValueError:
            return float(2**attempt)

    @staticmethod
    def _refusal(answer: Response) -> APIError:
        """
        Args:
            answer: A ``4xx`` or ``5xx`` answer.

        Returns:
            The error to raise, in the class that says what the caller can do about it.
        """
        try:
            body: Any = json.loads(answer.body)
        except ValueError:
            body = answer.body.decode("utf-8", errors="replace")[:500]
        retry: float | None
        try:
            retry = float(answer.headers.get("retry-after", ""))
        except ValueError:
            retry = None
        error = APIError(answer.status, body, retry_after=retry)
        if error.code == "elevation_required":
            return ElevationRequired(answer.status, body)
        if answer.status == 429:
            return RateLimited(answer.status, body, retry_after=retry)
        return error

    def wait_for_job(
        self,
        job: str | Mapping[str, Any],
        *,
        timeout: float = 1800.0,
        interval: float = 2.0,
        raise_on_failure: bool = True,
    ) -> dict[str, Any]:
        """
        Wait for a job the server queued (a deploy, an update, a backup) to end.

        Args:
            job: The job's id, or the answer that queued it (``job_id`` or ``job.id`` is read).
            timeout: Seconds to wait; the job keeps running on the server when this runs out.
            interval: Seconds between looks.
            raise_on_failure: Raise :class:`JobFailed` for a job that failed or was cancelled.

        Returns:
            The job as the server last described it. Its ``state`` says how it ended, or that it
            is still running when the wait ran out.

        Raises:
            JobFailed: The job ended badly (when ``raise_on_failure``).
            ValueError: ``job`` names no job.
        """
        job_id = job if isinstance(job, str) else (job.get("job_id") or (job.get("job") or {}).get("id"))
        if not job_id:
            raise ValueError("That is not a job: no id")
        deadline = time.monotonic() + timeout
        while True:
            current: dict[str, Any] = self.request("GET", f"/api/jobs/{urllib.parse.quote(str(job_id))}")
            if current.get("state") in FINAL_JOB_STATES:
                if raise_on_failure and current.get("state") != "succeeded":
                    raise JobFailed(current)
                return current
            if time.monotonic() >= deadline:
                return current
            self._sleep(interval)
