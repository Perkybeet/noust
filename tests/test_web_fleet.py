"""
``/api/fleet`` over HTTP: the views, the actions, the labels, on the real application.

The central is the real application with a signed-in operator; the nodes are
the fakes of :mod:`tests.fleet_views_support` behind the fleet's own client.
What is pinned: every view answers 200 whatever the nodes do, with the
central's own rows read through its own API as the operator; who may look and
who may act; sudo mode asked when a node's schema says so; an action planned,
run as a job and recorded per node; a retry; labels.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.fleet import aggregate
from noust.fleet.aggregate import set_aggregator
from noust.fleet.models import central_name
from noust.web.jobs import JobManager
from noust.web.server import create_app, get_token_manager
from tests.fleet_views_support import Central, build_central
from tests.test_web_auth import make_config
from tests.test_web_central import elevate, sign_in


@pytest.fixture
def central(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Central]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    Config.reset_instance()
    built = build_central(tmp_path / "fleet")
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


def _wait(client: TestClient, job_id: str) -> dict[str, Any]:
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


class TestViews:
    def test_apps_include_the_central_itself_and_every_node(self, client, central):
        sign_in(client)
        central.nodes["web-2"].apps = [{"domain": "shop.example.com", "status": "running"}]

        response = client.get("/api/fleet/apps")

        assert response.status_code == 200, response.text
        body = response.json()
        names = [node["name"] for node in body["nodes"]]
        assert names == [central_name(), "web-2", "web-3"]
        local = body["nodes"][0]
        assert local["local"] is True and local["status"] == "ok", local
        assert [(row["node"], row["href"]) for row in body["items"]] == [
            ("web-2", "/n/web-2/apps/shop.example.com")
        ]
        asked = central.nodes["web-2"].requests[-1]
        assert asked.headers["X-Noust-Actor-Scope"] == "admin"
        assert asked.headers["X-Noust-Actor"]

    def test_a_hub_has_no_rows_of_its_own(self, client, central, monkeypatch):
        monkeypatch.setattr(aggregate, "includes_central", lambda: False)
        sign_in(client)

        body = client.get("/api/fleet/apps").json()

        assert [node["name"] for node in body["nodes"]] == ["web-2", "web-3"]

    def test_a_node_down_and_a_node_too_old_are_200_with_their_outcomes(self, client, central):
        sign_in(client)
        central.nodes["web-3"].down = True

        response = client.get("/api/fleet/summary", params={"node": ["web-2", "web-3"]})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["partial"] is True
        outcomes = {node["name"]: node for node in body["nodes"]}
        assert outcomes["web-2"]["status"] == "unsupported"
        assert "/api/overview" in outcomes["web-2"]["missing"]
        assert outcomes["web-3"]["status"] == "unreachable"
        assert [row["node"] for row in body["items"]] == ["web-2", "web-3"]

    def test_a_read_token_looks_but_does_not_act(self, client, central):
        issued = get_token_manager().create_api_token("reader", "read")
        headers = {"Authorization": f"Bearer {issued['token']}"}

        assert client.get("/api/fleet/certificates", headers=headers).status_code == 200
        acted = client.post(
            "/api/fleet/actions",
            json={"action": "certs_renew", "targets": {"nodes": ["web-2"]}},
            headers=headers,
        )
        assert acted.status_code == 403
        labelled = client.put(
            "/api/fleet/servers/web-2/labels", json={"labels": {"env": "prod"}}, headers=headers
        )
        assert labelled.status_code == 403


class TestActions:
    def test_the_catalog(self, client):
        sign_in(client)

        actions = client.get("/api/fleet/actions").json()["actions"]

        assert {action["name"] for action in actions} == {
            "certs_renew",
            "backups_run",
            "backups_verify",
            "apps_update",
            "apps_restart",
            "noust_update",
            "os_updates",
        }

    def test_a_plan_runs_nothing(self, client, central):
        sign_in(client)

        response = client.post(
            "/api/fleet/actions",
            json={"action": "certs_renew", "targets": {"nodes": ["web-2", "web-3"]}, "plan": True},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["job"] is None
        assert body["plan"]["batches"] == [["web-2", "web-3"]]
        assert not any(r.method == "POST" for r in central.nodes["web-2"].requests)

    def test_sudo_mode_is_asked_when_a_nodes_schema_says_so(self, client, central):
        master = sign_in(client)
        for node in central.nodes.values():
            node.responses["/api/system/version"] = (
                200,
                {"current_version": "3.1.0", "update_state": "update_available"},
            )
        body = {"action": "noust_update", "targets": {"nodes": ["web-2"]}}

        refused = client.post("/api/fleet/actions", json=body)
        assert refused.status_code == 403
        assert refused.json()["error"] == "elevation_required"

        elevate(client, master)
        central.nodes["web-2"].handlers[("POST", "/api/system/update")] = lambda request: (
            central.nodes["web-2"].queue(status="failed", error="refused")
        )
        accepted = client.post("/api/fleet/actions", json=body)
        assert accepted.status_code == 200, accepted.text
        _wait(client, accepted.json()["job"]["job_id"])
        posted = next(
            r
            for r in central.nodes["web-2"].requests
            if r.url.path == "/api/system/update" and r.method == "POST"
        )
        assert posted.headers["X-Noust-Elevated"] == "1"

    def test_run_record_and_retry(self, client, central):
        sign_in(client)
        for name, node in central.nodes.items():
            fails = name == "web-3"
            node.handlers[("POST", "/api/certs/renew-all")] = (
                lambda request, node=node, fails=fails: node.queue(
                    status="failed" if fails else "completed", error="certbot: DNS problem"
                )
            )

        response = client.post(
            "/api/fleet/actions",
            json={
                "action": "certs_renew",
                "targets": {"nodes": ["web-2", "web-3"]},
                "strategy": {"serial": 1, "max_failures": -1},
            },
        )
        assert response.status_code == 200, response.text
        job_id = response.json()["job"]["job_id"]
        job = _wait(client, job_id)

        assert job["status"] == "failed"
        assert job["type"] == "fleet"
        states = {node["node"]: node["state"] for node in job["result"]["nodes"]}
        assert states == {"web-2": "succeeded", "web-3": "failed"}

        record = client.get(f"/api/fleet/jobs/{job_id}").json()
        assert record["status"] == "failed"
        assert "DNS problem" in record["nodes"][1]["output"]
        assert client.get("/api/fleet/jobs").json()["jobs"][0]["job_id"] == job_id

        retried = client.post(f"/api/fleet/jobs/{job_id}/retry", params={"plan": True}).json()
        assert [node["node"] for node in retried["plan"]["nodes"]] == ["web-3"]

    def test_an_unknown_job_is_404(self, client):
        sign_in(client)
        assert client.get("/api/fleet/jobs/nope1234").status_code == 404

    def test_nothing_runs_when_every_server_is_skipped(self, client, central):
        sign_in(client)
        central.store.set_node_access("web-2", "read", False)

        response = client.post(
            "/api/fleet/actions", json={"action": "certs_renew", "targets": {"nodes": ["web-2"]}}
        )

        assert response.status_code == 400
        assert "No selected server" in response.json()["detail"]


class TestLabels:
    def test_labels_are_set_and_select(self, client, central):
        sign_in(client)

        response = client.put("/api/fleet/servers/web-3/labels", json={"labels": {"env": "prod"}})
        assert response.json() == {"node": "web-3", "labels": {"env": "prod"}}

        plan = client.post(
            "/api/fleet/actions",
            json={"action": "certs_renew", "targets": {"labels": {"env": "prod"}}, "plan": True},
        ).json()["plan"]
        assert [node["node"] for node in plan["nodes"]] == ["web-3"]

        rows = client.get("/api/fleet/servers", params={"node": "web-3"}).json()["items"]
        assert rows[0]["labels"] == {"env": "prod"}

    def test_a_bad_label_is_a_400_on_its_field(self, client):
        sign_in(client)

        response = client.put("/api/fleet/servers/web-3/labels", json={"labels": {"Env": "x"}})

        assert response.status_code == 400
        assert "labels" in (response.json().get("fields") or {})


def test_every_fleet_route_is_mapped():
    from noust.web.permissions.routes_fleet import ROUTES

    assert ROUTES[("GET", "/api/fleet/apps")] == "fleet.read"
    assert ROUTES[("POST", "/api/fleet/actions")] == "fleet.manage"
    assert ROUTES[("POST", "/api/fleet/jobs/{job_id}/retry")] == "fleet.manage"
