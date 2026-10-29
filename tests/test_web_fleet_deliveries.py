# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``GET /api/views/deliveries``: the record of deliveries of every server, as the console reads it.

The central is the real application with a signed-in operator; the servers are the fakes of
:mod:`tests.support.fleet_views_support`. What is pinned: it answers the envelope of every other
view, plus how many deliveries each server has in each state; the central is one more server whose
own record is listed with the rest; a state that does not exist is a bad request and nobody is
asked; a state or a channel to filter by reaches the servers; the servers that refuse the operator
come grouped with what they are missing; and a Noust with no servers lists its own.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.fleet.aggregate import set_aggregator
from noust.fleet.models import central_name
from noust.web.jobs import JobManager
from noust.web.server import create_app
from tests.support.fleet_views_support import Central, build_central
from tests.support.web_auth import make_config
from tests.support.web_central import sign_in

PATH = "/api/notifications/deliveries"


def delivery(ident: int, created: float, **fields: Any) -> dict[str, Any]:
    return {
        "id": ident,
        "notification_id": f"n{ident}",
        "kind": "deploy_failed",
        "code": "deploy.failed",
        "subject": "shop.example.com",
        "channel": "ops",
        "channel_type": "slack",
        "state": "sent",
        "attempts": 1,
        "created_at": created,
        "updated_at": created,
        "next_attempt_at": None,
        "last_error": None,
        "can_retry": False,
        "held_for": None,
        **fields,
    }


def _wired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, names: tuple[str, ...]
) -> Iterator[Central]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    Config.reset_instance()
    built = build_central(tmp_path / "fleet", names)
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
def central(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Central]:
    yield from _wired(tmp_path, monkeypatch, ("web-2", "web-3"))


@pytest.fixture
def lone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Central]:
    yield from _wired(tmp_path, monkeypatch, ())


@pytest.fixture
def client(central: Central, sandbox: Path, runner: FakeRunner) -> TestClient:
    return TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))


@pytest.fixture
def lone_client(lone: Central, sandbox: Path, runner: FakeRunner) -> TestClient:
    return TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))


def _answer(central: Central) -> None:
    central.nodes["web-2"].responses[PATH] = (
        200,
        {
            "items": [
                delivery(2, 300.0),
                delivery(1, 100.0, state="failed", last_error="HTTP 500"),
            ],
            "counts": {"sent": 40, "failed": 3},
        },
    )
    central.nodes["web-3"].responses[PATH] = (
        200,
        {"items": [delivery(7, 200.0)], "counts": {"sent": 9}},
    )


class TestTheView:
    def test_it_answers_the_envelope_of_every_view_and_the_counts_of_each_server(
        self, client: TestClient, central: Central
    ) -> None:
        sign_in(client)
        _answer(central)

        response = client.get("/api/views/deliveries")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["resource"] == "deliveries"
        assert body["fleet"] is True
        assert [n["name"] for n in body["nodes"]] == [central_name(), "web-2", "web-3"]
        assert [r["id"] for r in body["items"] if r["node"] != central_name()] == [2, 7, 1]
        assert body["counts"]["web-2"] == {"sent": 40, "failed": 3}
        assert body["counts"]["web-3"] == {"sent": 9}
        assert central_name() in body["counts"], "the central is one more server"

    def test_the_central_lists_its_own_record_with_the_rest(
        self, client: TestClient, central: Central
    ) -> None:
        sign_in(client)
        _answer(central)

        body = client.get("/api/views/deliveries", params={"node": "@central"}).json()

        assert [n["name"] for n in body["nodes"]] == [central_name()]
        assert body["items"] == []
        assert isinstance(body["counts"][central_name()], dict)

    def test_naming_a_server_asks_only_it(self, client: TestClient, central: Central) -> None:
        sign_in(client)
        _answer(central)

        body = client.get("/api/views/deliveries", params={"node": "web-3"}).json()

        assert [r["id"] for r in body["items"]] == [7]
        assert list(body["counts"]) == ["web-3"]
        assert not [r for r in central.nodes["web-2"].requests if r.url.path == PATH]

    def test_a_row_says_where_it_can_be_sent_again(
        self, client: TestClient, central: Central
    ) -> None:
        sign_in(client)
        _answer(central)

        row = next(r for r in client.get("/api/views/deliveries").json()["items"] if r["id"] == 7)

        assert (row["node"], row["page"], row["href"]) == (
            "web-3",
            "/settings/notifications",
            "/n/web-3/settings/notifications",
        )


class TestTheFilters:
    def test_a_state_and_a_channel_reach_the_servers(
        self, client: TestClient, central: Central
    ) -> None:
        sign_in(client)
        _answer(central)

        client.get(
            "/api/views/deliveries", params={"state": "failed", "channel": "ops", "limit": 5}
        )

        asked = [dict(r.url.params) for r in central.nodes["web-2"].requests if r.url.path == PATH]
        assert {"limit": "5", "state": "failed", "channel": "ops"} in asked

    def test_a_state_that_does_not_exist_is_refused_and_nobody_is_asked(
        self, client: TestClient, central: Central
    ) -> None:
        sign_in(client)

        response = client.get("/api/views/deliveries", params={"state": "lost"})

        assert response.status_code == 400
        assert response.json()["error"] == "validationerror"
        assert "state" in response.text
        assert not [r for r in central.nodes["web-2"].requests if r.url.path == PATH]

    def test_a_limit_outside_what_a_server_takes_is_refused(
        self, client: TestClient, central: Central
    ) -> None:
        sign_in(client)

        assert client.get("/api/views/deliveries", params={"limit": 0}).status_code == 422
        assert client.get("/api/views/deliveries", params={"limit": 501}).status_code == 422


class TestWhoMayRead:
    def test_the_servers_that_refuse_the_operator_come_as_one_group_with_what_is_missing(
        self, client: TestClient, central: Central
    ) -> None:
        sign_in(client)
        for name in ("web-2", "web-3"):
            central.nodes[name].responses[PATH] = (
                403,
                {
                    "error": "permission_denied",
                    "detail": "This needs the 'settings.read' permission",
                    "hint": "Ask a security officer.",
                    "fields": None,
                },
            )

        body = client.get("/api/views/deliveries").json()

        (group,) = body["refusals"]
        assert group["servers"] == ["web-2", "web-3"]
        assert group["permission"] == "settings.read"
        assert group["coded"]["code"] == "fleet.refused.permission"
        assert "counts" in body and "web-2" not in body["counts"]

    def test_a_credential_that_reads_no_settings_reads_no_record(
        self, client: TestClient, central: Central
    ) -> None:
        from noust.web.server import get_token_manager
        from tests.support.teams_support import PASSWORD

        tokens = get_token_manager()
        owner = tokens.accounts.create("apps-only", "viewer", password=PASSWORD)
        issued = tokens.create_api_token(
            "apps-only", "read", None, owner=owner, permissions=["apps.read"]
        )

        response = client.get(
            "/api/views/deliveries", headers={"Authorization": f"Bearer {issued['token']}"}
        )

        assert response.status_code == 403
        assert response.json()["error"] == "permission_denied"
        assert not [r for r in central.nodes["web-2"].requests if r.url.path == PATH]


class TestWithoutAFleet:
    def test_a_noust_with_no_servers_lists_its_own_record(
        self, lone_client: TestClient, lone: Central
    ) -> None:
        sign_in(lone_client)

        body = lone_client.get("/api/views/deliveries").json()

        assert body["fleet"] is False
        assert body["servers"] == []
        assert [n["local"] for n in body["nodes"]] == [True]
        assert body["items"] == []
