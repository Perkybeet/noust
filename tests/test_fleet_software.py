# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The software of every server of the fleet, read by a central (4.2, item 118).

``GET /api/fleet/software`` is the fleet's view of each server's ``GET /api/server/software``: one
row per server with how much of the software Noust manages is installed there and how much of that
Noust put there. A server that runs a Noust of before 4.2 has no such route, and is ``unsupported``,
never an error and never an empty list that reads as "nothing installed": its row says the server
will count its software when it is updated.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.fleet.aggregate import RESOURCES, set_aggregator
from noust.web.auth import STATE_DIR_ENV
from noust.web.jobs import JobManager
from noust.web.permissions.registry import route_map
from noust.web.server import create_app
from tests.support.fleet_views_support import SCHEMA_30, SCHEMA_31, Central, Node, build_central
from tests.support.web_auth import make_config
from tests.support.web_central import sign_in

NAMES = ("web-1", "web-2", "old-1")

#: What a 4.2 server offers beyond a 4.1 one.
SOFTWARE_PATHS: dict[str, Any] = {"/api/server/software": {"get": {}}}

ITEMS = [
    {"id": "nginx", "installed": True, "origin": "noust", "severity": "critical_while"},
    {"id": "certbot", "installed": True, "origin": "unknown", "severity": "critical_while"},
    {"id": "git", "installed": True, "origin": "unknown", "severity": "never"},
    {"id": "docker", "installed": False, "origin": None, "severity": "critical_while"},
]


@pytest.fixture
def central(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Central]:
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(STATE_DIR_ENV, str(state))
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    Config.reset_instance()
    built = build_central(tmp_path / "fleet", NAMES)
    monkeypatch.setattr("noust.fleet.nodes.NodeManager", lambda *a, **k: built.manager)
    monkeypatch.setattr(
        "noust.core.update_checker.UpdateChecker.enabled", classmethod(lambda cls: False)
    )
    JobManager.reset_instance()
    built.nodes["old-1"].schema = dict(SCHEMA_30)
    for name in NAMES:
        built.store.set_node_access(name, "admin", True)
    yield built
    JobManager.reset_instance()
    set_aggregator(None)
    Config.reset_instance()
    NoustStore.reset_instance()


def serve_software(node: Node, items: list[dict[str, Any]], *, managed: bool = True) -> None:
    node.schema = {**SCHEMA_31, **SOFTWARE_PATHS}

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "managed": managed,
                "reason": None if managed else "An image-based system",
                "family": "apt" if managed else "none",
                "items": items,
            },
        )

    node.handlers[("GET", "/api/server/software")] = answer


@pytest.fixture
def client(central: Central, sandbox: Path, runner: FakeRunner) -> TestClient:
    serve_software(central.nodes["web-1"], ITEMS)
    serve_software(central.nodes["web-2"], ITEMS[:1], managed=False)
    return TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))


class TestTheView:
    def test_the_route_is_the_fleets_to_read_and_the_resource_is_known(self) -> None:
        assert route_map()[("GET", "/api/fleet/software")] == "fleet.read"
        assert "software" in RESOURCES

    def test_every_server_shows_how_much_of_its_software_is_there_and_whose(
        self, client: TestClient
    ) -> None:
        sign_in(client)

        response = client.get("/api/fleet/software", params={"refresh": "true"})

        assert response.status_code == 200, response.text
        rows = {row["node"]: row for row in response.json()["items"]}
        web1 = rows["web-1"]
        assert (web1["installed"], web1["total"], web1["by_noust"]) == (3, 4, 1)
        assert web1["managed"] is True and web1["family"] == "apt"
        assert web1["page"] == "/server/software" and web1["href"]
        assert rows["web-2"]["managed"] is False
        assert rows["web-2"]["reason"] == "An image-based system"

    def test_a_server_of_before_4_2_is_unsupported_not_empty(self, client: TestClient) -> None:
        sign_in(client)

        body = client.get("/api/fleet/software", params={"refresh": "true"}).json()

        outcomes = {n["name"]: n["status"] for n in body["nodes"]}
        assert outcomes["old-1"] == "unsupported"
        assert outcomes["web-1"] == "ok"
        assert "old-1" not in {row["node"] for row in body["items"]}

    def test_a_server_that_answers_404_is_unsupported_too(
        self, client: TestClient, central: Central
    ) -> None:
        # A 4.1 server whose schema the central cannot read, asked anyway.
        central.nodes["web-2"].handlers[("GET", "/api/server/software")] = lambda request: (
            httpx.Response(404, content=json.dumps({"detail": "Not Found"}).encode())
        )
        sign_in(client)

        body = client.get("/api/fleet/software", params={"refresh": "true"}).json()

        outcomes = {n["name"]: n["status"] for n in body["nodes"]}
        assert outcomes["web-2"] == "unsupported"
