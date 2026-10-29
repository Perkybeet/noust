# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Noust DNS on the fleet, through the console's API (4.1, item 104).

The engine is ``test_fleet_dns_cluster.py``'s. What is pinned here is that the route is a client
of it and nothing more: the same plan and the same refusals, a permission on the route, sudo mode
asked because the node marks its key route as needing it, a job to follow whose record never holds
the key, and a view of every server's part in it.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.config import Config
from noust.core.runner import FakeRunner, set_runner
from noust.core.store import NoustStore
from noust.fleet.aggregate import RESOURCES, set_aggregator
from noust.web import fleet_jobs
from noust.web.auth import STATE_DIR_ENV
from noust.web.jobs import JobManager
from noust.web.permissions.registry import route_map
from noust.web.server import create_app
from tests.support.fleet_dns import KEY, DnsNode
from tests.support.fleet_views_support import SCHEMA_30, Central, build_central
from tests.support.web_auth import make_config
from tests.support.web_central import elevate, sign_in

NAMES = ("dns-1", "dns-2", "old-1")
BODY = {
    "primary": {"node": "dns-1", "hostname": "ns1.example.net", "listen": ["203.0.113.10"]},
    "secondaries": [{"node": "dns-2", "hostname": "ns2.example.net"}],
}


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
    built.nodes["old-1"].schema = dict(SCHEMA_30)
    for name in NAMES:
        built.store.set_node_access(name, "admin", True)
    yield built
    JobManager.reset_instance()
    set_aggregator(None)
    Config.reset_instance()
    NoustStore.reset_instance()


@pytest.fixture
def servers(central: Central) -> dict[str, DnsNode]:
    found = {name: DnsNode(central.nodes[name]) for name in ("dns-1", "dns-2")}
    found["dns-2"].listen = ["198.51.100.7"]
    return found


@pytest.fixture
def client(central: Central, sandbox: Path, runner: FakeRunner) -> TestClient:
    return TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))


def wait(client: TestClient, job_id: str) -> dict[str, Any]:
    for _ in range(400):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


class TestTheRoutes:
    def test_the_permissions_are_the_fleets(self) -> None:
        assert route_map()[("POST", "/api/fleet/dns/cluster")] == "fleet.manage"
        assert route_map()[("GET", "/api/fleet/dns")] == "fleet.read"

    def test_it_is_in_the_catalog_of_bulk_actions(self, client: TestClient) -> None:
        sign_in(client)

        names = {a["name"] for a in client.get("/api/fleet/actions").json()["actions"]}

        assert "dns_cluster" in names

    def test_a_plan_installs_nothing_and_says_which_servers_take_part(
        self, client: TestClient, servers: dict[str, DnsNode]
    ) -> None:
        sign_in(client)

        response = client.post("/api/fleet/dns/cluster", json={**BODY, "plan": True})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["job"] is None
        assert body["plan"]["batches"] == [["dns-1"], ["dns-2"]]
        assert body["plan"]["requires_elevation"] is True
        assert all(
            node.bodies.get(("POST", "/api/dns/noust/server")) is None for node in servers.values()
        )

    def test_a_server_without_noust_dns_is_in_the_plan_as_unable_to_take_part(
        self, client: TestClient, servers: dict[str, DnsNode]
    ) -> None:
        sign_in(client)
        body = {
            **BODY,
            "secondaries": [*BODY["secondaries"], {"node": "old-1", "hostname": "ns9.example.net"}],
            "plan": True,
        }

        response = client.post("/api/fleet/dns/cluster", json=body)

        nodes = {n["node"]: n for n in response.json()["plan"]["nodes"]}
        assert nodes["old-1"]["state"] == "skipped" and nodes["old-1"]["reason"] == "unsupported"

    def test_sudo_mode_is_asked_because_the_node_marks_its_key_as_needing_it(
        self, client: TestClient, servers: dict[str, DnsNode]
    ) -> None:
        master = sign_in(client)

        refused = client.post("/api/fleet/dns/cluster", json=BODY)
        assert refused.status_code == 403
        assert refused.json()["error"] == "elevation_required"
        assert servers["dns-1"].calls == []

        elevate(client, master)
        accepted = client.post("/api/fleet/dns/cluster", json=BODY)
        assert accepted.status_code == 200, accepted.text
        wait(client, accepted.json()["job"]["job_id"])

    def test_the_cluster_is_set_up_as_a_job_and_the_key_is_in_nothing_the_console_reads(
        self, client: TestClient, servers: dict[str, DnsNode]
    ) -> None:
        master = sign_in(client)
        elevate(client, master)

        response = client.post("/api/fleet/dns/cluster", json=BODY)

        assert response.status_code == 200, response.text
        job = wait(client, response.json()["job"]["job_id"])
        assert job["status"] == "completed", job
        states = {n["node"]: n["state"] for n in job["result"]["nodes"]}
        assert states == {"dns-1": "succeeded", "dns-2": "succeeded"}
        assert servers["dns-2"].bodies[("POST", "/api/dns/noust/server")][0]["transfer_key"] == {
            "secret": KEY
        }
        assert servers["dns-1"].secondaries == ["198.51.100.7"]
        assert KEY not in response.text and KEY not in json.dumps(job)
        assert KEY not in client.get(f"/api/jobs/{job['id']}/log").text
        assert KEY not in client.get("/api/fleet/jobs").text

    @pytest.mark.parametrize(
        "body",
        [
            {**BODY, "secondaries": [BODY["primary"]]},
            {**BODY, "primary": {"node": "dns-1", "hostname": "ns1"}},
            {"secondaries": []},
        ],
    )
    def test_what_is_not_a_cluster_is_refused_before_a_job_is_queued(
        self, client: TestClient, servers: dict[str, DnsNode], body: dict[str, Any]
    ) -> None:
        sign_in(client)

        response = client.post("/api/fleet/dns/cluster", json=body)

        assert response.status_code in (400, 422)
        assert servers["dns-1"].calls == []


class TestTheView:
    def test_every_server_shows_its_part_and_one_without_noust_dns_is_unsupported(
        self, client: TestClient, servers: dict[str, DnsNode]
    ) -> None:
        sign_in(client)
        servers["dns-1"].role = "primary"
        servers["dns-1"].hostname = "ns1.example.net"
        servers["dns-1"].secondaries = ["198.51.100.7"]

        response = client.get("/api/fleet/dns", params={"refresh": "true"})

        assert response.status_code == 200, response.text
        body = response.json()
        rows = {row["node"]: row for row in body["items"]}
        assert rows["dns-1"]["role"] == "primary" and rows["dns-1"]["configured"] is True
        assert rows["dns-1"]["secondaries"] == ["198.51.100.7"]
        assert rows["dns-2"]["role"] is None and rows["dns-2"]["configured"] is False
        outcomes = {n["name"]: n["status"] for n in body["nodes"]}
        assert outcomes["old-1"] == "unsupported"
        assert outcomes["dns-1"] == "ok"
        assert rows["dns-1"]["page"] == "/domains/dns" and rows["dns-1"]["href"]

    def test_the_resource_is_one_the_aggregator_knows(self) -> None:
        assert "dns" in RESOURCES
