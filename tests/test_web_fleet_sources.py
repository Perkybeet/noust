# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/fleet/sources``: what a central shows of the access its servers clone with and of the events
it handed them.

What is pinned: each server's standing is what the keeper found at its last look and, until it has
looked, what was recorded; every problem has a sentence with a code and the other side's own words
beside it; the events are listed with where each stands, what the server queued for it and what the
central follows; leasing again and handing an event over again are the operator's; and every route
of the area is permissioned and scoped.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.config import Config
from noust.core.server_text import say
from noust.core.store import GitHubAppRecord, NodeRecord, NoustStore
from noust.fleet.sources.follows import Follows
from noust.fleet.sources.forwards import Forwards
from noust.fleet.sources.grants import LeaseGrants
from noust.fleet.sources.keeper import LeaseReport, NodeLeases, Problem
from noust.fleet.sources.worker import set_worker
from noust.web.api.deps import install_error_handlers, require_auth
from noust.web.api.fleet_sources import router
from noust.web.permissions.registry import permission_for_route
from noust.web.permissions.scopes import INSTANCE, rule_for

SESSION: dict[str, Any] = {"type": "session", "username": "yago", "account_id": 1}


class Keeper:
    """Stands for the keeper: says what it found at its last look, and what it was asked."""

    def __init__(self) -> None:
        self.found: dict[str, NodeLeases] = {}
        self.swept: list[tuple[Any, bool]] = []

    def reports(self) -> dict[str, NodeLeases]:
        return dict(self.found)

    def sweep(self, nodes: Any = None, *, force: bool = False) -> list[NodeLeases]:
        self.swept.append((nodes, force))
        return [r for r in self.found.values() if nodes is None or r.node in nodes]


class Follower:
    def __init__(self, store: NoustStore) -> None:
        self.follows = Follows(store)

    def step(self) -> list[Any]:
        return []


class Forwarder:
    def __init__(self, store: NoustStore) -> None:
        self.forwards = Forwards(store)

    def deliver_due(self) -> list[Any]:
        return []


class Worker:
    def __init__(self, store: NoustStore) -> None:
        self.keeper = Keeper()
        self.forwarder = Forwarder(store)
        self.follower = Follower(store)
        self.woken = 0

    def wake(self) -> None:
        self.woken += 1


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    Config.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    for name in ("web-1", "web-2"):
        instance.save_node(
            NodeRecord(
                name=name, ssh_host=name, ssh_port=22, ssh_user="u", host_key="k", console_port=1
            )
        )
    yield instance
    NoustStore.reset_instance()
    Config.reset_instance()


@pytest.fixture
def worker(store: NoustStore) -> Iterator[Worker]:
    made = Worker(store)
    set_worker(made)  # type: ignore[arg-type]
    yield made
    set_worker(None)


@pytest.fixture
def client(worker: Worker) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router, prefix="/api/fleet")
    app.dependency_overrides[require_auth] = lambda: SESSION
    return TestClient(app)


def an_app(store: NoustStore) -> None:
    store.save_github_app(GitHubAppRecord(app_id=4242, slug="noust", name="noust", owner="you"))


def by_name(body: dict[str, Any]) -> dict[str, Any]:
    return {node["node"]: node for node in body["nodes"]}


class TestWhereTheServersStand:
    def test_before_the_keeper_has_looked_it_is_what_was_recorded(
        self, client: TestClient, store: NoustStore
    ) -> None:
        LeaseGrants(store).leased(
            "web-1",
            "github.com:77:0",
            host="github.com",
            installation_id=77,
            repositories=["you/app"],
            expires_in=3000,
        )

        nodes = by_name(client.get("/api/fleet/sources").json())

        assert nodes["web-1"]["state"] == "leased"
        assert nodes["web-1"]["checked_at"] is None
        (lease,) = nodes["web-1"]["leases"]
        assert lease["repositories"] == ["you/app"] and 2900 < lease["expires_in"] <= 3000
        assert nodes["web-2"] == {
            "node": "web-2",
            "state": "unknown",
            "leases": [],
            "uncovered": [],
            "problems": [],
            "error": None,
            "checked_at": None,
        }

    def test_after_it_has_looked_it_is_what_it_found(
        self, client: TestClient, worker: Worker
    ) -> None:
        worker.keeper.found["web-1"] = NodeLeases(
            "web-1",
            "leased",
            leases=[LeaseReport("github.com:77:0", 77, ("you/app",), "current", 4_102_444_800.0)],
            uncovered=("you/ghost",),
            problems=[
                Problem(
                    say("sources.not_reachable", repository="you/ghost", url="https://github.com"),
                    None,
                )
            ],
            checked_at=1_800_000_000.0,
        )
        worker.keeper.found["web-2"] = NodeLeases(
            "web-2",
            "ceiling",
            error=Problem(
                say(
                    "sources.node_ceiling",
                    node="web-2",
                    command="noust fleet access --level deploy",
                ),
                "This server lets its central only read",
            ),
        )

        nodes = by_name(client.get("/api/fleet/sources").json())

        assert nodes["web-1"]["uncovered"] == ["you/ghost"]
        assert nodes["web-1"]["problems"][0]["coded"]["code"] == "sources.not_reachable"
        assert nodes["web-1"]["checked_at"].startswith("2027-")
        error = nodes["web-2"]["error"]
        assert nodes["web-2"]["state"] == "ceiling"
        assert error["coded"]["code"] == "sources.node_ceiling"
        assert "noust fleet access --level deploy" in error["hint"]
        assert error["output"] == "This server lets its central only read"

    def test_a_token_is_in_none_of_it(
        self, client: TestClient, store: NoustStore, worker: Worker
    ) -> None:
        LeaseGrants(store).leased(
            "web-1",
            "k",
            host="github.com",
            installation_id=1,
            repositories=["you/app"],
            expires_in=100,
        )

        assert "ghs_" not in client.get("/api/fleet/sources").text

    def test_whether_github_can_reach_this_central_is_said(
        self, client: TestClient, store: NoustStore
    ) -> None:
        an_app(store)

        body = client.get("/api/fleet/sources").json()

        assert body["github"]["configured"] is True and body["github"]["hooks_url"] is None
        assert [w["coded"]["code"] for w in body["github"]["warnings"]] == [
            "sources.webhook_unreachable"
        ]

        Config().set("web.hooks_url", "https://hooks.example.com/hooks")
        body = client.get("/api/fleet/sources").json()
        assert body["github"]["hooks_url"] == "https://hooks.example.com/hooks/github"
        assert body["github"]["warnings"] == []

    def test_how_many_events_are_in_each_state_and_how_many_are_followed(
        self, client: TestClient, store: NoustStore
    ) -> None:
        forwards = Forwards(store)
        one = forwards.add(
            "web-1",
            body={"kind": "push"},
            delivery="d",
            repository="you/app",
            ref="main",
            number=None,
            commit=None,
        )
        forwards.add(
            "web-2",
            body={"kind": "push"},
            delivery="d",
            repository="you/app",
            ref="main",
            number=None,
            commit=None,
        )
        forwards.delivered(one.id, {"jobs": []})
        Follows(store).deployment(
            one.id,
            "web-1",
            "shop.example.com",
            "j1",
            repository="you/app",
            installation_id=77,
            ref=None,
            environment_url=None,
        )

        body = client.get("/api/fleet/sources").json()

        assert body["deliveries"] == {"pending": 1, "delivered": 1, "failed": 0, "expired": 0}
        assert body["following"] == 1


class TestTheEvents:
    def owed(self, store: NoustStore, node: str = "web-1", number: int | None = None) -> Any:
        return Forwards(store).add(
            node,
            body={"kind": "push"},
            delivery="d-1",
            repository="you/app",
            ref="main",
            number=number,
            commit="a" * 40,
        )

    def test_each_is_listed_with_where_it_stands_what_the_server_queued_and_what_is_followed(
        self, client: TestClient, store: NoustStore
    ) -> None:
        sent = self.owed(store)
        Forwards(store).delivered(
            sent.id, {"jobs": [{"domain": "shop.example.com", "job_id": "j1", "merged": False}]}
        )
        Follows(store).deployment(
            sent.id,
            "web-1",
            "shop.example.com",
            "j1",
            repository="you/app",
            installation_id=77,
            ref="a" * 40,
            environment_url=None,
        )

        body = client.get("/api/fleet/sources/deliveries").json()

        (item,) = body["items"]
        assert (item["id"], item["node"], item["kind"], item["state"]) == (
            sent.id,
            "web-1",
            "push",
            "delivered",
        )
        assert item["jobs"] == [{"domain": "shop.example.com", "job_id": "j1", "merged": False}]
        (follow,) = item["follows"]
        assert (follow["kind"], follow["job_id"], follow["state"]) == ("deployment", "j1", "active")
        assert body["counts"]["delivered"] == 1

    def test_a_delivery_that_did_not_arrive_says_why(
        self, client: TestClient, store: NoustStore
    ) -> None:
        sent = self.owed(store)
        Forwards(store).settle(
            sent.id,
            "failed",
            {
                "text": "web-1 runs a Noust that cannot hold the GitHub access this central leases",
                "hint": "Update Noust on web-1 to 4.1 or later",
                "coded": say("sources.node_unsupported", node="web-1").to_dict(),
                "output": "web-1 does not offer POST /api/sources/events",
            },
        )

        (item,) = client.get("/api/fleet/sources/deliveries").json()["items"]

        assert item["state"] == "failed"
        assert item["error"]["coded"]["code"] == "sources.node_unsupported"
        assert "does not offer" in item["error"]["output"]

    def test_the_list_can_be_narrowed_by_server_and_by_state(
        self, client: TestClient, store: NoustStore
    ) -> None:
        self.owed(store, "web-1")
        done = self.owed(store, "web-2")
        Forwards(store).delivered(done.id, {})

        assert [
            i["node"]
            for i in client.get("/api/fleet/sources/deliveries?node=web-2").json()["items"]
        ] == ["web-2"]
        assert [
            i["node"]
            for i in client.get("/api/fleet/sources/deliveries?state=pending").json()["items"]
        ] == ["web-1"]
        assert client.get("/api/fleet/sources/deliveries?state=lost").status_code == 400

    def test_one_that_was_given_up_on_can_be_handed_over_again(
        self, client: TestClient, store: NoustStore, worker: Worker
    ) -> None:
        sent = self.owed(store)
        Forwards(store).settle(
            sent.id, "expired", {"text": "x", "coded": {"code": "c", "params": {}}}
        )

        response = client.post(f"/api/fleet/sources/deliveries/{sent.id}/retry")

        assert response.status_code == 200, response.text
        assert response.json()["state"] == "pending" and response.json()["error"] is None
        assert worker.woken == 1

    def test_one_the_server_took_is_not_sent_twice(
        self, client: TestClient, store: NoustStore
    ) -> None:
        sent = self.owed(store)
        Forwards(store).delivered(sent.id, {})

        response = client.post(f"/api/fleet/sources/deliveries/{sent.id}/retry")

        assert response.status_code == 409
        assert response.json()["coded"]["code"] == "sources.delivery_not_retryable"

    def test_one_that_does_not_exist_is_a_404(self, client: TestClient) -> None:
        assert client.post("/api/fleet/sources/deliveries/fnope/retry").status_code == 404


class TestLeasingAgain:
    def test_a_central_with_no_app_cannot(self, client: TestClient, worker: Worker) -> None:
        response = client.post("/api/fleet/sources/leases", json={})

        assert response.status_code == 409
        assert response.json()["coded"]["code"] == "sources.no_app"
        assert worker.keeper.swept == []

    def test_it_looks_at_the_servers_asked_and_answers_where_each_stands(
        self, client: TestClient, store: NoustStore, worker: Worker
    ) -> None:
        an_app(store)
        worker.keeper.found["web-1"] = NodeLeases("web-1", "none", checked_at=1_800_000_000.0)

        response = client.post(
            "/api/fleet/sources/leases", json={"nodes": ["web-1"], "force": True}
        )

        assert response.status_code == 200, response.text
        assert worker.keeper.swept == [(["web-1"], True)]
        assert [n["state"] for n in response.json()["nodes"]] == ["none"]


class TestEveryRouteIsClassified:
    def test_each_has_a_permission_and_the_instance_scope(self) -> None:
        routes: list[Any] = [r for r in router.routes if hasattr(r, "methods")]
        assert len(routes) == 4
        for route in routes:
            for method in route.methods - {"HEAD", "OPTIONS"}:
                template = "/api/fleet" + route.path
                assert permission_for_route(method, template) is not None, (method, template)
                rule = rule_for(method, template)
                assert rule is not None and rule.scoping == INSTANCE, (method, template)
