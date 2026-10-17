# Copyright (c) 2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which server builds an application for the others, and shipping its build (checklist A-49).

The engine is ``test_fleet_ship.py``'s. What is pinned here is the record (one builder per
application, a server of this central, gone with its node), the API and the terminal over it, the
plan before anything runs, and that the whole thing goes through the real application: a ship
queued over HTTP builds on the builder and ends on every server.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.config import Config
from noust.core.exceptions import NodeError, ValidationError
from noust.core.runner import FakeRunner, set_runner
from noust.core.store import NoustStore
from noust.fleet.aggregate import set_aggregator
from noust.fleet.builders import Builders
from noust.fleet.placements import Placements
from noust.web import fleet_jobs
from noust.web.auth import STATE_DIR_ENV
from noust.web.jobs import JobManager
from noust.web.permissions.registry import route_map
from noust.web.server import create_app, get_token_manager
from tests.fleet_views_support import Central, build_central
from tests.test_fleet_ship import DOMAIN, PAYLOAD, RELEASE, Builder, Target
from tests.test_web_auth import make_config
from tests.test_web_central import sign_in

NAMES = ("build-1", "web-1", "web-2")


@pytest.fixture(autouse=True)
def default_runner_after_a_rehearsal() -> Iterator[None]:
    yield
    set_runner(None)


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
    monkeypatch.setattr(fleet_jobs, "POLL_SECONDS", 0)
    monkeypatch.setattr(
        "noust.core.update_checker.UpdateChecker.enabled", classmethod(lambda cls: False)
    )
    JobManager.reset_instance()
    yield built
    JobManager.reset_instance()
    set_aggregator(None)
    Config.reset_instance()
    NoustStore.reset_instance()


@pytest.fixture
def builder(central: Central) -> Builder:
    return Builder(central.nodes["build-1"])


@pytest.fixture
def targets(central: Central) -> dict[str, Target]:
    return {name: Target(central.nodes[name]) for name in ("web-1", "web-2")}


class TestTheRecord:
    def test_a_builder_is_chosen_read_and_forgotten(self, central: Central) -> None:
        builders = Builders(central.store)

        saved = builders.set(DOMAIN, "build-1", actor="ana")

        assert saved.node == "build-1" and saved.created_by == "ana"
        assert builders.get(DOMAIN) == saved
        assert [b.domain for b in builders.list()] == [DOMAIN]
        assert builders.clear(DOMAIN) is True
        assert builders.get(DOMAIN) is None and builders.clear(DOMAIN) is False

    def test_choosing_another_keeps_who_first_chose(self, central: Central) -> None:
        builders = Builders(central.store)
        first = builders.set(DOMAIN, "build-1", actor="ana")

        second = builders.set(DOMAIN, "web-1", actor="bob")

        assert second.node == "web-1"
        assert second.created_by == "ana" and second.created_at == first.created_at
        assert len(builders.list()) == 1

    def test_the_builder_is_a_server_of_this_central(self, central: Central) -> None:
        with pytest.raises(NodeError, match="No node named ghost"):
            Builders(central.store).set(DOMAIN, "ghost")

    @pytest.mark.parametrize("domain", ["https://shop.example.com", "shop.example.com/x", "x y"])
    def test_the_domain_is_a_bare_domain(self, central: Central, domain: str) -> None:
        with pytest.raises(ValidationError):
            Builders(central.store).set(domain, "build-1")

    def test_a_server_that_leaves_the_fleet_takes_its_builds_with_it(
        self, central: Central
    ) -> None:
        builders = Builders(central.store)
        builders.set(DOMAIN, "build-1")
        builders.set("blog.example.com", "web-1")

        central.store.delete_node("build-1")

        assert [b.domain for b in builders.list()] == ["blog.example.com"]


class TestTheApi:
    @pytest.fixture
    def client(self, central: Central, sandbox: Path, runner: FakeRunner) -> TestClient:
        return TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))

    def test_reading_is_a_readers_and_changing_an_administrators(self) -> None:
        routes = route_map()

        assert routes[("GET", "/api/fleet/builders")] == "fleet.read"
        assert routes[("GET", "/api/fleet/builders/{domain}")] == "fleet.read"
        for method, path in (
            ("PUT", "/api/fleet/builders/{domain}"),
            ("DELETE", "/api/fleet/builders/{domain}"),
            ("POST", "/api/fleet/placements/{domain}/ship"),
        ):
            assert routes[(method, path)] == "fleet.manage", path

    def test_a_builder_is_recorded_listed_and_forgotten(self, client: TestClient) -> None:
        sign_in(client)

        saved = client.put(f"/api/fleet/builders/{DOMAIN}", json={"node": "build-1"})
        listed = client.get("/api/fleet/builders")
        read = client.get(f"/api/fleet/builders/{DOMAIN}")

        assert saved.status_code == 200, saved.text
        assert saved.json()["node"] == "build-1"
        assert [b["domain"] for b in listed.json()["builders"]] == [DOMAIN]
        assert read.json()["node"] == "build-1"
        assert client.delete(f"/api/fleet/builders/{DOMAIN}").status_code == 200
        assert client.get("/api/fleet/builders").json()["builders"] == []
        assert client.get(f"/api/fleet/builders/{DOMAIN}").status_code == 404
        assert client.delete(f"/api/fleet/builders/{DOMAIN}").status_code == 404

    def test_a_server_that_does_not_exist_is_refused_by_name(self, client: TestClient) -> None:
        sign_in(client)

        response = client.put(f"/api/fleet/builders/{DOMAIN}", json={"node": "ghost"})

        assert response.status_code == 400 and "ghost" in response.text

    def test_a_read_token_cannot_choose_one(self, client: TestClient) -> None:
        sign_in(client)
        issued = get_token_manager().create_api_token("reader", "read")

        response = client.put(
            f"/api/fleet/builders/{DOMAIN}",
            json={"node": "build-1"},
            headers={"Authorization": f"Bearer {issued['token']}"},
        )

        assert response.status_code == 403

    def test_choosing_and_forgetting_are_audited(self, client: TestClient) -> None:
        from noust.core import audit

        sign_in(client)
        client.put(f"/api/fleet/builders/{DOMAIN}", json={"node": "build-1"})
        client.delete(f"/api/fleet/builders/{DOMAIN}")

        names = {event["action"] for event in audit.get_log().read(limit=200)}
        assert "fleet.builder.set" in names and "fleet.builder.clear" in names

    def wait(self, client: TestClient, job_id: str) -> dict[str, Any]:
        for _ in range(400):
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in ("completed", "failed"):
                return job
            time.sleep(0.05)
        raise AssertionError(f"job {job_id} did not finish")

    def place(self, client: TestClient, *nodes: str) -> None:
        sign_in(client)
        response = client.put(
            f"/api/fleet/placements/{DOMAIN}", json={"nodes": list(nodes), "labels": {}}
        )
        assert response.status_code == 200, response.text

    def test_shipping_plans_the_first_server_alone_and_runs_nothing(
        self, client: TestClient, builder: Builder, targets: dict[str, Target]
    ) -> None:
        self.place(client, "web-1", "web-2")

        response = client.post(
            f"/api/fleet/placements/{DOMAIN}/ship", json={"plan": True, "builder": "build-1"}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["job"] is None
        assert body["plan"]["batches"][0] == ["web-1"]
        assert builder.builds == []
        assert targets["web-1"].uploads == []

    def test_shipping_uses_the_recorded_builder_and_ends_on_every_server(
        self, client: TestClient, builder: Builder, targets: dict[str, Target]
    ) -> None:
        self.place(client, "web-1", "web-2")
        client.put(f"/api/fleet/builders/{DOMAIN}", json={"node": "build-1"})

        response = client.post(f"/api/fleet/placements/{DOMAIN}/ship", json={})

        assert response.status_code == 200, response.text
        job = self.wait(client, response.json()["job"]["job_id"])
        assert job["status"] == "completed", job
        assert len(builder.builds) == 1
        assert {t.commit for t in targets.values()} == {targets["web-1"].commit}
        assert all(bytes(t.received) == PAYLOAD for t in targets.values())
        states = {n["node"]: n["state"] for n in job["result"]["nodes"]}
        assert states == {"web-1": "succeeded", "web-2": "succeeded"}
        item = job["result"]["nodes"][0]["items"][0]
        assert item["bundle"]["release_id"] == RELEASE and item["bundle"]["built_on"] == "build-1"

    def test_without_a_builder_it_says_so(
        self, client: TestClient, builder: Builder, targets: dict[str, Target]
    ) -> None:
        self.place(client, "web-1", "web-2")

        response = client.post(f"/api/fleet/placements/{DOMAIN}/ship", json={"plan": True})

        assert response.status_code == 400
        assert "Say which server builds" in response.text

    def test_the_builder_among_the_members_is_refused_with_what_to_do(
        self, client: TestClient, builder: Builder, targets: dict[str, Target]
    ) -> None:
        self.place(client, "build-1", "web-1")

        response = client.post(
            f"/api/fleet/placements/{DOMAIN}/ship", json={"plan": True, "builder": "build-1"}
        )

        assert response.status_code == 400
        assert "also a server to ship it to" in response.text

    def test_a_ship_to_only_some_members(
        self, client: TestClient, builder: Builder, targets: dict[str, Target]
    ) -> None:
        self.place(client, "web-1", "web-2")

        response = client.post(
            f"/api/fleet/placements/{DOMAIN}/ship",
            json={"plan": True, "builder": "build-1", "to": ["web-2"]},
        )

        assert response.json()["plan"]["batches"][0] == ["web-2"]

    def test_an_application_that_is_not_placed_is_a_404(self, client: TestClient) -> None:
        sign_in(client)

        assert client.post(f"/api/fleet/placements/{DOMAIN}/ship", json={}).status_code == 404


class TestTheTerminal:
    def invoke(self, *args: str):
        return CliRunner().invoke(root_cli, ["fleet", *args])

    def test_a_builder_is_chosen_shown_listed_and_cleared(self, central: Central) -> None:
        chosen = self.invoke("builder", "set", DOMAIN, "build-1")
        shown = json.loads(self.invoke("builder", "show", DOMAIN, "--json").output)
        listed = json.loads(self.invoke("builder", "list", "--json").output)
        table = self.invoke("builder", "list")
        cleared = self.invoke("builder", "clear", DOMAIN)

        assert chosen.exit_code == 0, chosen.output
        assert "noust fleet placement ship" in chosen.output
        assert shown["node"] == "build-1"
        assert listed["builders"][0]["domain"] == DOMAIN
        assert DOMAIN in table.output and "build-1" in table.output
        assert cleared.exit_code == 0
        assert json.loads(self.invoke("builder", "list", "--json").output)["builders"] == []

    def test_showing_one_that_is_not_recorded_says_how_to_choose(self, central: Central) -> None:
        result = self.invoke("builder", "show", DOMAIN)

        assert isinstance(result.exception, ValidationError)
        assert f"noust fleet builder set {DOMAIN}" in (result.exception.details or "")

    def test_a_rehearsal_chooses_nothing(self, central: Central) -> None:
        result = CliRunner().invoke(
            root_cli, ["--dry-run", "fleet", "builder", "set", DOMAIN, "build-1"]
        )

        assert result.exit_code == 0, result.output
        assert Builders(central.store).get(DOMAIN) is None

    def test_shipping_shows_the_plan_and_changes_nothing(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        Placements(central.store).save(DOMAIN, nodes=["web-1", "web-2"])

        result = self.invoke(
            "placement", "ship", DOMAIN, "--builder", "build-1", "--plan", "--json"
        )

        assert result.exit_code == 0, result.output
        assert json.loads(result.output.splitlines()[0])["plan"]["batches"][0] == ["web-1"] or (
            "web-1" in result.output
        )
        assert builder.builds == [] and targets["web-1"].uploads == []

    def test_shipping_runs_it_and_exits_clean(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        Placements(central.store).save(DOMAIN, nodes=["web-1", "web-2"])
        Builders(central.store).set(DOMAIN, "build-1")

        result = self.invoke("placement", "ship", DOMAIN, "-y")

        assert result.exit_code == 0, result.output
        assert len(builder.builds) == 1
        assert all(bytes(t.received) == PAYLOAD for t in targets.values())

    def test_a_failed_server_makes_the_exit_status_one(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        Placements(central.store).save(DOMAIN, nodes=["web-1", "web-2"])
        Builders(central.store).set(DOMAIN, "build-1")
        targets["web-2"].fail_receive = "disk full"

        result = self.invoke("placement", "ship", DOMAIN, "-y")

        assert result.exit_code == 1, result.output
