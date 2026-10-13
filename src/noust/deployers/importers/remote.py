# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Another platform's API, read with a token, for the importers that have no file to read.

Coolify and Dokploy keep an application's configuration in their own database,
not in the repository. What they offer instead is an API an operator can give a
read-only token to. This is the one way Noust talks to those APIs: a small JSON
client over :mod:`urllib` (no dependency), HTTPS only (a token must not cross
a network in the clear), behind the one SSRF guard
(:func:`noust.core.notifier.require_public_destination`: an address that
resolves inside this machine's networks is refused unless the operator listed
it), never following a redirect (a token sent to wherever a response points is
a leaked token), with a timeout and a size limit on every call.

The token is read here and used for the length of the command: it is never
written to disk, to the store or to a log.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable, Mapping
from typing import IO, Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit

from noust import __version__
from noust.core import notifier
from noust.core.exceptions import IntegrationError, ValidationError

#: Seconds before a call is abandoned.
REQUEST_TIMEOUT = 20

#: Bytes read from one answer at most; an application list is kilobytes.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

#: Anything with urlopen's calling convention.
Opener = Callable[..., IO[bytes]]

_LOOPBACK = ("localhost", "127.0.0.1", "::1")


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Refuses to follow a redirect: the token would go with it."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str):
        raise HTTPError(req.full_url, code, f"redirected to {newurl}", headers, fp)


def check_base_url(url: str, platform: str) -> str:
    """
    Check the address of another platform's server.

    Args:
        url: What the operator gave, such as ``https://coolify.example.com``.
        platform: Its name, for the messages.

    Returns:
        The address without a trailing slash, and without a path.

    Raises:
        ValidationError: Not an address, plain http (except on loopback), or
            carrying credentials.
    """
    text = (url or "").strip().rstrip("/")
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValidationError(
            f"{url!r} is not the address of a {platform} server",
            details=f"Give it as https://{platform}.example.com.",
            field="url",
        )
    if parts.username or parts.password:
        raise ValidationError(
            "The address must not carry credentials",
            details="Give the token separately: it is read from a file or the environment.",
            field="url",
        )
    if parts.scheme == "http" and parts.hostname not in _LOOPBACK:
        raise ValidationError(
            f"{platform} is reached over plain http at {parts.hostname}",
            details="The token would cross the network in the clear. Use its https address.",
            field="url",
        )
    return f"{parts.scheme}://{parts.netloc}{parts.path}".rstrip("/")


class RemoteClient:
    """
    JSON over HTTPS to one platform's API, with a token.

    Args:
        base_url: The platform's address.
        header: The header that carries the token, ``(name, value)``.
        platform: Its name, for the messages.
        timeout: Seconds per call.
        opener: Replacement for :func:`urllib.request.urlopen` (tests).
        hints: What to tell the operator for an error status, by status, over the
            importers' own words (a code host has other tokens to create).
    """

    def __init__(
        self,
        base_url: str,
        header: tuple[str, str] | None,
        *,
        platform: str,
        timeout: float = REQUEST_TIMEOUT,
        opener: Opener | None = None,
        hints: Mapping[int, str] | None = None,
    ) -> None:
        self.base_url = check_base_url(base_url, platform)
        self._header = header
        self._hints = dict(hints or {})
        self.platform = platform
        self.timeout = timeout
        self._opener: Opener = opener or urllib.request.build_opener(_NoRedirects).open
        self._guarded = opener is None

    def get(self, path: str, query: Mapping[str, Any] | None = None) -> Any:
        """
        Read one resource.

        Args:
            path: Path on the platform's server, starting with ``/``.
            query: Query parameters.

        Returns:
            The decoded JSON.

        Raises:
            IntegrationError: The server cannot be reached, refuses the token,
                answers an error, or answers something that is not JSON.
        """
        return self._request("GET", path, query, None)

    def post_form(self, path: str, form: Mapping[str, Any]) -> Any:
        """
        Send a form, as an OAuth token endpoint takes one.

        Args:
            path: Path on the platform's server, starting with ``/``.
            form: The fields, sent ``application/x-www-form-urlencoded``.

        Returns:
            The decoded JSON.

        Raises:
            IntegrationError: As :meth:`get`.
        """
        return self._request("POST", path, None, form)

    def _request(
        self,
        method: str,
        path: str,
        query: Mapping[str, Any] | None,
        form: Mapping[str, Any] | None,
    ) -> Any:
        """
        Make one guarded request.

        Args:
            method: ``GET`` or ``POST``.
            path: Path on the platform's server.
            query: Query parameters.
            form: Form fields of a POST.

        Returns:
            The decoded JSON.

        Raises:
            IntegrationError: As :meth:`get`.
        """
        if not path.startswith("/") or path.startswith("//"):
            raise IntegrationError(f"Refusing to request {path!r} from {self.platform}")
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        if self._guarded:
            try:
                notifier.require_public_destination(url, f"{self.platform} server")
            except ValueError as exc:
                raise IntegrationError(
                    str(exc),
                    details="A server on this machine's own network is only contacted when it "
                    "is listed: noust config set notifications.allow_private_hosts HOST",
                ) from None
        headers = {"Accept": "application/json", "User-Agent": f"noust/{__version__}"}
        if self._header is not None:
            headers[self._header[0]] = self._header[1]
        body = None
        if form is not None:
            body = urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            raise self._refusal(method, path, exc) from None
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None) or exc
            raise IntegrationError(
                f"{self.platform} could not be reached at {self.base_url}",
                details="Check the address and that this server can reach it over HTTPS.",
                output=str(reason),
            ) from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise IntegrationError(f"{self.platform}'s answer to {path} is too large")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise IntegrationError(
                f"{self.platform}'s answer to {path} is not JSON",
                details=f"Is {self.base_url} really a {self.platform} server's address?",
            ) from None

    def _refusal(self, method: str, path: str, exc: HTTPError) -> IntegrationError:
        """
        Turn an error status into the error Noust raises.

        Args:
            method: The method used.
            path: The path asked.
            exc: urllib's error.

        Returns:
            The error, with what to do about the usual causes.
        """
        hints = {
            401: "The token was not accepted. Create an API token with read access "
            f"in {self.platform} and give it again.",
            403: "The token may not read this. It needs read access to applications "
            "(and, to bring variables over, to sensitive values).",
            404: f"{self.platform} has no such resource; this may be an older or newer version.",
        }
        hints.update(self._hints)
        if 300 <= exc.code < 400:
            hint = f"The server redirected the request ({exc.reason}). Use its final https address."
        else:
            hint = hints.get(exc.code, "Retry in a moment.")
        return IntegrationError(
            f"{self.platform} refused {method} {path} ({exc.code})", details=hint
        )


def read_token(value: str | None, *, source: str) -> str:
    """
    Check a token before it is used.

    Args:
        value: The token as read from its file or the environment.
        source: Where it came from, for the message.

    Returns:
        The token.

    Raises:
        ValidationError: It is empty or holds a character a header cannot.
    """
    token = (value or "").strip()
    if not token or any(ord(char) < 33 or ord(char) > 126 for char in token):
        raise ValidationError(
            f"The token from {source} is empty or has a space or control character",
            details="Give the API token alone, with nothing around it.",
            field="token",
        )
    return token
