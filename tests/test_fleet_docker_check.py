# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Looking for new versions of the images in use on every server of the fleet (item 189).

The look exists on each server (``POST /api/docker/images/check``: the registries asked with the accounts
that server keeps, as a job); the central only asks every server for it through its tunnel and puts the
results side by side. That is a bulk action of the fleet engine like the others, so what is pinned here is
what the engine does not do for itself: the servers that cannot take part are shown as that (one that runs
a Noust before 4.3, one that has no Docker), each one's job is followed and what it found is kept in short,
the sudo mode is the one each server's own schema asks, and the route has the permission that changing
Docker has on a server and not another.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.fleet.aggregate import set_aggregator
from noust.web import fleet_jobs
from noust.web.auth import STATE_DIR_ENV
from noust.web.fleet_jobs import FleetRequest, plan
from noust.web.jobs import JobManager
from noust.web.permissions.registry import route_map
from noust.web.server import create_app
from tests.support.fleet_actions import ADMIN, _run, _states
from tests.support.fleet_views_support import SCHEMA_31, Central, Node, build_central
from tests.support.web_auth import make_config
from tests.support.web_central import elevate, sign_in

CHECK = "/api/docker/images/check"
SUMMARY = "/api/docker/summary"

#: What a 4.3 server offers of the zone, as its own OpenAPI schema spells it.
SCHEMA_43 = {
    **SCHEMA_31,
    CHECK: {"post": {"x-noust-requires-elevation": True}},
    SUMMARY: {"get": {}},
}

FOUND = {
    "checked_at": "2026-11-14T10:00:00+00:00",
    "records": [
        {"reference": "nginx:1.27", "state": "newer", "change": "minor"},
        {"reference": "postgres:16", "state": "newer", "change": "major"},
        {"reference": "redis:7", "state": "current"},
        {"reference": "mystery:1", "state": "unknown"},
    ],
    "skipped": [{"reference": "shop-web:dev", "why": "built_here"}],
}


def docker_answers(node: Node, *, available: bool = True, result: Any = FOUND) -> None:
    node.schema = dict(SCHEMA_43)
    node.handlers[("GET", SUMMARY)] = lambda request: httpx.Response(
        200, json={"available": available}
    )
    node.handlers[("POST", CHECK)] = lambda request: node.queue(result=result)


@pytest.fixture
def central(tmp_path: Path) -> Iterator[Central]:
    built = build_central(tmp_path, ("web-1", "web-2", "web-3"))
    docker_answers(built.nodes["web-1"])
    docker_answers(built.nodes["web-2"])
    # A server that runs a Noust before 4.3 does not offer the look.
    built.nodes["web-3"].schema = dict(SCHEMA_31)
    yield built
    set_aggregator(None)
    NoustStore.reset_instance()


def bodies(node: Node) -> list[dict[str, Any]]:
    return [
        json.loads(r.content or b"{}")
        for r in node.requests
        if r.url.path == CHECK and r.method == "POST"
    ]


def request(**options: Any) -> FleetRequest:
    return FleetRequest("docker_images_check", nodes=["web-1", "web-2", "web-3"], options=options)


class TestThePlan:
    def test_a_server_before_4_3_is_shown_as_unable_to_take_part(self, central: Central) -> None:
        described = plan(request(), manager=central.manager, asker=ADMIN).to_dict()

        assert _states(described)["web-3"] == ("skipped", "unsupported")
        assert f"does not offer POST {CHECK}" in described["nodes"][2]["step"]
        assert described["batches"] == [["web-1", "web-2"]]

    def test_sudo_mode_is_what_the_servers_schema_says(self, central: Central) -> None:
        assert plan(request(), manager=central.manager, asker=ADMIN).requires_elevation is True

    def test_a_server_with_no_docker_has_nothing_to_look_at(self, central: Central) -> None:
        docker_answers(central.nodes["web-2"], available=False)

        described = plan(request(), manager=central.manager, asker=ADMIN).to_dict()

        assert _states(described)["web-2"] == ("skipped", "not_needed")
        assert "no Docker" in described["nodes"][1]["step"]

    def test_the_options_are_checked(self, central: Central) -> None:
        from noust.core.exceptions import ValidationError

        with pytest.raises(ValidationError, match="Unknown option"):
            plan(request(rm=True), manager=central.manager, asker=ADMIN)
        with pytest.raises(ValidationError, match="pull must be bool"):
            plan(request(pull="yes"), manager=central.manager, asker=ADMIN)


class TestTheRun:
    def test_each_server_is_asked_by_its_own_api_as_the_operator_and_its_job_is_followed(
        self, central: Central
    ) -> None:
        snapshot, _recorder, _job = _run(central, request())

        assert snapshot["status"] == "succeeded"
        assert snapshot["summary"]["succeeded"] == 2
        for name in ("web-1", "web-2"):
            node = central.nodes[name]
            assert bodies(node) == [{"pull": False}]
            asked = next(r for r in node.requests if r.url.path == CHECK)
            assert asked.headers["X-Noust-Elevated"] == "1"
            assert asked.headers["X-Noust-Actor-Role"] == "admin"
        assert bodies(central.nodes["web-3"]) == []

    def test_downloading_to_compare_is_passed_on_and_is_off_by_default(
        self, central: Central
    ) -> None:
        _run(central, request(pull=True))

        assert bodies(central.nodes["web-1"]) == [{"pull": True}]

    def test_what_each_found_is_kept_in_short_beside_the_servers_own_job(
        self, central: Central
    ) -> None:
        snapshot, _recorder, _job = _run(central, request())

        item = next(n for n in snapshot["nodes"] if n["node"] == "web-1")["items"][0]
        assert item["state"] == "succeeded"
        assert (item["looked"], item["newer"], item["unknown"], item["skipped"]) == (4, 2, 1, 1)
        assert item["checked_at"] == FOUND["checked_at"]
        assert item["newer_references"] == ["nginx:1.27", "postgres:16"]
        assert next(n for n in snapshot["nodes"] if n["node"] == "web-1")["node_jobs"] == ["j001"]

    def test_a_server_whose_look_failed_keeps_its_own_words(self, central: Central) -> None:
        node = central.nodes["web-2"]
        node.handlers[("POST", CHECK)] = lambda request: node.queue(
            status="failed", error="Docker did not answer\ndaemon is down"
        )

        snapshot, _recorder, _job = _run(central, request())

        states = _states(snapshot)
        assert states["web-1"][0] == "succeeded" and states["web-2"][0] == "failed"
        failed = next(n for n in snapshot["nodes"] if n["node"] == "web-2")
        assert "Docker did not answer" in str(failed["error"])

    def test_a_server_that_does_not_answer_is_unreachable_and_the_others_are_looked_at(
        self, central: Central
    ) -> None:
        central.nodes["web-2"].down = True

        snapshot, _recorder, _job = _run(central, request())

        assert _states(snapshot)["web-1"][0] == "succeeded"
        assert _states(snapshot)["web-2"][0] == "unreachable"


class TestTheRoute:
    @pytest.fixture
    def client(
        self,
        central: Central,
        tmp_path: Path,
        sandbox: Path,
        runner: FakeRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> Iterator[TestClient]:
        state = tmp_path / "state"
        state.mkdir()
        monkeypatch.setenv(STATE_DIR_ENV, str(state))
        monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
        monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")
        monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
        Config.reset_instance()
        monkeypatch.setattr("noust.fleet.nodes.NodeManager", lambda *a, **k: central.manager)
        monkeypatch.setattr(fleet_jobs, "POLL_SECONDS", 0)
        monkeypatch.setattr(
            "noust.core.update_checker.UpdateChecker.enabled", classmethod(lambda cls: False)
        )
        JobManager.reset_instance()
        yield TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))
        JobManager.reset_instance()
        Config.reset_instance()

    def wait(self, client: TestClient, job_id: str) -> dict[str, Any]:
        for _ in range(400):
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in ("completed", "failed"):
                return job
            time.sleep(0.05)
        raise AssertionError(f"job {job_id} did not finish")

    def test_it_asks_for_what_changing_docker_asks_for_on_a_server(self) -> None:
        assert route_map()[("POST", "/api/fleet/docker/images/check")] == "docker.manage"

    def test_the_action_is_in_the_catalog_of_the_fleet(self, client: TestClient) -> None:
        sign_in(client)

        names = {a["name"] for a in client.get("/api/fleet/actions").json()["actions"]}

        assert "docker_images_check" in names

    def test_a_plan_asks_no_registry_and_says_who_would_be_asked(
        self, client: TestClient, central: Central
    ) -> None:
        sign_in(client)

        response = client.post("/api/fleet/docker/images/check", json={"plan": True})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["job"] is None
        assert body["plan"]["batches"] == [["web-1", "web-2"]]
        assert bodies(central.nodes["web-1"]) == []

    def test_sudo_mode_is_asked_because_each_servers_own_schema_marks_its_look_as_needing_it(
        self, client: TestClient, central: Central
    ) -> None:
        master = sign_in(client)

        refused = client.post("/api/fleet/docker/images/check", json={})

        assert refused.status_code == 403
        assert refused.json()["error"] == "elevation_required"
        assert bodies(central.nodes["web-1"]) == []
        elevate(client, master)
        accepted = client.post("/api/fleet/docker/images/check", json={})
        assert accepted.status_code == 200, accepted.text
        self.wait(client, accepted.json()["job"]["job_id"])

    def test_it_is_a_job_with_a_result_for_each_server(
        self, client: TestClient, central: Central
    ) -> None:
        master = sign_in(client)
        elevate(client, master)

        response = client.post("/api/fleet/docker/images/check", json={"pull": False})

        assert response.status_code == 200, response.text
        job = self.wait(client, response.json()["job"]["job_id"])
        assert job["status"] == "completed", job
        assert bodies(central.nodes["web-2"]) == [{"pull": False}]
        states = {n["node"]: n["state"] for n in job["result"]["nodes"]}
        # The server of a Noust before 4.3 is in the record as a server that did not take part.
        assert states == {"web-1": "succeeded", "web-2": "succeeded", "web-3": "skipped"}

    def test_it_can_be_aimed_at_some_servers(self, client: TestClient, central: Central) -> None:
        sign_in(client)

        response = client.post(
            "/api/fleet/docker/images/check", json={"nodes": ["web-2"], "plan": True}
        )

        assert response.json()["plan"]["batches"] == [["web-2"]]

    def test_with_no_server_that_can_take_part_it_says_so(
        self, client: TestClient, central: Central
    ) -> None:
        master = sign_in(client)
        elevate(client, master)

        response = client.post("/api/fleet/docker/images/check", json={"nodes": ["web-3"]})

        assert response.status_code == 400
        assert "No selected server can run this action" in response.text

    def test_a_server_that_is_not_the_centrals_is_a_400(
        self, client: TestClient, central: Central
    ) -> None:
        sign_in(client)

        response = client.post(
            "/api/fleet/docker/images/check", json={"nodes": ["nope"], "plan": True}
        )

        assert response.status_code == 400
