# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What every server of the fleet is doing: ``GET /api/fleet/jobs/active``.

A central sees only its own jobs. This asks each node's ``/api/jobs/active`` and
``/api/operations/active`` through its tunnel, all at once and each with its own time limit, and
says per server how it answered: ``ok``, ``unsupported`` (a node that has not been updated answers
404 and will count its tasks when it is) or ``unreachable``. It never fails as a whole: a server
that is down or old has its row and the rest are shown. It is the fleet aggregator's one more view
(rule 3), so the identity each node is told, the cache and the deadline are the ones every view has.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.fleet.aggregate import gather, set_aggregator
from noust.fleet.models import central_name
from noust.web.jobs import JobManager
from noust.web.operations import OPERATIONS, active_operations
from noust.web.server import create_app
from tests.support.fleet_views_support import Central, build_central
from tests.support.web_auth import make_config
from tests.support.web_central import sign_in

ROUTE = "/api/fleet/jobs/active"

JOB = {
    "id": "ab12cd34",
    "type": "deploy",
    "name": "Deploy shop.example.com",
    "status": "running",
    "state": "running",
    "progress": 40,
    "total_steps": 100,
    "current_step": "Building",
    "metadata": {"domain": "shop.example.com"},
    "title": {"code": "jobs.title.deploy", "params": {"domain": "shop.example.com"}},
    "actor": "ana",
}
OPERATION = {
    "id": "op-1f2e3d4c",
    "title": {"code": "operations.app_restart", "params": {"target": "api.example.com"}},
    "target": "api.example.com",
    "domain": "api.example.com",
    "actor": "ben",
    "started_at": "2026-11-03T10:00:00+00:00",
}


@pytest.fixture
def central(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Central]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    Config.reset_instance()
    built = build_central(tmp_path / "fleet", ("web-2", "web-3"))
    monkeypatch.setattr("noust.fleet.nodes.NodeManager", lambda *a, **k: built.manager)
    monkeypatch.setattr(
        "noust.core.update_checker.UpdateChecker.enabled", classmethod(lambda cls: False)
    )
    JobManager.reset_instance()
    active_operations.clear()
    yield built
    active_operations.clear()
    JobManager.reset_instance()
    set_aggregator(None)
    Config.reset_instance()
    NoustStore.reset_instance()


@pytest.fixture
def client(central: Central, sandbox: Path, runner: FakeRunner) -> TestClient:
    return TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))


def answers(node: Any, path: str, status: int, body: Any) -> None:
    """
    Make a node answer one route, ahead of the fake's own handling of ``/api/jobs/<id>``, which
    would take ``active`` for a job.
    """
    node.handlers[("GET", path)] = lambda request: httpx.Response(status, json=body)


def updated(node: Any, jobs: list[dict[str, Any]], operations: list[dict[str, Any]]) -> None:
    """Make a node answer the two routes of 4.2."""
    answers(node, "/api/jobs/active", 200, {"jobs": jobs, "total": len(jobs), "active": 0})
    answers(node, "/api/operations/active", 200, {"operations": operations})


def not_updated(node: Any) -> None:
    """Make a node answer 404 to both, as one that predates them does."""
    answers(node, "/api/jobs/active", 404, {"detail": "Not Found"})
    answers(node, "/api/operations/active", 404, {"detail": "Not Found"})


def rows_of(result: Any) -> dict[str, dict[str, Any]]:
    return {row["node"]: row for row in result.items}


class TestAskingEveryServer:
    def test_each_server_is_a_row_with_its_jobs_and_its_operations(self, central: Central) -> None:
        updated(central.nodes["web-2"], [JOB], [OPERATION])
        updated(central.nodes["web-3"], [], [])

        result = gather("jobs_active", manager=central.manager)

        rows = rows_of(result)
        assert set(rows) == {"web-2", "web-3"}
        assert rows["web-2"]["status"] == "ok"
        assert rows["web-2"]["jobs"] == [JOB] and rows["web-2"]["operations"] == [OPERATION]
        assert rows["web-3"]["status"] == "ok"
        assert rows["web-3"]["jobs"] == [] and rows["web-3"]["operations"] == []
        assert not result.partial

    def test_a_row_says_where_the_server_is_in_this_console(self, central: Central) -> None:
        updated(central.nodes["web-2"], [], [])

        row = rows_of(gather("jobs_active", nodes=["web-2"], manager=central.manager))["web-2"]

        assert row["local"] is False and row["href"].startswith("/n/web-2")

    def test_both_routes_are_asked_of_every_node_and_with_the_operators_identity(
        self, central: Central
    ) -> None:
        for node in central.nodes.values():
            updated(node, [], [])

        gather("jobs_active", manager=central.manager)

        for node in central.nodes.values():
            paths = {request.url.path for request in node.requests}
            assert {"/api/jobs/active", "/api/operations/active"} <= paths


class TestNeverFailingAsAWhole:
    def test_a_node_that_has_not_been_updated_answers_404_and_is_unsupported(
        self, central: Central
    ) -> None:
        updated(central.nodes["web-2"], [JOB], [])
        not_updated(central.nodes["web-3"])

        result = gather("jobs_active", manager=central.manager)

        rows = rows_of(result)
        assert rows["web-3"]["status"] == "unsupported"
        assert rows["web-3"]["jobs"] == [] and rows["web-3"]["operations"] == []
        assert rows["web-2"]["status"] == "ok" and rows["web-2"]["jobs"] == [JOB]
        outcome = result.outcome("web-3")
        assert outcome is not None and outcome.status == "unsupported"

    def test_a_4_1_node_that_lists_jobs_but_not_operations_still_shows_its_jobs(
        self, central: Central
    ) -> None:
        node = central.nodes["web-2"]
        answers(node, "/api/jobs/active", 200, {"jobs": [JOB], "total": 1, "active": 1})
        answers(node, "/api/operations/active", 404, {"detail": "Not Found"})
        updated(central.nodes["web-3"], [], [])

        row = rows_of(gather("jobs_active", manager=central.manager))["web-2"]

        assert row["status"] == "unsupported"
        assert row["jobs"] == [JOB] and row["operations"] == []

    def test_a_node_that_is_down_is_unreachable_and_the_others_answer(
        self, central: Central
    ) -> None:
        updated(central.nodes["web-2"], [JOB], [])
        central.nodes["web-3"].down = True

        result = gather("jobs_active", manager=central.manager)

        rows = rows_of(result)
        assert rows["web-3"]["status"] == "unreachable"
        assert rows["web-3"]["jobs"] == [] and rows["web-3"]["operations"] == []
        assert rows["web-2"]["status"] == "ok"
        assert result.partial

    def test_a_node_that_hangs_does_not_hold_the_others(
        self, central: Central, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import threading

        from noust.fleet import aggregate

        updated(central.nodes["web-2"], [JOB], [])
        central.nodes["web-3"].gate = threading.Event()
        monkeypatch.setattr(aggregate, "DEADLINE", 0.5)
        monkeypatch.setattr(aggregate, "NODE_TIMEOUT", 0.3)

        try:
            result = gather("jobs_active", manager=central.manager)
        finally:
            central.nodes["web-3"].gate.set()

        rows = rows_of(result)
        assert rows["web-2"]["status"] == "ok"
        assert rows["web-3"]["status"] != "ok"


class TestTheRoute:
    def test_it_answers_the_envelope_of_every_view_and_a_row_for_each_server(
        self, client: TestClient, central: Central
    ) -> None:
        updated(central.nodes["web-2"], [JOB], [OPERATION])
        not_updated(central.nodes["web-3"])
        sign_in(client)

        response = client.get(ROUTE)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["resource"] == "jobs_active"
        by_node = {row["node"]: row for row in body["items"]}
        assert {"web-2", "web-3", central_name()} <= set(by_node)
        assert by_node["web-2"]["status"] == "ok" and by_node["web-2"]["jobs"] == [JOB]
        assert by_node["web-3"]["status"] == "unsupported"
        statuses = {node["name"]: node["status"] for node in body["nodes"]}
        assert statuses["web-3"] == "unsupported" and statuses["web-2"] == "ok"

    def test_this_central_is_a_row_of_its_own_with_what_it_runs_now(
        self, client: TestClient, central: Central
    ) -> None:
        for node in central.nodes.values():
            updated(node, [], [])
        running = active_operations.begin(
            OPERATIONS[("POST", "/api/apps/{domain}/restart")],
            {"domain": "shop.example.com"},
            "ana",
        )
        sign_in(client)

        body = client.get(ROUTE).json()

        own = next(row for row in body["items"] if row["node"] == central_name())
        assert own["local"] is True and own["status"] == "ok"
        assert [item["id"] for item in own["operations"]] == [running.id]

    def test_naming_a_server_asks_only_it(self, client: TestClient, central: Central) -> None:
        updated(central.nodes["web-2"], [], [])
        sign_in(client)

        body = client.get(ROUTE, params={"node": "web-2"}).json()

        assert [row["node"] for row in body["items"]] == ["web-2"]
        assert not [r for r in central.nodes["web-3"].requests if "active" in r.url.path]

    def test_it_is_not_taken_for_the_job_with_that_id(
        self, client: TestClient, central: Central
    ) -> None:
        sign_in(client)

        response = client.get(ROUTE)

        assert response.status_code == 200 and "items" in response.json()

    def test_it_needs_to_read_the_fleet_and_is_the_instances(self) -> None:
        from noust.web.permissions import Permission
        from noust.web.permissions.registry import permission_for_route
        from noust.web.permissions.scopes import INSTANCE, rule_for

        assert permission_for_route("GET", ROUTE) == Permission.FLEET_READ
        rule = rule_for("GET", ROUTE)
        assert rule is not None and rule.scoping == INSTANCE

    def test_an_anonymous_request_is_refused(self, client: TestClient) -> None:
        assert client.get(ROUTE).status_code == 401
