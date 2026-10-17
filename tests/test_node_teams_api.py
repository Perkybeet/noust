# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Servers of the fleet assigned to teams, over HTTP (rule 4 of the teams).

Two teams, each with a server, and a spare one nobody took. What a member of a
team may know and do about them is what the scope table and the proxy say; the
fake node behind the tunnel is a real socket, so a call that is forwarded is
seen to arrive and one that is refused is seen not to.
"""

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
from tests.teams_support import World, build_world, make_person
from tests.test_node_proxy import FakeManager, FakeNode, FakeTunnels, ServedNode


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
def world(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> World:
    return build_world(tmp_path, monkeypatch)


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


def _names(response: Any) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["name"] for item in response.json()["items"]]


class TestWhoSeesWhichServer:
    def test_a_member_lists_its_teams_servers_and_no_other(
        self, world: World, fleet: StoreManager
    ) -> None:
        assert _names(world.client.get("/api/nodes", headers=world.as_("alice"))) == ["acme-1"]
        assert _names(world.client.get("/api/nodes", headers=world.as_("bob"))) == ["globex-1"]
        assert _names(world.client.get("/api/nodes", headers=world.as_("dana"))) == ["acme-1"]

    def test_the_instance_sees_all_of_them_and_the_team_of_each(
        self, world: World, fleet: StoreManager
    ) -> None:
        response = world.client.get("/api/nodes", headers=world.as_("admin"))

        teams = {item["name"]: item["team"] for item in response.json()["items"]}
        assert teams == {"acme-1": "acme", "globex-1": "globex", "spare": None}

    def test_an_account_of_the_default_team_sees_none_of_them(
        self, world: World, fleet: StoreManager
    ) -> None:
        lone = make_person(world.teams, "lone", "viewer")

        assert _names(world.client.get("/api/nodes", headers=lone.headers)) == []

    def test_another_teams_server_is_answered_as_one_that_does_not_exist(
        self, world: World, fleet: StoreManager
    ) -> None:
        other = world.client.get("/api/nodes/globex-1", headers=world.as_("alice"))
        spare = world.client.get("/api/nodes/spare", headers=world.as_("alice"))
        ghost = world.client.get("/api/nodes/ghost", headers=world.as_("alice"))

        assert other.status_code == spare.status_code == ghost.status_code == 404
        assert other.json()["detail"].replace("globex-1", "X") == ghost.json()["detail"].replace(
            "ghost", "X"
        )
        assert spare.json()["detail"].replace("spare", "X") == ghost.json()["detail"].replace(
            "ghost", "X"
        )

    def test_a_member_reads_and_tests_its_own_server(
        self, world: World, fleet: StoreManager
    ) -> None:
        read = world.client.get("/api/nodes/acme-1", headers=world.as_("dana"))
        test = world.client.post("/api/nodes/acme-1/test", headers=world.as_("alice"))

        assert read.status_code == 200 and read.json()["team"] == "acme"
        assert test.status_code == 200 and test.json()["reachable"] is True

    def test_testing_another_teams_server_is_a_404(self, world: World, fleet: StoreManager) -> None:
        assert (
            world.client.post("/api/nodes/globex-1/test", headers=world.as_("alice")).status_code
            == 404
        )


class TestWhatStaysTheInstances:
    # Changing the fleet needs fleet.manage, which no team role holds: the permission refuses it
    # first. Reading how the central reaches a server needs only fleet.read, which every account
    # holds, so the scope table is what keeps a team out.
    @pytest.mark.parametrize(
        ("method", "path", "error"),
        [
            ("POST", "/api/nodes", "permission_denied"),
            ("DELETE", "/api/nodes/acme-1", "permission_denied"),
            ("GET", "/api/nodes/acme-1/key", "permission_denied"),
            ("PUT", "/api/nodes/acme-1/ssh", "permission_denied"),
            ("PUT", "/api/nodes/acme-1/monitoring", "permission_denied"),
            ("PUT", "/api/nodes/acme-1/team", "permission_denied"),
            ("GET", "/api/nodes/acme-1/ssh", "instance_scope_required"),
            ("GET", "/api/nodes/acme-1/inventory", "instance_scope_required"),
            ("GET", "/api/fleet/servers", "instance_scope_required"),
        ],
    )
    def test_how_the_central_reaches_a_server_is_not_a_teams(
        self, world: World, fleet: StoreManager, method: str, path: str, error: str
    ) -> None:
        response = world.client.request(method, path, headers=world.as_("alice"), json={})

        assert response.status_code == 403
        assert response.json()["error"] == error


class TestAssigning:
    def test_the_instance_assigns_a_server_and_the_team_then_sees_it(
        self, world: World, fleet: StoreManager
    ) -> None:
        before = world.client.get("/api/nodes/spare", headers=world.as_("alice"))
        assigned = world.client.put(
            "/api/nodes/spare/team", headers=world.as_("admin"), json={"team": "acme"}
        )
        after = world.client.get("/api/nodes/spare", headers=world.as_("alice"))

        assert (before.status_code, assigned.status_code, after.status_code) == (404, 200, 200)
        assert assigned.json()["team"] == "acme"

    def test_giving_it_back_hides_it_from_the_team_again(
        self, world: World, fleet: StoreManager
    ) -> None:
        returned = world.client.put(
            "/api/nodes/acme-1/team", headers=world.as_("admin"), json={"team": None}
        )

        assert returned.json()["team"] is None
        assert world.client.get("/api/nodes/acme-1", headers=world.as_("alice")).status_code == 404

    def test_a_team_that_does_not_exist_is_refused_and_nothing_changes(
        self, world: World, fleet: StoreManager
    ) -> None:
        response = world.client.put(
            "/api/nodes/spare/team", headers=world.as_("admin"), json={"team": "nope"}
        )

        assert response.status_code == 404
        assert NodeAssignments(world.store).team_of("spare") is None

    def test_moving_a_server_takes_it_from_the_first_team(
        self, world: World, fleet: StoreManager
    ) -> None:
        world.client.put(
            "/api/nodes/acme-1/team", headers=world.as_("admin"), json={"team": "globex"}
        )

        assert _names(world.client.get("/api/nodes", headers=world.as_("alice"))) == []
        assert _names(world.client.get("/api/nodes", headers=world.as_("bob"))) == [
            "acme-1",
            "globex-1",
        ]


class TestWorkingOnATeamsServer:
    def test_a_member_reaches_its_servers_api_through_the_central(
        self, world: World, fleet: StoreManager, served: tuple[FakeNode, int]
    ) -> None:
        node, _port = served

        response = world.client.get(
            "/api/nodes/acme-1/api/apps/shop.acme.example", headers=world.as_("alice")
        )

        assert response.status_code == 200
        assert response.json() == {"domain": "shop.acme.example"}
        assert [seen["method"] for seen in node.seen] == ["GET"]

    def test_a_viewer_reads_but_does_not_delete(
        self, world: World, fleet: StoreManager, served: tuple[FakeNode, int]
    ) -> None:
        node, _port = served

        read = world.client.get(
            "/api/nodes/acme-1/api/apps/shop.acme.example", headers=world.as_("dana")
        )
        delete = world.client.delete(
            "/api/nodes/acme-1/api/apps/shop.acme.example", headers=world.as_("dana")
        )

        assert read.status_code == 200
        assert delete.status_code == 403
        assert delete.json()["error"] == "permission_denied"
        assert [seen["method"] for seen in node.seen] == ["GET"]

    def test_a_team_admin_manages_the_applications_of_its_server(
        self, world: World, fleet: StoreManager
    ) -> None:
        response = world.client.delete(
            "/api/nodes/acme-1/api/apps/shop.acme.example", headers=world.as_("alice")
        )

        assert response.status_code == 202

    def test_another_teams_server_cannot_be_reached_through_the_proxy(
        self, world: World, fleet: StoreManager, served: tuple[FakeNode, int]
    ) -> None:
        node, _port = served

        response = world.client.get(
            "/api/nodes/globex-1/api/apps/blog.globex.example", headers=world.as_("alice")
        )

        assert response.status_code == 404
        assert node.seen == []

    def test_a_server_nobody_took_cannot_be_reached_by_a_member(
        self, world: World, fleet: StoreManager, served: tuple[FakeNode, int]
    ) -> None:
        node, _port = served

        response = world.client.get(
            "/api/nodes/spare/api/apps/x.example", headers=world.as_("alice")
        )

        assert response.status_code == 404
        assert node.seen == []

    @pytest.mark.parametrize(
        ("who", "role"),
        [("dana", "viewer"), ("omar", "operator"), ("alice", "operator")],
    )
    def test_the_node_is_told_the_role_the_member_holds_in_the_servers_team(
        self, world: World, fleet: StoreManager, served: tuple[FakeNode, int], who: str, role: str
    ) -> None:
        # A node knows roles, not teams: a team admin is an operator there, never more than it
        # holds in the team, and never the instance role an account happens to have elsewhere.
        node, _port = served

        world.client.get("/api/nodes/acme-1/api/apps/shop.acme.example", headers=world.as_(who))

        assert node.seen[-1]["headers"]["x-noust-actor-role"] == role

    def test_what_is_about_the_machine_is_refused_before_it_is_forwarded(
        self, world: World, fleet: StoreManager, served: tuple[FakeNode, int]
    ) -> None:
        node, _port = served

        reboot = world.client.post(
            "/api/nodes/acme-1/api/server/power/reboot", headers=world.as_("alice"), json={}
        )

        assert reboot.status_code == 403
        assert reboot.json()["error"] == "instance_scope_required"
        assert node.seen == []

    def test_a_path_the_central_cannot_classify_is_refused(
        self, world: World, fleet: StoreManager, served: tuple[FakeNode, int]
    ) -> None:
        node, _port = served

        response = world.client.get("/api/nodes/acme-1/api/echo/x", headers=world.as_("alice"))

        assert response.status_code == 403
        assert response.json()["error"] == "scope_undeclared"
        assert node.seen == []

    def test_a_permission_is_held_in_the_servers_team_not_in_any_team(
        self, world: World, fleet: StoreManager
    ) -> None:
        # Admin of acme, viewer of globex: the ceiling has apps.manage, the globex server does not.
        carol = make_person(world.teams, "carol", "viewer", team="acme", team_role="admin")
        world.teams.add_member("globex", "carol", "viewer")

        own = world.client.delete("/api/nodes/acme-1/api/apps/a.example", headers=carol.headers)
        other = world.client.delete("/api/nodes/globex-1/api/apps/b.example", headers=carol.headers)

        assert own.status_code == 202
        assert other.status_code == 403
        assert other.json()["error"] == "permission_denied"

    def test_the_instance_is_not_held_to_any_of_it(
        self, world: World, fleet: StoreManager, served: tuple[FakeNode, int]
    ) -> None:
        response = world.client.get(
            "/api/nodes/spare/api/echo/anything", headers=world.as_("admin")
        )

        assert response.status_code == 200


class TestStreams:
    def test_a_member_opens_a_stream_of_its_own_server(
        self, world: World, fleet: StoreManager
    ) -> None:
        with world.client.websocket_connect(
            "/ws/nodes/acme-1/events", headers=world.as_("alice")
        ) as socket:
            assert "from the node" in socket.receive_text()

    def test_another_teams_server_has_no_stream_for_it(
        self, world: World, fleet: StoreManager
    ) -> None:
        with pytest.raises(WebSocketDisconnect) as closed:
            with world.client.websocket_connect(
                "/ws/nodes/globex-1/events", headers=world.as_("alice")
            ) as socket:
                socket.receive_text()

        assert closed.value.code == node_proxy.WS_CLOSE_NODE_NOT_FOUND

    def test_a_stream_the_central_does_not_know_is_refused(
        self, world: World, fleet: StoreManager
    ) -> None:
        with pytest.raises(WebSocketDisconnect) as closed:
            with world.client.websocket_connect(
                "/ws/nodes/acme-1/echo", headers=world.as_("alice")
            ) as socket:
                socket.receive_text()

        assert closed.value.code == node_proxy.WS_CLOSE_FORBIDDEN
