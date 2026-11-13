# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The console's own API, called by the assistant's loop as the assistant (item 171).

The loop runs in a thread of its own and makes its calls the way the MCP endpoint and a manifest
applied from the console do (:mod:`noust.web.mcp.gateway`): an ordinary request handed to the
application that serves, which crosses the whole stack (the token check, the permission each route
declares, the team scope, the approval policy of who calls, the audit trail). What the assistant may
do is exactly what its token may do over HTTP, and cannot be more.

Unlike :class:`noust.web.gateway_client.ForwardedCaller` it holds **no credential of the person**:
not their cookie, not their token. The token of the conversation is given with each call, and the
only thing kept of the request that began the turn is where it came from, so that a token limited to
a network is held to it again and the allowed-hosts check sees the same host.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from fastapi import Request

from noust.web.auth import get_client_ip, is_secure_request
from noust.web.mcp.gateway import GatewayResponse, call_api

__all__ = ["ApiBridge", "LoopBridge"]

#: The longest one call of the API may take.
CALL_TIMEOUT = 120.0


class ApiBridge(Protocol):
    """What the loop needs of the API: one call, as the token's owner."""

    def call(
        self,
        method: str,
        path: str,
        *,
        token: str,
        query: Sequence[tuple[str, str]] = (),
        body: Any = None,
        request_id: str | None = None,
    ) -> GatewayResponse:
        """
        Make one call.

        Args:
            method: The HTTP method.
            path: The route, from ``/api`` on, parameters in place.
            token: The assistant's token for the conversation.
            query: The query pairs.
            body: A JSON body, or None.
            request_id: What the audit trail joins what the call records by.

        Returns:
            What the API answered.
        """
        ...


@dataclass(frozen=True)
class LoopBridge:
    """
    The application that serves, called from a worker thread through its event loop.

    Attributes:
        app: The ASGI application.
        loop: The event loop it runs on, which each call is handed back to.
        client_host: Where the request that began the turn came from.
        host: The ``Host`` it asked for.
        scheme: ``http`` or ``https``.
    """

    app: Any = field(repr=False)
    loop: asyncio.AbstractEventLoop = field(repr=False)
    client_host: str = "unknown"
    host: str = "localhost"
    scheme: str = "https"

    @classmethod
    def of(cls, request: Request, loop: asyncio.AbstractEventLoop) -> LoopBridge:
        """
        Keep where a request came from, and nothing else of it.

        Args:
            request: The request that begins a turn.
            loop: The event loop it is served on.

        Returns:
            The bridge.
        """
        return cls(
            app=request.app,
            loop=loop,
            client_host=get_client_ip(request),
            host=request.headers.get("host") or "localhost",
            scheme="https" if is_secure_request(request) else "http",
        )

    def call(
        self,
        method: str,
        path: str,
        *,
        token: str,
        query: Sequence[tuple[str, str]] = (),
        body: Any = None,
        request_id: str | None = None,
    ) -> GatewayResponse:
        """
        Make one call, handed to the application's event loop and waited for.

        Args:
            method: The HTTP method.
            path: The route, from ``/api`` on.
            token: The assistant's token.
            query: The query pairs.
            body: A JSON body, or None.
            request_id: The correlation identifier of the call.

        Returns:
            What the API answered.
        """
        pending = asyncio.run_coroutine_threadsafe(
            call_api(
                self.app,
                method,
                path,
                query=_mapping(query),
                body=body,
                token=token,
                client_host=self.client_host,
                host=self.host,
                scheme=self.scheme,
                correlation_id=request_id,
                user_agent="noust-assistant",
            ),
            self.loop,
        )
        return pending.result(timeout=CALL_TIMEOUT)


def _mapping(pairs: Sequence[tuple[str, str]]) -> Mapping[str, str]:
    """
    Args:
        pairs: Query pairs.

    Returns:
        Them as the gateway takes them.
    """
    return dict(pairs)
