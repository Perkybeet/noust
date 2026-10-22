# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one way Noust talks to a DNS provider's REST API, for every provider but Cloudflare.

A small client over :mod:`urllib` like :class:`~noust.managers.dns.cloudflare.CloudflareClient`:
no new dependency, a fixed host named by the provider's module, a timeout on every request, a
bound on what is read, and one error for every way a request can fail with the provider's own
words kept verbatim in the error's ``output``. What differs from one provider to the next
(the authentication header, the shape of an error, the pagination) is the provider's, passed in;
what is the same (the transport, the bounds, the refusal of a path that is not a path) is here.

The credential is held for the life of the client and never reaches an error, a log line or an
audit event: the error carries what the provider answered, not what was sent.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable, Mapping
from typing import IO, Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit

from noust import __version__
from noust.managers.dns.provider import DnsProviderError

#: Seconds before a request is abandoned.
REQUEST_TIMEOUT = 15

#: Bytes of a response read at most.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024

#: Anything with urlopen's calling convention.
Opener = Callable[..., IO[bytes]]

#: Turns the provider's error body into the words shown.
Explain = Callable[[bytes], str]

#: Turns an error status into the hint shown above the provider's own words.
Hint = Callable[[int], str]


def default_hint(label: str) -> Hint:
    """
    Args:
        label: The provider's name.

    Returns:
        A hint for the statuses every provider uses the same way.
    """

    def hint(status: int) -> str:
        if status in (401, 403):
            return (
                f"{label} did not accept this credential for that. Check what it is allowed to "
                "do (it needs to read the zones and edit their records) and add the account again."
            )
        if status == 404:
            return (
                f"{label} does not show this to the credential: the zone or record may not exist."
            )
        if status == 429:
            return f"{label} is rate limiting this credential; try again in a minute."
        if status == 400 or status == 422:
            return f"{label} refused the request as invalid."
        return f"Retry in a moment; if it persists, check {label}'s status page."

    return hint


def json_words(raw: bytes) -> str:
    """
    Read the usual shapes of a JSON error body into one verbatim line.

    Args:
        raw: The body.

    Returns:
        The provider's ``message``, ``error`` or ``errors`` text; empty when it has none.
    """
    try:
        payload = json.loads(raw or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return raw.decode("utf-8", "replace").strip()[:300]
    if not isinstance(payload, dict):
        return ""
    parts: list[str] = []
    for key in ("message", "error", "errors", "detail", "details", "cause"):
        value = payload.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            parts.append(str(value.get("message") or value.get("code") or value))
        elif isinstance(value, list):
            parts.extend(
                str(item.get("message") or item) if isinstance(item, dict) else str(item)
                for item in value
            )
    return "; ".join(part for part in parts if part)


class RestClient:
    """
    JSON over HTTPS to one provider, with one credential.

    Args:
        label: The provider's name, for messages.
        base_url: The API root; a fixed ``https`` address of the provider.
        headers: The authentication headers, sent with every request.
        opener: Replacement for :func:`urllib.request.urlopen` (tests).
        timeout: Seconds per request.
        explain: Reads the provider's error body.
        hint: Says what an error status usually means.
    """

    def __init__(
        self,
        label: str,
        base_url: str,
        headers: Mapping[str, str],
        *,
        opener: Opener | None = None,
        timeout: float = REQUEST_TIMEOUT,
        explain: Explain = json_words,
        hint: Hint | None = None,
    ) -> None:
        if urlsplit(base_url).scheme != "https":
            raise ValueError("A provider's API is reached over https only")
        self.label = label
        self.base_url = base_url.rstrip("/")
        self._headers = dict(headers)
        self._opener: Opener = opener or urllib.request.urlopen
        self.timeout = timeout
        self._explain = explain
        self._hint = hint or default_hint(label)

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        raw_body: bytes | None = None,
    ) -> bytes:
        """
        Send one request and return the body of the answer.

        Args:
            method: HTTP method.
            path: Path on the API, starting with ``/``.
            body: JSON body, or None.
            query: Query string parameters.
            headers: More headers for this request (a signature).
            raw_body: A body that is not JSON (an XML document), sent as it is.

        Returns:
            What the provider answered; empty for an answer with no body.

        Raises:
            DnsProviderError: The path is not a path, the provider could not be reached, or it
                refused the request.
        """
        if not path.startswith("/") or path.startswith("//") or "://" in path:
            raise DnsProviderError(f"Refusing to request {path!r} from {self.label}")
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        data = raw_body if raw_body is not None else None
        if body is not None:
            data = json.dumps(body).encode()
        merged = {
            **self._headers,
            "Accept": "application/json",
            "User-Agent": f"noust/{__version__}",
            **(headers or {}),
        }
        if body is not None:
            merged["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method.upper(), headers=merged)
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            words = ""
            try:
                words = self._explain(exc.read(64 * 1024) or b"")
            except OSError:
                words = ""
            raise DnsProviderError(
                f"{self.label} refused {method.upper()} {path} ({exc.code})",
                status=exc.code,
                details=self._hint(exc.code),
                output=words or str(exc.reason),
            ) from None
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None) or exc
            raise DnsProviderError(
                f"{self.label} could not be reached",
                details=f"Check this server's outbound HTTPS access to {urlsplit(self.base_url).hostname}.",
                output=str(reason),
            ) from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise DnsProviderError(f"{self.label}'s answer to {method.upper()} {path} is too large")
        return raw

    def json(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
    ) -> Any:
        """
        Send a request and decode a JSON answer.

        Args:
            method: HTTP method.
            path: Path on the API.
            body: JSON body, or None.
            query: Query string parameters.

        Returns:
            The decoded answer; None for an answer with no body.

        Raises:
            DnsProviderError: As :meth:`request`, or the answer is not JSON.
        """
        raw = self.request(method, path, body=body, query=query)
        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise DnsProviderError(
                f"{self.label}'s answer to {method.upper()} {path} is not JSON", details=str(exc)
            ) from None
