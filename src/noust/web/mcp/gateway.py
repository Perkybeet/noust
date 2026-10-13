# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Calling the console's own API from inside the console, as the caller.

The MCP server is a client of the API (rule 3 of the 3.4 design): it
re-implements nothing a route does. A tool call becomes an ordinary request to
the very application that is serving it, carrying the caller's own bearer token,
its address and the host it asked for, and travelling the whole stack -
the security middleware, the token check, the permission the route declares, the
team scope, the approvals, sudo mode, the audit trail. So what a tool may do is
exactly what its token may do over HTTP, and cannot be more.

No network and no HTTP library are involved: the request is handed to the ASGI
application directly and its answer collected.
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: Largest answer collected from the application, so a tool cannot be made to
#: hold an unbounded body in memory.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class GatewayResponse:
    """
    What the API answered a tool's request.

    Attributes:
        status: The HTTP status.
        headers: The response headers, lower-cased names.
        body: The raw body.
    """

    status: int
    headers: Mapping[str, str]
    body: bytes

    def json(self) -> Any:
        """
        Returns:
            The body decoded as JSON, or its text when it is not JSON.
        """
        text = self.body.decode("utf-8", errors="replace")
        if not text.strip():
            return None
        try:
            return json.loads(text)
        except ValueError:
            return text


async def call_api(
    app: Any,
    method: str,
    path: str,
    *,
    query: Mapping[str, Any] | None = None,
    body: Any = None,
    token: str,
    client_host: str,
    host: str,
    scheme: str = "https",
) -> GatewayResponse:
    """
    Make one request of the application, as a caller holding ``token``.

    Args:
        app: The ASGI application that is serving.
        method: The HTTP method.
        path: The route, from ``/api`` on, parameters already in place.
        query: Query parameters; ``None`` values are left out.
        body: A JSON-serialisable body, or None.
        token: The caller's bearer token, passed on exactly as received.
        client_host: The caller's address as the security layer resolved it, so
            a token restricted to a network is held to it again.
        host: The ``Host`` the caller asked for, so the allowed-hosts check
            sees the same one.
        scheme: ``http`` or ``https``, as the caller reached the console.

    Returns:
        The API's answer.
    """
    pairs = [
        (key, "true" if value is True else "false" if value is False else str(value))
        for key, value in (query or {}).items()
        if value is not None
    ]
    payload = json.dumps(body).encode("utf-8") if body is not None else b""
    headers = [
        (b"host", host.encode("latin-1")),
        (b"authorization", f"Bearer {token}".encode("latin-1")),
        (b"accept", b"application/json"),
        (b"user-agent", b"noust-mcp"),
    ]
    if body is not None:
        headers.append((b"content-type", b"application/json"))
        headers.append((b"content-length", str(len(payload)).encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": scheme,
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": urllib.parse.urlencode(pairs).encode(),
        "headers": headers,
        "client": (client_host, 0),
        "server": (host.partition(":")[0], 443 if scheme == "https" else 80),
    }
    sent = False
    status = 500
    response_headers: dict[str, str] = {}
    chunks: list[bytes] = []
    received = 0

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        nonlocal status, received
        if message["type"] == "http.response.start":
            status = int(message["status"])
            for key, value in message.get("headers", []):
                response_headers[key.decode("latin-1").lower()] = value.decode("latin-1")
        elif message["type"] == "http.response.body":
            part = message.get("body", b"")
            received += len(part)
            if received <= MAX_RESPONSE_BYTES:
                chunks.append(part)

    await app(scope, receive, send)
    return GatewayResponse(status=status, headers=response_headers, body=b"".join(chunks))
