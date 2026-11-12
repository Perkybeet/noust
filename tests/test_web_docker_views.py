# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/views/docker``: the Docker zone of every server, over HTTP, with the central as one more server.

The central is the real application with a signed-in operator; the nodes are the fakes of
:mod:`tests.support.fleet_views_support`. What is pinned is what the console reads: the envelope of
every view, the central as one of the servers, a node before 4.3 as an outcome with a sentence (a 404 of
its own, never an error of the view), and who may ask: the zone is the machine's, so a person of a team is
refused it whatever role they hold, and a credential that does not read Docker is refused.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from noust.central import machine
from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.fleet import docker as fleet_docker
from noust.fleet.aggregate import set_aggregator
from noust.fleet.models import central_name
from noust.web.jobs import JobManager
from noust.web.server import create_app, get_token_manager
from tests.support.fleet_views_support import Central, build_central
from tests.support.teams_support import PASSWORD, World, build_world
from tests.support.web_auth import make_config
from tests.support.web_central import sign_in

ROUTES = {
    "summary": "docker_summary",
    "attention": "docker_attention",
    "engine": "docker_engine",
    "stacks": "docker_stacks",
    "containers": "docker_containers",
    "images": "docker_images",
    "volumes": "docker_volumes",
    "networks": "docker_networks",
}

ENVELOPE = {
    "resource",
    "generated_at",
    "partial",
    "fleet",
    "servers",
    "nodes",
    "refusals",
    "items",
}


@pytest.fixture
def central(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Central]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    Config.reset_instance()
    built = build_central(tmp_path / "fleet", ("web-2", "web-3"), web_3="4.2.1")
    monkeypatch.setattr("noust.fleet.nodes.NodeManager", lambda *a, **k: built.manager)
    monkeypatch.setattr(
        "noust.core.update_checker.UpdateChecker.enabled", classmethod(lambda cls: False)
    )
    JobManager.reset_instance()
    built.nodes["web-2"].responses["/api/docker/containers"] = (
        200,
        {
            "available": True,
            "items": [
                {
                    "id": "a" * 64,
                    "name": "portainer",
                    "state_kind": "running",
                    "owner": {"kind": "nobody", "subject": "", "project": ""},
                }
            ],
            "total": 1,
        },
    )
    machine.reset_machine_facts()
    yield built
    machine.reset_machine_facts()
    JobManager.reset_instance()
    set_aggregator(None)
    Config.reset_instance()
    NoustStore.reset_instance()


@pytest.fixture
def client(central: Central, sandbox: Path, runner: FakeRunner) -> TestClient:
    return TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))


class TestTheEnvelope:
    @pytest.mark.parametrize("route", sorted(ROUTES))
    def test_every_view_answers_the_same_envelope(self, client: TestClient, route: str) -> None:
        sign_in(client)

        response = client.get(f"/api/views/docker/{route}")

        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == ENVELOPE
        assert body["resource"] == ROUTES[route]
        assert [node["name"] for node in body["nodes"]] == [central_name(), "web-2", "web-3"]
        assert body["fleet"] is True

    def test_the_central_is_one_server_and_a_row_of_its_own(self, client: TestClient) -> None:
        sign_in(client)

        rows = {r["node"]: r for r in client.get("/api/views/docker/summary").json()["items"]}

        assert set(rows) == {central_name(), "web-2", "web-3"}
        assert rows[central_name()]["local"] is True

    def test_a_server_before_43_is_an_outcome_with_a_sentence_and_not_an_error(
        self, client: TestClient
    ) -> None:
        sign_in(client)

        body = client.get("/api/views/docker/containers").json()

        outcome = next(n for n in body["nodes"] if n["name"] == "web-3")
        assert outcome["status"] == "unsupported" and outcome["code"] == "not_offered"
        assert outcome["version"] == "4.2.1"
        assert "/api/docker/containers" in outcome["message"]
        assert "older Noust" in outcome["hint"]
        assert [(r["node"], r["name"]) for r in body["items"]] == [("web-2", "portainer")]

    def test_naming_a_server_asks_only_it(self, client: TestClient, central: Central) -> None:
        sign_in(client)

        body = client.get("/api/views/docker/containers", params={"node": "web-2"}).json()

        assert [n["name"] for n in body["nodes"]] == ["web-2"]
        assert not central.nodes["web-3"].requests

    def test_a_row_per_server_is_asked_a_bounded_page_with_its_filters(
        self, client: TestClient, central: Central
    ) -> None:
        sign_in(client)

        client.get(
            "/api/views/docker/containers",
            params={"limit": 50, "owner": "nobody", "search": "port"},
        )

        asked = [
            dict(r.url.params)
            for r in central.nodes["web-2"].requests
            if r.url.path == "/api/docker/containers"
        ]
        assert asked == [{"limit": "50", "owner": "nobody", "search": "port"}]

    @pytest.mark.parametrize("limit", [0, 1001])
    def test_a_page_that_makes_no_sense_is_refused(self, client: TestClient, limit: int) -> None:
        sign_in(client)

        assert client.get("/api/views/docker/containers", params={"limit": limit}).status_code in (
            400,
            422,
        )

    def test_the_default_page_is_the_one_the_view_names(self) -> None:
        assert (fleet_docker.DEFAULT_LIMIT, fleet_docker.MAX_LIMIT) == (200, 1000)


class TestWhoMayAsk:
    def test_a_credential_that_does_not_read_docker_is_refused(self, client: TestClient) -> None:
        tokens = get_token_manager()
        owner = tokens.accounts.create("apps-only", "admin", password=PASSWORD)
        issued = tokens.create_api_token(
            "apps-only", "read", None, owner=owner, permissions=["apps.read"]
        )

        response = client.get(
            "/api/views/docker/summary", headers={"Authorization": f"Bearer {issued['token']}"}
        )

        assert response.status_code == 403
        assert response.json()["error"] == "permission_denied"

    def test_a_viewer_of_the_whole_server_reads_it(self, client: TestClient) -> None:
        issued = get_token_manager().create_api_token("reader", "read")

        response = client.get(
            "/api/views/docker/summary", headers={"Authorization": f"Bearer {issued['token']}"}
        )

        assert response.status_code == 200


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> Iterator[World]:
    built = build_world(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


class TestAPersonOfATeam:
    @pytest.mark.parametrize("who", ["alice", "dana", "omar", "bob"])
    def test_is_refused_every_view_of_the_zone_whatever_role_it_holds(
        self, world: World, who: str
    ) -> None:
        for route in ROUTES:
            response = world.client.get(f"/api/views/docker/{route}", headers=world.as_(who))
            assert response.status_code == 403, route
            assert response.json()["error"] == "instance_scope_required"

    def test_while_the_views_of_its_own_resources_still_answer(self, world: World) -> None:
        assert world.client.get("/api/views/apps", headers=world.as_("alice")).status_code == 200
