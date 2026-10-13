# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one JSON client the cloud providers share.

A small client over :mod:`urllib`, as the GitHub one is, on purpose: no new
dependency, one fixed https host per provider, a timeout on every request, one
error for every way a request can fail, and the provider's own words kept
verbatim in the error's ``output``.

Three things are particular to calling an API that bills:

- **A request that creates is never repeated on its own.** A ``POST`` whose
  answer was lost may have created the machine; retrying would create a second
  one at the operator's cost. Only a rate limit (429, where the provider says it
  did nothing) is waited out and sent again; reads are retried on a server error
  or a dropped connection.
- **The token is never in an error.** Providers echo request details in their
  messages now and then; whatever they print is scrubbed of the token before it
  goes into an exception, a log line or a job.
- **A rehearsal changes nothing there.** ``--dry-run`` swaps the process-wide
  runner for a :class:`~noust.core.runner.DryRunRunner`; any request that is not
  a read refuses when that is the runner, so the promise holds for an API as it
  does for a command.
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable, Mapping
from http.client import HTTPException
from typing import IO, Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from noust import __version__
from noust.core.exceptions import CloudProviderError
from noust.core.runner import DryRunRunner, get_runner

#: Seconds before a request is abandoned.
REQUEST_TIMEOUT = 30

#: Bytes of a response read at most. Listing a provider's images is the largest
#: answer there is, a few hundred kilobytes.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

#: Longest a rate limit is waited out before the request is given up.
MAX_RATE_WAIT = 60

#: Times a request is sent at most, retries included.
MAX_ATTEMPTS = 3

#: Methods that change nothing at the provider.
READ_METHODS = frozenset({"GET", "HEAD"})

#: Anything with urlopen's calling convention: ``opener(request, timeout=...)``.
Opener = Callable[..., IO[bytes]]

#: Reads the provider's own sentence out of an error body.
MessageReader = Callable[[Any], str]


def generic_message(payload: Any) -> str:
    """
    Read a provider's error sentence out of a JSON body of the usual shapes.

    Args:
        payload: The decoded body.

    Returns:
        ``message``, ``error.message`` or ``error``, whichever the body has;
        empty when it has none.
    """
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "")
    if isinstance(error, str):
        return error
    return str(payload.get("message") or "")


def _hint(provider: str, status: int) -> str:
    """
    Say what an error status usually means for this account.

    Args:
        provider: The provider's display name.
        status: The HTTP status.

    Returns:
        The hint shown above the provider's own message.
    """
    if status == 401:
        return (
            f"{provider} did not accept this token. It may be wrong, expired or revoked: "
            "create a new one in the provider's console and save the account again."
        )
    if status == 403:
        return (
            f"The token cannot do this at {provider}. Creating servers needs a token with write "
            "access; a read-only token only lists."
        )
    if status == 404:
        return f"{provider} does not know this: it may have been deleted in its console."
    if status == 422:
        return f"{provider} refused the request as invalid; its message says which part."
    if status == 429:
        return f"{provider} is rate limiting this account. Wait a minute and try again."
    return f"Try again in a moment; if it persists, check {provider}'s status page."


class CloudHttp:
    """
    JSON over HTTPS to one provider's API host, with one account's token.

    Args:
        provider: The provider's display name, for messages.
        base_url: The API root, always ``https://`` and a fixed host; tests pass
            a fake opener, never another host.
        token: The account's API token. Held in memory, sent as a bearer token,
            never put in a URL, an error or a log line.
        message: Reads the provider's sentence out of an error body.
        timeout: Seconds per request.
        opener: Replacement for :func:`urllib.request.urlopen`.
        sleep: Pause between attempts.
        clock: Wall clock, for ``RateLimit-Reset`` arithmetic.
    """

    def __init__(
        self,
        provider: str,
        base_url: str,
        token: str,
        *,
        message: MessageReader = generic_message,
        timeout: float = REQUEST_TIMEOUT,
        opener: Opener | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not base_url.startswith("https://"):
            raise CloudProviderError(
                f"Refusing to talk to {base_url!r}",
                details="A provider's API is only ever reached over https.",
            )
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._message = message
        self.timeout = timeout
        self._opener: Opener = opener or urllib.request.urlopen
        self._sleep = sleep
        self._clock = clock

    def __repr__(self) -> str:
        """Describe the client without its token."""
        return f"CloudHttp({self.provider!r}, {self.base_url!r}, token=***)"

    __str__ = __repr__

    def scrub(self, text: str) -> str:
        """
        Remove the token from a provider's words.

        Args:
            text: What the provider said.

        Returns:
            The text without the token.
        """
        return text.replace(self._token, "***") if self._token else text

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        """
        Send one request and decode its JSON answer.

        Args:
            method: HTTP method.
            path: Path on the API host, starting with ``/``.
            query: Query string parameters.
            body: JSON body, or None.

        Returns:
            The decoded JSON, or None for an empty answer (204).

        Raises:
            CloudProviderError: The path is not a path, a rehearsal is on and the
                request would change something, the provider could not be reached,
                answered an error status or answered something that is not JSON.
        """
        verb = method.upper()
        if not path.startswith("/") or path.startswith("//"):
            raise CloudProviderError(
                f"Refusing to request {path!r} from {self.provider}",
                details="Only paths on the provider's API host are requested.",
            )
        if verb not in READ_METHODS and isinstance(get_runner(), DryRunRunner):
            raise CloudProviderError(
                f"A dry run does not change anything at {self.provider}",
                details=f"{verb} {path} was not sent.",
            )
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        data = json.dumps(body).encode() if body is not None else None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
            "User-Agent": f"noust/{__version__}",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"

        attempt = 0
        while True:
            attempt += 1
            request = urllib.request.Request(url, data=data, method=verb, headers=headers)
            try:
                with self._opener(request, timeout=self.timeout) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                break
            except HTTPError as exc:
                error = self._error(verb, path, exc)
                if attempt < MAX_ATTEMPTS and self._worth_retrying(verb, error):
                    self._sleep(self._wait(error, attempt))
                    continue
                raise error from None
            except (URLError, TimeoutError, HTTPException, OSError) as exc:
                reason = self.scrub(str(getattr(exc, "reason", None) or exc))
                if verb in READ_METHODS and attempt < MAX_ATTEMPTS:
                    self._sleep(attempt)
                    continue
                raise CloudProviderError(
                    f"{self.provider} could not be reached",
                    details=(
                        "Check this server's outbound HTTPS access."
                        if verb in READ_METHODS
                        else "The request may have reached the provider before the connection "
                        "failed: look in its console for a machine before you try again, so "
                        "that two are not created."
                    ),
                    output=reason,
                ) from None

        if len(raw) > MAX_RESPONSE_BYTES:
            raise CloudProviderError(
                f"{self.provider}'s answer to {verb} {path} is too large",
                details=f"More than {MAX_RESPONSE_BYTES} bytes.",
            )
        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CloudProviderError(
                f"{self.provider}'s answer to {verb} {path} is not JSON", details=str(exc)
            ) from None

    def _worth_retrying(self, verb: str, error: CloudProviderError) -> bool:
        """
        Decide whether a failed request may be sent again.

        Args:
            verb: The request's method.
            error: What failed.

        Returns:
            True for a rate limit (the provider did nothing) and, for a read only,
            a server error.
        """
        if error.status == 429:
            return True
        return verb in READ_METHODS and error.status is not None and error.status >= 500

    def _wait(self, error: CloudProviderError, attempt: int) -> float:
        """
        Say how long to wait before the next attempt.

        Args:
            error: What failed.
            attempt: The attempt that failed, from 1.

        Returns:
            Seconds: the provider's own request when it made one, else a short
            backoff; never more than :data:`MAX_RATE_WAIT`.
        """
        if error.retry_after is not None:
            return float(min(max(error.retry_after, 0), MAX_RATE_WAIT))
        return float(min(attempt, MAX_RATE_WAIT))

    def _error(self, verb: str, path: str, exc: HTTPError) -> CloudProviderError:
        """
        Turn an error status into the error Noust raises, the provider's words kept.

        Args:
            verb: The request's method.
            path: The request's path.
            exc: urllib's error, carrying the response.

        Returns:
            The error to raise.
        """
        message = ""
        try:
            raw = exc.read(64 * 1024)
            payload = json.loads(raw) if raw else {}
            message = self._message(payload)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            message = ""
        headers = exc.headers
        retry_after: int | None = None
        if exc.code == 429 and headers is not None:
            retry_after = self._retry_after(
                headers.get("Retry-After"), headers.get("RateLimit-Reset")
            )
        return CloudProviderError(
            f"{self.provider} refused {verb} {path} ({exc.code})",
            details=_hint(self.provider, exc.code),
            output=self.scrub(message or str(exc.reason)),
            status=exc.code,
            retry_after=retry_after,
        )

    def _retry_after(self, after: str | None, reset: str | None) -> int | None:
        """
        Read how long a rate limit asks to be waited out.

        Args:
            after: The ``Retry-After`` header (seconds).
            reset: The ``RateLimit-Reset`` header (epoch seconds, or seconds).

        Returns:
            Seconds, or None when neither header says.
        """
        if after is not None and after.strip().isdigit():
            return int(after)
        if reset is not None and reset.strip().isdigit():
            value = int(reset)
            # Some providers send the moment, others the wait.
            return max(0, int(value - self._clock())) if value > 10**9 else value
        return None
