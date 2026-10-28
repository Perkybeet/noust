# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_web_websockets.py`` that other tests build on."""

from __future__ import annotations

from starlette.websockets import WebSocketDisconnect

from noust.web.websockets.router import WS_SUBPROTOCOL, WS_TOKEN_PREFIX


def token_subprotocols(token: str) -> list[str]:
    """
    Build the subprotocol list that carries a credential.

    Args:
        token: The credential to present.

    Returns:
        The value of ``Sec-WebSocket-Protocol``, split into entries.
    """
    return [WS_SUBPROTOCOL, f"{WS_TOKEN_PREFIX}{token}"]


def connect_code(client, url: str, **kwargs) -> int | None:
    """
    Try to open a WebSocket and report the close code of a refusal.

    Args:
        client: The test client.
        url: The WebSocket path.
        **kwargs: Extra arguments for ``websocket_connect``.

    Returns:
        The close code, or None when the handshake was accepted.
    """
    try:
        with client.websocket_connect(url, **kwargs):
            return None
    except WebSocketDisconnect as exc:
        return exc.code
