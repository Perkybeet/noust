# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The assistant calls the console's API through the gateway of MCP, saying who it is (item 171).

What a credential's list of clients shows is read from the name a call says it is made by, so the
assistant's calls must carry its own and not the MCP gateway's, and both must be Noust's, never a client.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from noust.core.credential_clients import parse_user_agent
from noust.web.assistant.bridge import LoopBridge
from noust.web.mcp.gateway import call_api


def recording_app(seen: list[dict[bytes, bytes]]) -> Any:
    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        seen.append(dict(scope["headers"]))
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b"{}"})

    return app


def test_the_gateway_of_mcp_still_says_it_is_the_gateway_of_mcp() -> None:
    seen: list[dict[bytes, bytes]] = []
    asyncio.run(call_api(recording_app(seen), "GET", "/api/x", client_host="127.0.0.1", host="h"))
    assert seen[0][b"user-agent"] == b"noust-mcp"


def test_a_call_says_what_it_is_made_by_when_asked_to() -> None:
    seen: list[dict[bytes, bytes]] = []
    asyncio.run(
        call_api(
            recording_app(seen),
            "GET",
            "/api/x",
            client_host="127.0.0.1",
            host="h",
            user_agent="noust-assistant",
        )
    )
    assert seen[0][b"user-agent"] == b"noust-assistant"


def test_the_assistants_bridge_says_it_is_the_assistant() -> None:
    seen: list[dict[bytes, bytes]] = []
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        bridge = LoopBridge(app=recording_app(seen), loop=loop, client_host="127.0.0.1", host="h")
        answer = bridge.call("GET", "/api/x", token="noust_example_token")
        assert answer.status == 200
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()
    assert seen[0][b"user-agent"] == b"noust-assistant"


@pytest.mark.parametrize("header", ["noust-assistant", "noust-assistant/1", "noust-mcp"])
def test_what_noust_calls_itself_is_never_listed_as_a_client_of_a_credential(header: str) -> None:
    assert parse_user_agent(header) is None
