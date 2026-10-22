# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The console's own API, used by code that runs inside the console, as whoever asked.

A manifest is planned and applied by the same engine the command line runs
(:mod:`noust.manifest`), which is a client of the API: every read and every
change is a request. Run inside the console, those requests go back to the very
application that is serving, carrying the credentials of the person who asked
(:func:`noust.web.mcp.gateway.call_api`), so they cross the whole stack - the
token or session check, the permission each route declares, the team scope,
sudo mode, the approvals and the audit trail. What the engine may do is exactly
what its caller may do over HTTP, and cannot be more.

:class:`ForwardedCaller` is what is kept of a request so a worker thread can go
on making calls as its author after the request has been answered. It lives in
memory only, for the length of one job: it is never written to the store, a log
or a job record.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request

from noust.client.api import refusal
from noust.web.auth import CSRF_HEADER_NAME, bearer_token, get_client_ip, is_secure_request
from noust.web.mcp.gateway import call_api


@dataclass(frozen=True)
class ForwardedCaller:
    """
    The credentials of a request, held to make more requests as its author.

    Attributes:
        app: The ASGI application that serves, which is called back.
        loop: The event loop the application runs on, which a worker thread
            hands each call back to.
        token: The caller's bearer token, passed on as received; None for a
            signed-in browser session.
        cookie: The session's ``Cookie`` header, for a browser session.
        csrf: The session's CSRF token, which a browser session needs for
            every change.
        client_host: The caller's address as the security layer resolved it,
            so a token restricted to a network is held to it again.
        host: The ``Host`` the caller asked for, so the allowed-hosts check
            sees the same one.
        scheme: ``http`` or ``https``, as the caller reached the console.
    """

    app: Any = field(repr=False)
    loop: asyncio.AbstractEventLoop = field(repr=False)
    token: str | None = field(default=None, repr=False)
    cookie: str | None = field(default=None, repr=False)
    csrf: str | None = field(default=None, repr=False)
    client_host: str = "unknown"
    host: str = "localhost"
    scheme: str = "https"

    @classmethod
    def of(cls, request: Request, loop: asyncio.AbstractEventLoop) -> ForwardedCaller:
        """
        Keep what a request needs to be answered again as its author.

        Args:
            request: The request being served.
            loop: The event loop it is served on.

        Returns:
            The caller; its credentials are the request's, nothing more.
        """
        token = bearer_token(request)
        return cls(
            app=request.app,
            loop=loop,
            token=token,
            cookie=None if token else request.headers.get("cookie"),
            csrf=None if token else request.headers.get(CSRF_HEADER_NAME),
            client_host=get_client_ip(request),
            host=request.headers.get("host") or "localhost",
            scheme="https" if is_secure_request(request) else "http",
        )


class GatewayTransport:
    """
    The server's own API as the :class:`~noust.manifest.plan.Transport` of the manifest engine.

    It is called from a worker thread (a threadpool for a preview, a job for an
    apply): each call is handed to the event loop the application runs on and
    waited for.

    Args:
        caller: Whose credentials the calls carry.
        writable: Whether a change may be made. A preview only reads and a
            write attempted through it is a defect, so it raises.
    """

    #: The longest one call of the API may take.
    TIMEOUT = 60.0

    def __init__(self, caller: ForwardedCaller, *, writable: bool = False) -> None:
        self._caller = caller
        self._writable = writable

    def _call(self, method: str, path: str, body: Any = None, **query: Any) -> Any:
        """
        Args:
            method: The HTTP method.
            path: A route below ``/api``.
            body: A JSON body, or None.
            **query: Query parameters.

        Returns:
            The decoded answer.

        Raises:
            RuntimeError: A change through a transport that only reads.
            RemoteError: When the route refuses the call.
        """
        if method != "GET" and not self._writable:
            raise RuntimeError("a preview reads and writes nothing")
        caller = self._caller
        pending = asyncio.run_coroutine_threadsafe(
            call_api(
                caller.app,
                method,
                "/api" + (path if path.startswith("/") else "/" + path),
                query=query,
                body=body,
                token=caller.token,
                cookie=caller.cookie,
                csrf=caller.csrf,
                client_host=caller.client_host,
                host=caller.host,
                scheme=caller.scheme,
            ),
            caller.loop,
        )
        response = pending.result(timeout=self.TIMEOUT)
        decoded = response.json()
        if response.status >= 400:
            raise refusal(response.status, decoded)
        return decoded

    def get(self, path: str, **query: Any) -> Any:
        """
        Args:
            path: A route.
            **query: Query parameters.

        Returns:
            The decoded body.
        """
        return self._call("GET", path, **query)

    def post(self, path: str, body: Any = None, **query: Any) -> Any:
        """
        Args:
            path: A route.
            body: The JSON body.
            **query: Query parameters.

        Returns:
            The decoded body.

        Raises:
            RuntimeError: When this transport only reads.
        """
        return self._call("POST", path, body, **query)

    def put(self, path: str, body: Any) -> Any:
        """
        Args:
            path: A route.
            body: The JSON body.

        Returns:
            The decoded body.

        Raises:
            RuntimeError: When this transport only reads.
        """
        return self._call("PUT", path, body)

    def patch(self, path: str, body: Any) -> Any:
        """
        Args:
            path: A route.
            body: The JSON body.

        Returns:
            The decoded body.

        Raises:
            RuntimeError: When this transport only reads.
        """
        return self._call("PATCH", path, body)
