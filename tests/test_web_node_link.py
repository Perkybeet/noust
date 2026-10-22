# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Servers that dial in, over HTTP: ``/api/fleet/link``.

The link itself is tested with the runner (``tests/test_fleet_link.py``); what is
pinned here is what the HTTP layer owes: sudo mode for everything that changes
what this central trusts, an invitation that never shows its nonce in a list, a
code that registers a server which then lists as one that dials in, and the
permission and the approval of each route.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from noust.core.accounts.approvals import rule_for
from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.web.api import node_link as api
from noust.web.auth import CSRF_HEADER_NAME
from noust.web.permissions import Permission
from noust.web.permissions.registry import permission_for_route
from noust.web.server import create_app, get_token_manager
from tests.link_support import LINK_UID, LinkRig, build_link_rig
from tests.test_web_auth import make_config


@pytest.fixture(autouse=True)
def fresh_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("noust.fleet.link.central.central_name", lambda: "nas")
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")
    Config.reset_instance()
    NoustStore.reset_instance()
    yield
    Config.reset_instance()
    NoustStore.reset_instance()


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LinkRig:
    built = build_link_rig(tmp_path / "central")
    monkeypatch.setattr(api, "_links", lambda: built.links)
    monkeypatch.setattr(api, "node_manager", lambda: built.manager)
    monkeypatch.setattr("noust.web.api.nodes.node_manager", lambda: built.manager)
    monkeypatch.setattr("noust.web.api.nodes.tunnels", lambda: built.tunnels)
    monkeypatch.setattr("noust.web.api.nodes.node_store", lambda: built.fleet.store)
    return built


@pytest.fixture
def client(sandbox: Path, runner: FakeRunner) -> TestClient:
    return TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))


def sign_in(client: TestClient) -> str:
    master = get_token_manager().generate_master_token()
    response = client.post("/api/auth/login", json={"token": master})
    assert response.status_code == 200, response.text
    client.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return master


def elevate(client: TestClient, master: str) -> None:
    response = client.post("/api/auth/elevate", json={"token": master})
    assert response.status_code == 200, response.text


def test_the_status_says_whether_this_central_takes_servers_that_dial_in(
    client: TestClient, rig: LinkRig
) -> None:
    sign_in(client)

    response = client.get("/api/fleet/link")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is True
    assert body["problems"] == []
    assert body["setup_command"] == "noust node link setup"
    assert body["links"] == []


def test_an_invitation_needs_sudo_mode_and_gives_the_command(
    client: TestClient, rig: LinkRig
) -> None:
    master = sign_in(client)
    request = {"name": "web-5", "host": "central.example.com"}

    refused = client.post("/api/fleet/link/invites", json=request)
    assert refused.status_code == 403
    assert refused.json()["error"] == "elevation_required"
    assert rig.links.records.list() == []

    elevate(client, master)
    response = client.post("/api/fleet/link/invites", json=request)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["node"] == "web-5"
    assert body["command"].startswith("noust fleet link --name nas --as web-5 --host ")
    assert "--invite " in body["command"]
    # The nonce is in the command it is meant to be pasted with, and in no list.
    listed = client.get("/api/fleet/link").json()["links"]
    assert [row["node"] for row in listed] == ["web-5"]
    assert "nonce" not in listed[0]


def test_a_refusal_says_what_is_wrong(client: TestClient, rig: LinkRig) -> None:
    master = sign_in(client)
    elevate(client, master)

    response = client.post(
        "/api/fleet/link/invites", json={"name": "web-5", "host": "x.example.com; reboot"}
    )

    assert response.status_code == 400
    assert "Invalid host" in response.json()["detail"]


def test_a_cancelled_invitation_is_gone(client: TestClient, rig: LinkRig) -> None:
    master = sign_in(client)
    elevate(client, master)
    client.post("/api/fleet/link/invites", json={"name": "web-5", "host": "central.example.com"})

    response = client.delete("/api/fleet/link/invites/web-5")

    assert response.status_code == 204
    assert rig.links.records.list() == []


def test_a_code_registers_a_server_that_then_lists_as_one_that_dials_in(
    client: TestClient, rig: LinkRig
) -> None:
    master = sign_in(client)
    elevate(client, master)
    invite = rig.invite("web-5")
    rig.central.listen(invite.port, uid=LINK_UID)

    response = client.post(
        "/api/fleet/link/accept",
        json={"name": "web-5", "link_code": rig.code(invite), "wait_seconds": 0},
    )

    assert response.status_code == 201, response.text
    node = response.json()
    assert node["name"] == "web-5"
    assert node["ssh_user"] == "noust-link"
    assert node["link"]["state"] == "active"
    assert node["link"]["port"] == invite.port
    assert node["tunnel"]["transport"] == "link"
    assert node["tunnel"]["open"] is True
    listed = client.get("/api/nodes").json()["items"]
    assert [item["name"] for item in listed] == ["web-5"]
    assert listed[0]["link"]["port"] == invite.port


def test_a_server_that_is_not_connected_is_a_bad_gateway_and_leaves_nothing(
    client: TestClient, rig: LinkRig
) -> None:
    master = sign_in(client)
    elevate(client, master)
    invite = rig.invite("web-5")

    response = client.post(
        "/api/fleet/link/accept",
        json={"name": "web-5", "link_code": rig.code(invite), "wait_seconds": 0},
    )

    assert response.status_code == 502, response.text
    assert response.json()["error"] == "node_unreachable"
    assert rig.fleet.store.get_node("web-5") is None
    assert rig.central.keys_file.read_text() == ""


def test_every_route_has_a_permission_and_the_one_that_adds_a_server_an_approval() -> None:
    for method, path in (
        ("GET", "/api/fleet/link"),
        ("POST", "/api/fleet/link/invites"),
        ("DELETE", "/api/fleet/link/invites/{name}"),
        ("POST", "/api/fleet/link/accept"),
    ):
        assert permission_for_route(method, path) == Permission.FLEET_MANAGE
    rule = rule_for("POST", "/api/fleet/link/accept", [Permission.FLEET_MANAGE], {}, {})
    assert rule is not None and rule.action == "fleet.node.link"
    assert rule_for("POST", "/api/fleet/link/invites", [Permission.FLEET_MANAGE], {}, {}) is None
