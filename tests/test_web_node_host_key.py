# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Accepting the new SSH host key of a server over HTTP, with no console page yet.

The two routes are what ``noust node host-key`` does on the same registry: the first asks the server's own
address which key it presents and says it beside the pinned one; the second takes the key whose fingerprint
the caller gives, which is the whole of the decision, and needs sudo mode. What the web layer adds is held
here: who may ask, that a refusal is audited, and that the account that holds only the permission to read
the fleet is turned away.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from noust.core.secrets import SecretStore
from noust.fleet.keys import NodeKeys, known_hosts_line
from noust.fleet.nodes import NodeManager
from noust.fleet.tunnels import TunnelManager
from noust.web.api import nodes as nodes_api
from noust.web.permissions import Permission
from tests.support.fleet_support import (
    HOST_KEY,
    Clock,
    Fleet,
    KeygenRunner,
    ed25519_line,
    fingerprint,
)
from tests.support.fleet_support import FakeNode as FleetNode
from tests.support.teams_support import World, build_world

NEW_KEY = ed25519_line(2)
NEW_FINGERPRINT = fingerprint(NEW_KEY)
PINNED_FINGERPRINT = fingerprint(HOST_KEY)


@dataclass
class Rig:
    """The console over a real registry of one server, ``web-2``, whose host key changed."""

    world: World
    fleet: Fleet
    state: Path

    def audit(self) -> list[dict[str, Any]]:
        path = self.state / "web-audit.log"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def post(self, path: str, who: str = "admin", **body: Any) -> Any:
        return self.world.client.post(path, json=body, headers=self.world.as_(who))


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Rig:
    world = build_world(tmp_path, monkeypatch)
    secrets = SecretStore(root=tmp_path / "secrets")
    keygen = KeygenRunner()
    keys = NodeKeys(secrets, keygen)
    clock = Clock()
    ports = iter(range(50000, 51000))
    tunnels = TunnelManager(
        runner=keygen,
        store=world.store,
        keys=keys,
        clock=clock,
        sleep=lambda seconds: clock.advance(seconds),
        probe=lambda port: True,
        free_port=lambda: next(ports),
    )
    node = FleetNode()
    manager = NodeManager(
        store=world.store,
        secrets=secrets,
        runner=keygen,
        tunnels=tunnels,
        blockers=list,
        transport=node.transport(),
    )
    fleet = Fleet(world.store, secrets, keygen, keys, tunnels, node, manager, clock, {})
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")
    monkeypatch.setattr(nodes_api, "node_manager", lambda: manager)
    monkeypatch.setattr(nodes_api, "tunnels", lambda: tunnels)
    fleet.added()
    keygen.script(["ssh-keyscan"], stdout=f"web2.example.com {NEW_KEY}\n")
    return Rig(world, fleet, tmp_path / "state")


class TestAskingWhatTheServerPresents:
    def test_both_fingerprints_come_back_and_nothing_changes(self, rig: Rig) -> None:
        response = rig.post("/api/nodes/web-2/host-key/scan")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["node"] == "web-2"
        assert body["pinned"] == PINNED_FINGERPRINT
        assert body["presented"] == NEW_FINGERPRINT
        assert body["changed"] is True
        assert rig.fleet.manager.get("web-2").host_key == known_hosts_line("web-2", HOST_KEY)

    def test_a_server_that_is_not_registered_is_not_found(self, rig: Rig) -> None:
        assert rig.post("/api/nodes/nope/host-key/scan").status_code == 404

    def test_a_team_member_may_not_ask(self, rig: Rig) -> None:
        response = rig.post("/api/nodes/web-2/host-key/scan", who="dana")

        assert response.status_code in (403, 404)

    def test_the_permission_is_the_one_that_manages_the_fleet(self) -> None:
        from noust.web.permissions.routes_nodes import ROUTES

        assert ROUTES[("POST", "/api/nodes/{node}/host-key/scan")] == Permission.FLEET_MANAGE
        assert ROUTES[("POST", "/api/nodes/{node}/host-key")] == Permission.FLEET_MANAGE


class TestAccepting:
    def test_the_fingerprint_that_is_what_answers_is_pinned_and_audited(self, rig: Rig) -> None:
        response = rig.post("/api/nodes/web-2/host-key", fingerprint=NEW_FINGERPRINT)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["changed"] is True
        assert (body["previous"], body["presented"]) == (PINNED_FINGERPRINT, NEW_FINGERPRINT)
        assert body["node"]["name"] == "web-2" and body["node"]["status"] == "reachable"
        assert rig.fleet.manager.get("web-2").host_key == known_hosts_line("web-2", NEW_KEY)
        (event,) = [e for e in rig.audit() if e["action"] == "fleet.node.host_key"]
        assert event["result"] == "ok" and event["resource"] == "node:web-2"
        assert NEW_FINGERPRINT in event["detail"] and PINNED_FINGERPRINT in event["detail"]

    def test_another_fingerprint_changes_nothing_and_the_refusal_is_audited(self, rig: Rig) -> None:
        response = rig.post("/api/nodes/web-2/host-key", fingerprint=fingerprint(ed25519_line(3)))

        assert response.status_code == 400
        assert rig.fleet.manager.get("web-2").host_key == known_hosts_line("web-2", HOST_KEY)
        (event,) = [e for e in rig.audit() if e["action"] == "fleet.node.host_key"]
        assert event["result"] == "failure"

    def test_a_text_that_is_not_a_fingerprint_is_a_400_naming_the_field(self, rig: Rig) -> None:
        response = rig.post("/api/nodes/web-2/host-key", fingerprint="abc")

        assert response.status_code in (400, 422)
        assert "fingerprint" in response.text

    def test_it_needs_sudo_mode_and_looking_does_not(self, rig: Rig) -> None:
        from noust.web.api.openapi import api_routes, route_requires_elevation

        routes = {
            route.path_format: route
            for route in api_routes(rig.world.client.app.routes)
            if "POST" in route.methods and "host-key" in route.path_format
        }

        assert route_requires_elevation(routes["/api/nodes/{node}/host-key"]) is True
        assert route_requires_elevation(routes["/api/nodes/{node}/host-key/scan"]) is False

    def test_accepting_needs_a_second_person_when_approvals_are_on_and_looking_does_not(
        self, rig: Rig
    ) -> None:
        from noust.core.accounts.approvals import one_time_fields, rule_for

        schema = rig.world.client.app.openapi()
        accept = schema["paths"]["/api/nodes/{node}/host-key"]["post"]
        scan = schema["paths"]["/api/nodes/{node}/host-key/scan"]["post"]

        assert accept.get("x-noust-requires-approval")
        assert accept.get("x-noust-requires-elevation") is True
        assert not scan.get("x-noust-requires-approval")
        rule = rule_for("POST", "/api/nodes/{node}/host-key", [Permission.FLEET_MANAGE], {}, {})
        assert rule is not None
        assert (rule.action, rule.kind) == ("fleet.node.host_key", "infrastructure")
        assert (
            rule_for("POST", "/api/nodes/{node}/host-key/scan", [Permission.FLEET_MANAGE], {}, {})
            is None
        )
        # The fingerprint is not a credential: it is what the second person is asked to accept.
        assert one_time_fields("POST", "/api/nodes/{node}/host-key") == ()

    def test_the_fingerprint_is_in_the_digest_and_in_what_the_decider_reads(self) -> None:
        import json

        from noust.core.accounts.approvals import snapshot

        path = "/api/nodes/web-2/host-key"
        first, shown = snapshot(
            "POST", path, [], json.dumps({"fingerprint": NEW_FINGERPRINT}).encode()
        )
        other, _ = snapshot(
            "POST", path, [], json.dumps({"fingerprint": PINNED_FINGERPRINT}).encode()
        )

        assert first != other
        assert shown["body"] == {"fingerprint": NEW_FINGERPRINT}

    def test_a_team_member_may_not_accept(self, rig: Rig) -> None:
        response = rig.post("/api/nodes/web-2/host-key", who="dana", fingerprint=NEW_FINGERPRINT)

        assert response.status_code in (403, 404)
        assert rig.fleet.manager.get("web-2").host_key == known_hosts_line("web-2", HOST_KEY)
