# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``GET /api/attention``: what needs this person, as one list the server joins (item 161).

The bell, Home and the bar read it, so they show the same number. It is the Overview's own list (the
applications, the machine, the findings of Security, the alerts and the probes: the fleet reads the
same one from every server) plus what is a person's: the requests waiting for their decision, a newer
Noust and the recipes with an update, each only for whoever may act on it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.store import NoustStore
from noust.managers.attention import AttentionList, reason
from noust.web.api import attention as attention_api
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.permissions import Permission
from noust.web.server import create_app, get_token_manager


def row(key: str, title: str, severity: str = "warn", kind: str = "state") -> dict[str, Any]:
    rows = AttentionList()
    rows.add(key, {"kind": "app", "domain": title}, title, reason(kind, severity, "x"))
    [item], _ = rows.result()
    return item


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def app(tmp_path: Path, store: NoustStore, runner: object) -> FastAPI:
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    response = signed_in.post("/api/auth/login", json={"token": token})
    assert response.status_code == 200, response.text
    signed_in.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return signed_in


@pytest.fixture
def anonymous(app: FastAPI) -> TestClient:
    return TestClient(app, client=("testclient", 50000), follow_redirects=False)


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """What the server's own Overview says, set by a test; and nothing that is a person's."""
    said: dict[str, Any] = {"attention": [], "attention_total": 0}
    monkeypatch.setattr(attention_api, "current_overview", lambda: said)
    monkeypatch.setattr(attention_api, "waiting_requests", lambda session: [])
    monkeypatch.setattr(attention_api, "noust_update", lambda: None)
    monkeypatch.setattr(attention_api, "recipe_statuses", lambda: [])
    return said


class TestTheListOverHttp:
    def test_it_needs_a_session(self, anonymous: TestClient) -> None:
        assert anonymous.get("/api/attention").status_code == 401

    def test_it_is_for_anyone_signed_in_and_says_so_in_the_schema(self, client: TestClient) -> None:
        schema = client.get("/api/openapi.json").json()

        operation = schema["paths"]["/api/attention"]["get"]
        assert operation["x-noust-permission"] == "self"
        reference = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        assert reference.endswith("AttentionResponse")

    def test_a_server_with_nothing_to_say_says_so(
        self, client: TestClient, server: dict[str, Any]
    ) -> None:
        assert client.get("/api/attention").json() == {"items": [], "total": 0}

    def test_the_overviews_rows_come_with_their_origin(
        self, client: TestClient, server: dict[str, Any]
    ) -> None:
        server["attention"] = [row("app:a", "a.example.com", "fail")]
        server["attention_total"] = 1

        body = client.get("/api/attention").json()

        [item] = body["items"]
        assert (item["id"], item["origin"], item["severity"]) == ("app:a", "state", "fail")
        assert item["reasons"][0]["origin"] == "state"

    def test_it_is_the_same_list_the_overview_gives_when_nobody_adds_anything(
        self, client: TestClient
    ) -> None:
        overview = client.get("/api/overview").json()

        body = client.get("/api/attention").json()

        assert [item["id"] for item in body["items"]] == [
            item["id"] for item in overview["attention"]
        ]
        assert body["total"] == overview["attention_total"]


class TestWhatIsAPersons:
    def test_the_requests_waiting_for_a_decision_join_the_list_in_rank(
        self, client: TestClient, server: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server["attention"] = [row("app:a", "a.example.com", "fail"), row("app:z", "z", "warn")]
        server["attention_total"] = 2
        monkeypatch.setattr(
            attention_api,
            "waiting_requests",
            lambda session: [{"id": 7, "requester": {"name": "ana"}, "created_at": 1_790_000_000}],
        )

        body = client.get("/api/attention").json()

        assert [item["id"] for item in body["items"]] == ["app:a", "approval:7", "app:z"]
        assert body["total"] == 3

    def test_a_list_the_overview_cut_still_counts_every_row(
        self, client: TestClient, server: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server["attention"] = [row("app:a", "a.example.com")]
        server["attention_total"] = 140
        monkeypatch.setattr(
            attention_api,
            "waiting_requests",
            lambda session: [{"id": 7, "requester": {"name": "ana"}, "created_at": 1.0}],
        )

        body = client.get("/api/attention").json()

        assert len(body["items"]) == 2
        assert body["total"] == 141

    def test_a_newer_noust_and_the_recipes_behind_join_it(
        self, client: TestClient, server: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            attention_api,
            "noust_update",
            lambda: {"has_update": True, "latest_version": "4.3.0", "current_version": "4.2.1"},
        )
        monkeypatch.setattr(
            attention_api,
            "recipe_statuses",
            lambda: [
                {
                    "domain": "kuma.example.com",
                    "recipe": "uptime-kuma",
                    "update_available": True,
                    "installed_version": "1.0",
                    "latest_version": "1.1",
                }
            ],
        )

        body = client.get("/api/attention").json()

        assert {item["id"] for item in body["items"]} == {"noust:update", "recipe:kuma.example.com"}
        assert body["total"] == 2


class TestOnlyWhatIsOwn:
    def test_a_central_asks_for_what_is_the_persons_own_and_reads_the_servers_from_each(
        self, client: TestClient, server: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server["attention"] = [row("app:a", "a.example.com", "fail")]
        server["attention_total"] = 1
        monkeypatch.setattr(
            attention_api,
            "waiting_requests",
            lambda session: [{"id": 7, "requester": {"name": "ana"}, "created_at": 1.0}],
        )

        everything = client.get("/api/attention").json()
        own = client.get("/api/attention", params={"only": "own"}).json()

        assert [item["id"] for item in everything["items"]] == ["app:a", "approval:7"]
        assert [item["id"] for item in own["items"]] == ["approval:7"]
        assert own["total"] == 1

    def test_any_other_value_is_refused(self, client: TestClient, server: dict[str, Any]) -> None:
        assert client.get("/api/attention", params={"only": "everything"}).status_code == 422


class TestWhoSeesWhat:
    @staticmethod
    def session(*permissions: str) -> dict[str, Any]:
        return {"permissions": list(permissions)}

    def test_a_person_who_cannot_read_the_server_is_not_shown_its_list(
        self, server: dict[str, Any]
    ) -> None:
        server["attention"] = [row("app:a", "a.example.com", "fail")]
        server["attention_total"] = 1

        body = attention_api.gather(self.session(Permission.SELF))

        assert body == {"items": [], "total": 0}

    def test_a_person_who_can_read_it_is_shown_it(self, server: dict[str, Any]) -> None:
        server["attention"] = [row("app:a", "a.example.com", "fail")]
        server["attention_total"] = 1

        body = attention_api.gather(self.session(Permission.SELF, Permission.SERVER_READ))

        assert [item["id"] for item in body["items"]] == ["app:a"]

    def test_a_newer_noust_is_for_whoever_may_update_it(
        self, server: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            attention_api,
            "noust_update",
            lambda: {"has_update": True, "latest_version": "4.3.0", "current_version": "4.2.1"},
        )

        reader = attention_api.gather(self.session(Permission.SELF, Permission.SERVER_READ))
        manager = attention_api.gather(
            self.session(Permission.SELF, Permission.SERVER_READ, Permission.SERVER_MANAGE)
        )

        assert reader["items"] == []
        assert [item["id"] for item in manager["items"]] == ["noust:update"]

    def test_the_recipes_are_for_whoever_may_read_applications_and_only_their_own(
        self, server: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        statuses = [
            {
                "domain": "mine.example.com",
                "recipe": "ghost",
                "update_available": True,
                "installed_version": "5.0",
                "latest_version": "5.1",
            },
            {
                "domain": "theirs.example.com",
                "recipe": "ghost",
                "update_available": True,
                "installed_version": "5.0",
                "latest_version": "5.1",
            },
        ]
        monkeypatch.setattr(attention_api, "recipe_statuses", lambda: statuses)
        monkeypatch.setattr(
            attention_api,
            "visible_domains",
            lambda found: [s for s in found if s["domain"] == "mine.example.com"],
        )

        nobody = attention_api.gather(self.session(Permission.SELF))
        reader = attention_api.gather(self.session(Permission.SELF, Permission.APPS_READ))

        assert nobody["items"] == []
        assert [item["id"] for item in reader["items"]] == ["recipe:mine.example.com"]

    def test_the_requests_are_asked_of_the_people_who_may_decide_them(
        self, server: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        asked: list[dict[str, Any]] = []

        def waiting(session: dict[str, Any]) -> list[dict[str, Any]]:
            asked.append(session)
            return []

        monkeypatch.setattr(attention_api, "waiting_requests", waiting)
        me = self.session(Permission.SELF)

        attention_api.gather(me)

        assert asked == [me]
