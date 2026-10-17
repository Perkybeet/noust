# Copyright (c) 2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/fleet/placements`` over HTTP: where an application runs, how it looks across those
servers, how it is rolled out and copied, and what a load balancer is told (checklist F-18).

The rules are tested in ``test_fleet_placements.py``, ``test_fleet_placement_status.py``,
``test_fleet_placement_rollout.py`` and ``test_fleet_placement_copy.py``; what is pinned here is what
the surface owes: who may look and who may change, the plan before anything runs, sudo mode where a
server's schema asks for it, and that a copy's secrets are never in an answer.
"""

from __future__ import annotations

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
from noust.fleet.placements import Placements
from noust.web.jobs import JobManager
from noust.web.permissions.registry import route_map
from noust.web.server import create_app, get_token_manager
from tests.fleet_views_support import Central, build_central
from tests.placement_support import DOMAIN, SECRET_VALUE, Shop, Upstream
from tests.test_web_auth import make_config
from tests.test_web_central import elevate, sign_in

NAMES = ("web-1", "web-2", "web-3")


@pytest.fixture
def central(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Central]:
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
    yield built
    JobManager.reset_instance()
    set_aggregator(None)
    Config.reset_instance()
    NoustStore.reset_instance()


@pytest.fixture
def client(central: Central, sandbox: Path, runner: FakeRunner) -> TestClient:
    return TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))


@pytest.fixture
def upstream() -> Upstream:
    return Upstream(head="aaa1111")


@pytest.fixture
def shops(central: Central, upstream: Upstream) -> dict[str, Shop]:
    return {name: Shop(central.nodes[name], upstream) for name in NAMES}


def wait(client: TestClient, job_id: str) -> dict[str, Any]:
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


def place(client: TestClient, **body: Any):
    payload = {"nodes": list(NAMES), "labels": {}, **body}
    return client.put(f"/api/fleet/placements/{DOMAIN}", json=payload)


def test_the_routes_are_a_readers_to_look_and_an_administrators_to_change() -> None:
    routes = route_map()

    for method, path in (
        ("GET", "/api/fleet/placements"),
        ("GET", "/api/fleet/placements/{domain}"),
        ("GET", "/api/fleet/placements/{domain}/upstreams"),
    ):
        assert routes[(method, path)] == "fleet.read", path
    for method, path in (
        ("PUT", "/api/fleet/placements/{domain}"),
        ("DELETE", "/api/fleet/placements/{domain}"),
        ("POST", "/api/fleet/placements/{domain}/rollout"),
        ("POST", "/api/fleet/placements/{domain}/copy"),
    ):
        assert routes[(method, path)] == "fleet.manage", path


def test_a_placement_is_recorded_listed_and_forgotten(client, central) -> None:
    sign_in(client)

    saved = place(client, serial=2, canary="web-2")
    listed = client.get("/api/fleet/placements")

    assert saved.status_code == 200, saved.text
    assert saved.json()["domain"] == DOMAIN
    assert saved.json()["members"] == list(NAMES)
    assert listed.json()["placements"][0]["nodes"] == list(NAMES)
    assert listed.json()["placements"][0]["serial"] == 2
    assert client.delete(f"/api/fleet/placements/{DOMAIN}").status_code == 200
    assert client.get("/api/fleet/placements").json()["placements"] == []
    assert client.delete(f"/api/fleet/placements/{DOMAIN}").status_code == 404


def test_a_placement_that_names_nothing_is_a_400(client) -> None:
    sign_in(client)

    response = client.put(f"/api/fleet/placements/{DOMAIN}", json={"nodes": [], "labels": {}})

    assert response.status_code == 400, response.text


def test_a_server_that_does_not_exist_is_refused_by_name(client) -> None:
    sign_in(client)

    response = place(client, nodes=["web-1", "ghost"])

    assert response.status_code == 400
    assert "ghost" in response.text


def test_the_status_compares_the_servers(client, central, shops, upstream) -> None:
    sign_in(client)
    place(client)
    shops["web-3"].commit = "old0000"
    shops["web-3"].node.apps = [shops["web-3"].row()]

    response = client.get(f"/api/fleet/placements/{DOMAIN}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"]["reference_commit"] == "aaa1111"
    assert body["status"]["in_sync"] is False
    assert {s["node"]: s["sync"] for s in body["status"]["servers"]} == {
        "web-1": "current",
        "web-2": "current",
        "web-3": "differs",
    }


def test_the_upstreams_are_written_for_the_balancer(client, central, shops) -> None:
    sign_in(client)
    place(client)

    nginx = client.get(f"/api/fleet/placements/{DOMAIN}/upstreams").json()
    listed = client.get(
        f"/api/fleet/placements/{DOMAIN}/upstreams", params={"format": "list"}
    ).json()

    assert "upstream shop_example_com {" in nginx["text"]
    assert nginx["format"] == "nginx"
    assert len(nginx["servers"]) == 3
    assert listed["text"].count("\n") == 3 and ":443" in listed["text"]
    bad = client.get(f"/api/fleet/placements/{DOMAIN}/upstreams", params={"format": "haproxy"})
    assert bad.status_code in (400, 422)


def test_a_placement_that_is_not_recorded_is_a_404(client) -> None:
    sign_in(client)

    assert client.get(f"/api/fleet/placements/{DOMAIN}").status_code == 404
    assert client.post(f"/api/fleet/placements/{DOMAIN}/rollout", json={}).status_code == 404


def test_a_rollout_plan_shows_the_first_server_and_runs_nothing(client, central, shops) -> None:
    sign_in(client)
    place(client)

    response = client.post(f"/api/fleet/placements/{DOMAIN}/rollout", json={"plan": True})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job"] is None
    assert body["plan"]["batches"][0] == ["web-1"]
    assert all(shop.deploys == [] for shop in shops.values())


def test_the_placements_own_strategy_is_the_default_and_the_request_overrides_it(
    client, central, shops
) -> None:
    sign_in(client)
    place(client, serial=2, canary="web-3", max_failures=1)

    default = client.post(f"/api/fleet/placements/{DOMAIN}/rollout", json={"plan": True}).json()
    chosen = client.post(
        f"/api/fleet/placements/{DOMAIN}/rollout",
        json={"plan": True, "canary": "web-2", "serial": 1},
    ).json()

    assert default["plan"]["strategy"] == {"serial": 2, "max_failures": 1, "canary": "web-3"}
    assert chosen["plan"]["strategy"]["canary"] == "web-2"
    assert chosen["plan"]["strategy"]["serial"] == 1


def test_a_rollout_runs_as_a_job_and_every_server_ends_on_one_commit(
    client, central, shops, upstream
) -> None:
    sign_in(client)
    place(client)
    upstream.head = "bbb2222"

    response = client.post(f"/api/fleet/placements/{DOMAIN}/rollout", json={})

    assert response.status_code == 200, response.text
    job = wait(client, response.json()["job"]["job_id"])
    assert job["status"] == "completed"
    assert {shop.commit for shop in shops.values()} == {"bbb2222"}
    states = {n["node"]: n["state"] for n in job["result"]["nodes"]}
    assert states == dict.fromkeys(NAMES, "succeeded")


def test_a_read_token_cannot_roll_out(client, central, shops) -> None:
    sign_in(client)
    place(client)
    issued = get_token_manager().create_api_token("reader", "read")

    response = client.post(
        f"/api/fleet/placements/{DOMAIN}/rollout",
        json={},
        headers={"Authorization": f"Bearer {issued['token']}"},
    )

    assert response.status_code == 403
    assert all(shop.deploys == [] for shop in shops.values())


def test_a_copy_asks_for_sudo_mode_and_then_runs(client, central, shops, upstream) -> None:
    master = sign_in(client)
    place(client)
    created: list[Shop] = []
    central.nodes["web-3"].apps = []
    central.nodes["web-3"].handlers.pop(("GET", "/api/apps"), None)
    Shop.accept_imports(central.nodes["web-3"], upstream, imported=created)
    body = {"from_node": "web-1", "to": ["web-3"]}

    refused = client.post(f"/api/fleet/placements/{DOMAIN}/copy", json=body)
    assert refused.status_code == 403
    assert refused.json()["error"] == "elevation_required"

    elevate(client, master)
    accepted = client.post(f"/api/fleet/placements/{DOMAIN}/copy", json=body)

    assert accepted.status_code == 200, accepted.text
    job = wait(client, accepted.json()["job"]["job_id"])
    assert job["status"] == "completed", job
    assert len(created) == 1
    assert SECRET_VALUE not in accepted.text + str(job)


def test_a_copy_with_no_destination_goes_to_the_members_that_lack_the_application(
    client, central, shops, upstream
) -> None:
    sign_in(client)
    place(client)
    central.nodes["web-3"].handlers[("GET", "/api/apps")] = lambda request: httpx.Response(
        200, json={"apps": [], "total": 0}
    )

    plan = client.post(
        f"/api/fleet/placements/{DOMAIN}/copy", json={"from_node": "web-1", "plan": True}
    )

    assert plan.status_code == 200, plan.text
    assert [n["node"] for n in plan.json()["plan"]["nodes"]] == ["web-3"]


def test_a_copy_with_nowhere_to_go_is_said(client, central, shops) -> None:
    sign_in(client)
    place(client)

    response = client.post(
        f"/api/fleet/placements/{DOMAIN}/copy", json={"from_node": "web-1", "plan": True}
    )

    assert response.status_code == 400
    assert "already" in response.text.lower()


def test_removing_a_server_from_the_fleet_takes_it_out_of_the_placements(client, central) -> None:
    sign_in(client)
    place(client)

    central.manager.remove("web-2", revoke=False)

    saved = Placements(central.store).get(DOMAIN)
    assert saved is not None and saved.nodes == ("web-1", "web-3")
