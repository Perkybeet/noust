# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Rotating a server's key, and moving it to the tunnel account, over HTTP (F-03).

The routes are the two steps of ``noust node rekey`` and ``noust node migrate-tunnel`` on the real
registry: the first asks for the command to run on the server, the second hands over the join code
it printed. What is pinned here is what the web layer adds: who may ask, what is audited, and that
the join code, which carries the server's token, is never echoed or recorded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from noust.core.accounts.approvals import one_time_fields, rule_for
from noust.core.secrets import SecretStore
from noust.fleet.keys import TOKEN, NodeKeys, secret_name
from noust.fleet.nodes import NodeManager
from noust.fleet.tunnels import TunnelManager
from noust.web.api import nodes as nodes_api
from noust.web.permissions import Permission
from tests.fleet_support import (
    OTHER_TOKEN,
    Clock,
    Fleet,
    KeygenRunner,
    ed25519_line,
    join_code,
)
from tests.fleet_support import FakeNode as FleetNode
from tests.teams_support import World, build_world


@dataclass
class Rig:
    """The console over a real registry of one server, `web-2`."""

    world: World
    fleet: Fleet
    state: Path

    def audit(self) -> list[dict[str, Any]]:
        """
        Returns:
            The audit lines written so far, oldest first.
        """
        path = self.state / "web-audit.log"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def post(self, path: str, who: str = "admin", **body: Any) -> Any:
        """
        Args:
            path: The route.
            who: The caller.
            **body: The JSON body.

        Returns:
            The response.
        """
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
    return Rig(world, fleet, tmp_path / "state")


def _token(rig: Rig) -> str | None:
    return rig.fleet.secrets.read(secret_name("web-2", TOKEN))


class TestRekey:
    def test_the_first_step_gives_the_command_for_the_new_key_and_the_same_again(
        self, rig: Rig
    ) -> None:
        first = rig.post("/api/nodes/web-2/rekey")
        second = rig.post("/api/nodes/web-2/rekey")

        assert first.status_code == 200, first.text
        body = first.json()
        assert body["step"] == "authorize"
        assert body["node"] is None
        assert body["authorize_command"].startswith("noust fleet authorize")
        pending = rig.fleet.keys.pending_public_key("web-2")
        assert pending is not None and pending.blob in body["authorize_command"]
        assert second.json()["authorize_command"] == body["authorize_command"]

    def test_the_code_the_server_printed_switches_to_the_new_key(self, rig: Rig) -> None:
        rig.post("/api/nodes/web-2/rekey")
        new = rig.fleet.keys.pending_public_key("web-2")
        rig.fleet.node.token = OTHER_TOKEN
        code = join_code(new.line(), token=OTHER_TOKEN, ssh_user="noust-tunnel")

        response = rig.post("/api/nodes/web-2/rekey", join_code=code)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["step"] == "done"
        assert body["authorize_command"] is None
        assert body["node"]["name"] == "web-2"
        assert body["node"]["status"] == "reachable"
        assert rig.fleet.keys.public_key("web-2").fingerprint == new.fingerprint
        assert rig.fleet.keys.pending_public_key("web-2") is None
        assert _token(rig) == OTHER_TOKEN

    def test_it_is_audited_without_the_code_or_the_token_it_carries(self, rig: Rig) -> None:
        rig.post("/api/nodes/web-2/rekey")
        new = rig.fleet.keys.pending_public_key("web-2")
        rig.fleet.node.token = OTHER_TOKEN
        code = join_code(new.line(), token=OTHER_TOKEN, ssh_user="noust-tunnel")

        response = rig.post("/api/nodes/web-2/rekey", join_code=code)

        (event,) = [e for e in rig.audit() if e["action"] == "fleet.node.rekey"]
        assert event["result"] == "ok"
        assert event["resource"] == "node:web-2"
        assert "noust-tunnel@" in event["detail"]
        everything = json.dumps(rig.audit()) + response.text
        assert OTHER_TOKEN not in everything
        assert code not in everything

    def test_a_server_that_does_not_answer_is_put_back_and_the_failure_is_audited(
        self, rig: Rig
    ) -> None:
        old = rig.fleet.keys.public_key("web-2")
        rig.post("/api/nodes/web-2/rekey")
        new = rig.fleet.keys.pending_public_key("web-2")
        # The server never accepted the new token.
        code = join_code(new.line(), token=OTHER_TOKEN)

        response = rig.post("/api/nodes/web-2/rekey", join_code=code)

        assert response.status_code >= 400
        assert rig.fleet.keys.public_key("web-2").fingerprint == old.fingerprint
        assert rig.fleet.keys.pending_public_key("web-2") is not None
        (event,) = [e for e in rig.audit() if e["action"] == "fleet.node.rekey"]
        assert event["result"] == "failure"
        assert OTHER_TOKEN not in json.dumps(rig.audit()) + response.text

        # Resumable: once the server takes it, the same code finishes the job.
        rig.fleet.node.token = OTHER_TOKEN
        again = rig.post("/api/nodes/web-2/rekey", join_code=code)
        assert again.status_code == 200, again.text

    def test_a_code_for_another_key_says_which_step_comes_first(self, rig: Rig) -> None:
        stranger = ed25519_line(5, "noust-central@nas")

        response = rig.post("/api/nodes/web-2/rekey", join_code=join_code(stranger))

        assert response.status_code == 400
        assert "noust node rekey web-2" in json.dumps(response.json())

    def test_a_server_that_is_not_registered_is_not_found(self, rig: Rig) -> None:
        assert rig.post("/api/nodes/nope/rekey").status_code == 404
        assert rig.post("/api/nodes/nope/migrate-tunnel").status_code == 404


class TestMigrateTunnel:
    def test_the_first_step_replaces_the_key_in_roots_authorized_keys_by_the_one_it_holds(
        self, rig: Rig
    ) -> None:
        response = rig.post("/api/nodes/web-2/migrate-tunnel")

        assert response.status_code == 200, response.text
        body = response.json()
        current = rig.fleet.keys.public_key("web-2")
        assert body["step"] == "authorize"
        assert "--replace-root-key" in body["authorize_command"]
        assert current.blob in body["authorize_command"]

    def test_the_code_of_the_tunnel_account_moves_the_server_and_is_audited(self, rig: Rig) -> None:
        key = rig.fleet.keys.public_key("web-2")

        response = rig.post(
            "/api/nodes/web-2/migrate-tunnel",
            join_code=join_code(key.line(), ssh_user="noust-tunnel"),
        )

        assert response.status_code == 200, response.text
        assert response.json()["step"] == "done"
        assert response.json()["node"]["ssh_user"] == "noust-tunnel"
        (event,) = [e for e in rig.audit() if e["action"] == "fleet.node.migrate"]
        assert event["result"] == "ok"
        assert event["resource"] == "node:web-2"

    def test_a_code_that_still_names_root_is_refused_and_audited(self, rig: Rig) -> None:
        key = rig.fleet.keys.public_key("web-2")

        response = rig.post(
            "/api/nodes/web-2/migrate-tunnel", join_code=join_code(key.line(), ssh_user="root")
        )

        assert response.status_code == 400
        assert "root" in json.dumps(response.json())
        (event,) = [e for e in rig.audit() if e["action"] == "fleet.node.migrate"]
        assert event["result"] == "failure"


class TestWhoMay:
    @pytest.mark.parametrize("who", ["alice", "bob", "dana", "omar", "aud"])
    @pytest.mark.parametrize("route", ["rekey", "migrate-tunnel"])
    def test_only_who_manages_the_fleet_may_rotate_a_key(
        self, rig: Rig, who: str, route: str
    ) -> None:
        response = rig.post(f"/api/nodes/web-2/{route}", who)

        assert response.status_code in (403, 404), response.text
        assert rig.fleet.keys.pending_public_key("web-2") is None

    def test_a_team_that_has_the_server_still_may_not(self, rig: Rig) -> None:
        from noust.core.teams.nodes import NodeAssignments

        NodeAssignments(rig.world.store).assign("web-2", "acme")

        response = rig.post("/api/nodes/web-2/rekey", "alice")

        assert response.status_code == 403
        assert rig.fleet.keys.pending_public_key("web-2") is None

    def test_both_steps_ask_for_sudo_mode_and_a_second_person_when_approvals_are_on(
        self, rig: Rig
    ) -> None:
        schema = rig.world.client.app.openapi()
        for route in ("rekey", "migrate-tunnel"):
            template = f"/api/nodes/{{node}}/{route}"
            operation = schema["paths"][template]["post"]
            assert operation.get("x-noust-requires-elevation") is True
            assert operation.get("x-noust-requires-approval")
            rule = rule_for("POST", template, [Permission.FLEET_MANAGE], {}, {})
            assert rule is not None and rule.kind == "infrastructure"
            # The join code carries the server's token: a fingerprint would keep a hash of it.
            assert one_time_fields("POST", template) == ("join_code",)
