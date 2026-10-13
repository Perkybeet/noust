# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The API's version, its ``/api/v1`` address and its deprecation policy.

The API has one version, ``1``, and two addresses for it: ``/api/...``, which
every client until now uses (the console, the fleet, scripts), and
``/api/v1/...``, which a client that wants a promise should use. They are not
two sets of routes. :class:`ApiVersionMiddleware` is the outermost layer and
removes the version from the path before anything else looks at it, so the
router, the permission map, the rate limiter, the audit trail and the node
proxy all judge the canonical ``/api/...`` path and cannot disagree about a
route that has two names (rule 3: one implementation, one place a route's
rules are looked up).

A client may rely on what ``docs/api.md`` calls the compatibility promise:
within version 1 an operation is added, never removed or changed to need more.
:func:`deprecated` is how an operation announces that it is going away, and the
only way: it sets ``deprecated: true`` in the schema, and makes every answer of
the route carry ``Deprecation`` (RFC 9745), ``Sunset`` (RFC 8594) and a ``Link``
to the successor, so a client that never reads the schema still hears of it.

Every answer under ``/api`` carries ``X-Noust-API-Version``. That includes the
refusals made before routing (a wrong credential, a blocked address, a rate
limit), which is why this is a middleware and not a dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from email.utils import format_datetime
from typing import Any

from fastapi import Depends, Request
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: The API's version. It changes only when a client written against the
#: previous one would break, and then the old one is served beside it, never
#: replaced: ``/api/v2`` would appear next to ``/api/v1``.
API_VERSION = "1"

#: The header every answer under ``/api`` carries.
API_VERSION_HEADER = "X-Noust-API-Version"

#: Where the API lives, version aside.
API_PREFIX = "/api"

#: The address that names the version.
VERSIONED_PREFIX = f"{API_PREFIX}/v{API_VERSION}"

#: The shortest notice a deprecated operation gets between the day it is
#: announced and the day it may stop answering. Enforced when a route is
#: declared (:func:`deprecated`), so a shorter one cannot reach a release.
MINIMUM_NOTICE_DAYS = 180

#: The key under which a deprecated route leaves its notice in the ASGI scope
#: for the middleware, which writes the headers whatever the route answers.
DEPRECATION_SCOPE_KEY = "noust.deprecation"

#: The operation extension that carries the notice in the schema.
DEPRECATION_EXTENSION = "x-noust-deprecation"

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def canonical_api_path(path: str) -> str:
    """
    Name a request path the way the router knows it.

    Args:
        path: A request path, decoded.

    Returns:
        The path without the version segment when it starts with
        ``/api/v1`` (``/api/v1/apps`` is ``/api/apps``), otherwise unchanged.
        One pass only: what follows the prefix is the router's to judge, and
        ``/api/v1/v1/apps`` is a route that does not exist, as it should be.
    """
    if path == VERSIONED_PREFIX:
        return API_PREFIX
    if path.startswith(VERSIONED_PREFIX + "/"):
        return API_PREFIX + path[len(VERSIONED_PREFIX) :]
    return path


def versioned_api_path(path: str) -> str:
    """
    Give the versioned address of an API path.

    Args:
        path: A canonical API path or path template, ``/api/...``.

    Returns:
        The same path under ``/api/v1``.

    Raises:
        ValueError: When the path is not under ``/api``.
    """
    if path != API_PREFIX and not path.startswith(API_PREFIX + "/"):
        raise ValueError(f"{path!r} is not an API path: expected /api or /api/...")
    return VERSIONED_PREFIX + path[len(API_PREFIX) :]


def is_api_path(path: str) -> bool:
    """
    Args:
        path: A canonical request path.

    Returns:
        True for ``/api`` and everything below it, matched by whole segment.
    """
    return path == API_PREFIX or path.startswith(API_PREFIX + "/")


def _day(value: str, what: str) -> date:
    """
    Read a calendar day written ``YYYY-MM-DD``.

    Args:
        value: The text.
        what: Which argument it came from, for the error.

    Returns:
        The day.

    Raises:
        ValueError: When it is not exactly that shape, or not a real day.
    """
    if not _ISO_DAY.fullmatch(value):
        raise ValueError(f"{what} must be a calendar day written YYYY-MM-DD, got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{what} must be a calendar day written YYYY-MM-DD: {exc}") from exc


@dataclass(frozen=True)
class Deprecation:
    """
    The notice a deprecated operation gives.

    Attributes:
        since: The day it was announced; its ``Deprecation`` header.
        sunset: The earliest day it may stop answering; its ``Sunset`` header.
            None while no day has been committed to.
        successor: The canonical path (``/api/...``) that replaces it.
    """

    since: date
    sunset: date | None = None
    successor: str | None = None

    @classmethod
    def declare(
        cls, since: str, sunset: str | None = None, successor: str | None = None
    ) -> Deprecation:
        """
        Read and check a notice.

        Args:
            since: The day it was announced, ``YYYY-MM-DD``.
            sunset: The earliest day it may be removed, ``YYYY-MM-DD``; at
                least :data:`MINIMUM_NOTICE_DAYS` after ``since``.
            successor: The canonical path that replaces it, ``/api/...``.

        Returns:
            The notice.

        Raises:
            ValueError: When a day is malformed, the notice is shorter than
                the policy allows, or the successor is not a canonical API
                path (a path under ``/api/v1`` is the same route spelt twice).
        """
        announced = _day(since, "since")
        removal = _day(sunset, "sunset") if sunset is not None else None
        if removal is not None and removal < announced + timedelta(days=MINIMUM_NOTICE_DAYS):
            raise ValueError(
                f"sunset {removal} is less than {MINIMUM_NOTICE_DAYS} days after since "
                f"{announced}: an operation is announced at least {MINIMUM_NOTICE_DAYS} days "
                "before it may stop answering (docs/api.md, Deprecation)"
            )
        if successor is not None and (
            not successor.startswith(API_PREFIX + "/")
            or successor == VERSIONED_PREFIX
            or successor.startswith(VERSIONED_PREFIX + "/")
        ):
            raise ValueError(
                f"successor must be a canonical API path such as /api/apps, got {successor!r}"
            )
        return cls(announced, removal, successor)

    def extension(self) -> dict[str, str]:
        """
        Returns:
            The notice as the operation's ``x-noust-deprecation`` object.
        """
        notice = {"since": self.since.isoformat()}
        if self.sunset is not None:
            notice["sunset"] = self.sunset.isoformat()
        if self.successor is not None:
            notice["successor"] = self.successor
        return notice

    def apply(self, headers: MutableHeaders) -> None:
        """
        Write the notice into a response's headers.

        Args:
            headers: The response's headers. ``Deprecation`` and ``Sunset``
                replace any a handler set; ``Link`` is added beside others.
        """
        midnight = datetime.combine(self.since, time.min, tzinfo=timezone.utc)
        # RFC 9745: a structured-field date is an integer with a leading "@".
        headers["Deprecation"] = f"@{int(midnight.timestamp())}"
        if self.sunset is not None:
            end = datetime.combine(self.sunset, time.min, tzinfo=timezone.utc)
            headers["Sunset"] = format_datetime(end, usegmt=True)
        if self.successor is not None:
            headers.append(
                "Link", f'<{versioned_api_path(self.successor)}>; rel="successor-version"'
            )


def deprecated(
    since: str, *, sunset: str | None = None, successor: str | None = None
) -> dict[str, Any]:
    """
    Announce that a route is going away.

    Unpack the result into the route's decorator::

        @router.get("/old", **deprecated(since="2026-10-01", sunset="2027-06-01",
                                         successor="/api/new"))

    The schema marks the operation ``deprecated: true`` with an
    ``x-noust-deprecation`` object, and every answer of the route, a refusal
    inside it included, carries ``Deprecation``, ``Sunset`` and ``Link``. The
    route must not also pass ``dependencies``, ``deprecated`` or
    ``openapi_extra``; merge them by hand if it needs them.

    Args:
        since: The day it was announced, ``YYYY-MM-DD``.
        sunset: The earliest day it may be removed, ``YYYY-MM-DD``; at least
            :data:`MINIMUM_NOTICE_DAYS` after ``since``.
        successor: The canonical path that replaces it.

    Returns:
        Keyword arguments for ``APIRouter.get`` and its siblings.

    Raises:
        ValueError: As :meth:`Deprecation.declare`; raised when the route is
            declared, which is when the application is imported.
    """
    notice = Deprecation.declare(since, sunset, successor)

    async def announce(request: Request) -> None:
        """Leave the notice where the middleware writes it from."""
        request.scope[DEPRECATION_SCOPE_KEY] = notice

    return {
        "deprecated": True,
        "dependencies": [Depends(announce)],
        "openapi_extra": {DEPRECATION_EXTENSION: notice.extension()},
    }


class ApiVersionMiddleware:
    """
    Serve ``/api/v1`` as ``/api`` and stamp every API answer with the version.

    Added last in :func:`noust.web.server.create_app`, so it is the outermost
    layer: the security middleware, the router and every handler see the
    canonical path, and a refusal the security middleware makes still carries
    the header.
    """

    def __init__(self, app: ASGIApp) -> None:
        """
        Args:
            app: The application to wrap.
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """
        Rewrite the path, then add the headers to the response.

        Args:
            scope: The ASGI connection scope.
            receive: ASGI receive channel.
            send: ASGI send channel.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        canonical = canonical_api_path(path)
        if canonical != path:
            # A copy, as ASGI asks of a middleware that changes the scope; the
            # layers below and the closure below share this one.
            scope = {**scope, "path": canonical}
            raw_path = scope.get("raw_path")
            if isinstance(raw_path, bytes):
                scope["raw_path"] = canonical_api_path(raw_path.decode("latin-1")).encode("latin-1")
        if not is_api_path(canonical):
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers[API_VERSION_HEADER] = API_VERSION
                notice = scope.get(DEPRECATION_SCOPE_KEY)
                if isinstance(notice, Deprecation):
                    notice.apply(headers)
            await send(message)

        await self.app(scope, receive, send_with_headers)
