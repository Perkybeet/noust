# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_node_teams_api.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from noust.core.store import NodeRecord
from noust.core.teams.nodes import NodeAssignments
from noust.web.api import node_proxy
from noust.web.api import nodes as nodes_api
from tests.support.node_proxy import FakeManager, FakeNode, FakeTunnels, ServedNode
from tests.support.teams_support import World


class StoreManager(FakeManager):
    """The registry the API uses, reading the servers the store records."""

    def __init__(self, tunnels: FakeTunnels, store: Any) -> None:
        super().__init__(tunnels)
        self.store = store

    def get(self, name: str) -> NodeRecord:
        record = self.store.get_node(name)
        if record is None:
            from noust.core.exceptions import NodeError

            raise NodeError(f"No node named {name} is registered on this central")
        return record

    def list(self) -> list[NodeRecord]:
        return self.store.list_nodes()

    def get_node(self, name: str) -> NodeRecord | None:
        return self.store.get_node(name)

    def set_node_status(self, name: str, status: str, *, version: str | None = None) -> bool:
        return bool(self.store.set_node_status(name, status, version=version))


def _record(name: str) -> NodeRecord:
    base = FakeManager(FakeTunnels(0)).records["web-2"]
    return replace(base, name=name, ssh_host=f"{name}.example.com")


@pytest.fixture(scope="module")
def served() -> Iterator[tuple[FakeNode, int]]:
    node = FakeNode()

    async def events(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text('{"hello": "from the node"}')
        try:
            await websocket.receive_text()
        except WebSocketDisconnect:
            return

    node.app.add_api_websocket_route("/ws/events", events)
    with ServedNode(node) as running:
        yield node, running.port


@pytest.fixture
def fleet(
    world: World, served: tuple[FakeNode, int], monkeypatch: pytest.MonkeyPatch
) -> StoreManager:
    node, port = served
    node.seen.clear()
    node_proxy.node_schemas.forget()
    tunnels = FakeTunnels(port)
    manager = StoreManager(tunnels, world.store)
    monkeypatch.setattr(nodes_api, "node_manager", lambda: manager)
    monkeypatch.setattr(nodes_api, "tunnels", lambda: tunnels)
    assignments = NodeAssignments(world.store)
    for name, team in (("acme-1", "acme"), ("globex-1", "globex"), ("spare", None)):
        world.store.save_node(_record(name))
        if team is not None:
            assignments.assign(name, team)
    return manager
