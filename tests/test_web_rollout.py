# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/apps/{domain}/rollout`` and ``/api/deploy-freezes`` (checklist B-08, B-17, B-21, B-26).

The settings an application's delivery has, the pause and its release, and the freezes
that hold deploys. The stores are ``test_deploy_gates.py``'s; here are the doors.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core import app_rollout_store, deploy_freeze
from noust.core import webhook_deliveries as deliveries_log
from noust.core.store import App, NoustStore
from noust.deployers.lifecycle import StopPolicyChange
from noust.web.api import app_rollout, deploy_freezes
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers

DOMAIN = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            source="https://github.com/you/app.git",
            app_path=str(tmp_path / "shop"),
            port=3000,
        )
    )
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture
def client(store: NoustStore) -> TestClient:
    server = FastAPI()
    install_error_handlers(server)
    server.include_router(app_rollout.router, prefix="/api/apps")
    server.include_router(deploy_freezes.router, prefix="/api/deploy-freezes")
    session = {"type": "session", "sid": "abcdef012345", "username": "ana", "account_id": 1}
    server.dependency_overrides[get_current_session] = lambda: session
    return TestClient(server)


@pytest.fixture
def stops(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The stop policies written into units, instead of writing any."""
    written: list[dict[str, Any]] = []

    def fake(domain: str, *, timeout: int | None, signal: str | None, logger: Any = None) -> Any:
        written.append({"domain": domain, "timeout": timeout, "signal": signal})
        app_rollout_store.set_stop_policy(domain, timeout=timeout, signal=signal)
        return StopPolicyChange(domain, timeout, signal, (domain,))

    monkeypatch.setattr(app_rollout, "set_stop_policy", fake)
    return written


class TestReading:
    def test_the_defaults_are_the_servers(self, client: TestClient) -> None:
        body = client.get(f"/api/apps/{DOMAIN}/rollout").json()

        assert body["history_keep"] is None
        assert body["history_keep_server"] == 20 == body["history_keep_effective"]
        assert body["stop_timeout"] is None and body["stop_signal"] is None
        assert body["stop_supported"] is True
        assert body["pause_on_rollback"] is True
        assert body["paused"] is None and body["freeze"] is None and body["held"] == 0

    def test_a_stack_has_no_unit_to_give_a_stop_policy(
        self, client: TestClient, store: NoustStore
    ) -> None:
        app = store.get_app(DOMAIN)
        assert app is not None
        app.app_type = "docker-compose"
        store.update_app(app)

        assert client.get(f"/api/apps/{DOMAIN}/rollout").json()["stop_supported"] is False

    def test_what_can_deploy_it_by_itself_is_listed(
        self, client: TestClient, store: NoustStore
    ) -> None:
        from noust.web.api.hooks import mint_webhook_secret

        mint_webhook_secret(DOMAIN)

        assert client.get(f"/api/apps/{DOMAIN}/rollout").json()["automatic"] == ["webhook"]

    def test_an_unknown_application_is_a_404(self, client: TestClient) -> None:
        assert client.get("/api/apps/gone.example.com/rollout").status_code == 404

    def test_a_freeze_that_holds_it_is_shown(self, client: TestClient) -> None:
        deploy_freeze.create("incident 412", by="ana")

        body = client.get(f"/api/apps/{DOMAIN}/rollout").json()

        assert body["freeze"]["reason"] == "incident 412"
        assert body["freeze"]["scope"] == "server" and body["freeze"]["state"] == "active"


class TestChanging:
    def test_the_history_and_going_back_are_changed_together(self, client: TestClient) -> None:
        response = client.put(
            f"/api/apps/{DOMAIN}/rollout", json={"history_keep": 40, "pause_on_rollback": False}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["history_keep"] == 40 and body["history_keep_effective"] == 40
        assert body["pause_on_rollback"] is False

    def test_null_goes_back_to_the_servers_number(self, client: TestClient) -> None:
        client.put(f"/api/apps/{DOMAIN}/rollout", json={"history_keep": 40})

        body = client.put(f"/api/apps/{DOMAIN}/rollout", json={"history_keep": None}).json()

        assert body["history_keep"] is None and body["history_keep_effective"] == 20

    def test_a_field_left_out_is_not_touched(self, client: TestClient) -> None:
        client.put(f"/api/apps/{DOMAIN}/rollout", json={"history_keep": 40})

        body = client.put(f"/api/apps/{DOMAIN}/rollout", json={"pause_on_rollback": False}).json()

        assert body["history_keep"] == 40

    def test_the_stop_policy_is_written_into_the_unit(
        self, client: TestClient, stops: list[dict[str, Any]]
    ) -> None:
        response = client.put(
            f"/api/apps/{DOMAIN}/rollout", json={"stop_timeout": 45, "stop_signal": "SIGINT"}
        )

        assert response.status_code == 200, response.text
        assert stops == [{"domain": DOMAIN, "timeout": 45, "signal": "SIGINT"}]
        assert response.json()["stop_timeout"] == 45

    def test_one_half_of_the_policy_keeps_the_other(
        self, client: TestClient, stops: list[dict[str, Any]]
    ) -> None:
        client.put(
            f"/api/apps/{DOMAIN}/rollout", json={"stop_timeout": 45, "stop_signal": "SIGINT"}
        )

        client.put(f"/api/apps/{DOMAIN}/rollout", json={"stop_timeout": 60})

        assert stops[-1] == {"domain": DOMAIN, "timeout": 60, "signal": "SIGINT"}

    @pytest.mark.parametrize(
        ("body", "field"),
        [
            ({"history_keep": 0}, "history_keep"),
            ({"history_keep": 5000}, "history_keep"),
            ({}, "rollout"),
        ],
    )
    def test_a_value_out_of_range_names_its_field_and_changes_nothing(
        self, client: TestClient, body: dict[str, Any], field: str
    ) -> None:
        response = client.put(f"/api/apps/{DOMAIN}/rollout", json=body)

        assert response.status_code == 400
        assert response.json()["fields"] == [field] or field in str(response.json())
        assert app_rollout_store.get(DOMAIN).history_keep is None

    def test_a_signal_that_kills_nothing_is_refused(
        self, client: TestClient, stops: list[dict[str, Any]]
    ) -> None:
        # The real operation checks it: the fake above stands in for the unit write only.
        from noust.deployers import lifecycle

        with pytest.raises(Exception, match="Not a signal"):
            lifecycle.set_stop_policy(DOMAIN, timeout=None, signal="SIGKILL")


class TestThePause:
    def test_pausing_and_resuming(self, client: TestClient) -> None:
        paused = client.post(f"/api/apps/{DOMAIN}/rollout/pause", json={"reason": "looking"})

        assert paused.status_code == 200
        assert paused.json()["paused"]["reason"] == "looking"
        assert paused.json()["paused"]["by"] == "ana"

        resumed = client.post(f"/api/apps/{DOMAIN}/rollout/resume")

        assert resumed.json() == {"domain": DOMAIN, "was_paused": True, "held": 0}
        assert client.get(f"/api/apps/{DOMAIN}/rollout").json()["paused"] is None

    def test_resuming_says_how_many_pushes_were_held(
        self, client: TestClient, store: NoustStore
    ) -> None:
        app = store.get_app(DOMAIN)
        assert app is not None and app.id is not None
        app_rollout_store.pause(DOMAIN, by="rollback")
        for _ in range(2):
            deliveries_log.record_delivery(app.id, deliveries_log.IGNORED_PAUSED, detail="paused")
        deliveries_log.record_delivery(app.id, deliveries_log.DEPLOY_STARTED)

        shown = client.get(f"/api/apps/{DOMAIN}/rollout").json()
        resumed = client.post(f"/api/apps/{DOMAIN}/rollout/resume").json()

        assert shown["held"] == 2 and resumed["held"] == 2

    def test_a_reason_too_long_is_refused(self, client: TestClient) -> None:
        response = client.post(f"/api/apps/{DOMAIN}/rollout/pause", json={"reason": "x" * 400})

        assert response.status_code == 422 or response.status_code == 400


class TestFreezes:
    def test_freezing_the_server_and_lifting_it(self, client: TestClient) -> None:
        made = client.post("/api/deploy-freezes", json={"reason": "incident 412", "hours": 2})

        assert made.status_code == 201, made.text
        freeze = made.json()
        assert freeze["scope"] == "server" and freeze["state"] == "active"
        assert freeze["created_by"] == "ana" and freeze["ends_at"]

        listed = client.get("/api/deploy-freezes").json()["freezes"]
        assert [f["id"] for f in listed] == [freeze["id"]]

        lifted = client.delete(f"/api/deploy-freezes/{freeze['id']}")
        assert lifted.status_code == 200
        assert lifted.json()["lifted"][0]["id"] == freeze["id"]
        assert client.get("/api/deploy-freezes").json()["freezes"] == []
        past = client.get("/api/deploy-freezes", params={"include_past": True}).json()["freezes"]
        assert past[0]["state"] == "lifted"

    def test_one_application_alone(self, client: TestClient) -> None:
        made = client.post("/api/deploy-freezes", json={"reason": "sale", "domain": DOMAIN})

        assert made.status_code == 201
        assert made.json()["scope"] == DOMAIN

    def test_an_unknown_application_is_a_404(self, client: TestClient) -> None:
        made = client.post(
            "/api/deploy-freezes", json={"reason": "x", "domain": "gone.example.com"}
        )

        assert made.status_code == 404

    @pytest.mark.parametrize(
        "body",
        [
            {"reason": ""},
            {"reason": "x", "starts_at": "friday"},
            {"reason": "x", "hours": -1},
            {"reason": "x", "hours": 3, "ends_at": "2099-01-01T00:00"},
        ],
    )
    def test_what_cannot_be_a_freeze_is_refused(
        self, client: TestClient, body: dict[str, Any]
    ) -> None:
        response = client.post("/api/deploy-freezes", json=body)

        assert response.status_code in (400, 422)
        assert deploy_freeze.list_freezes() == []

    def test_lifting_what_does_not_exist_is_a_404(self, client: TestClient) -> None:
        assert client.delete("/api/deploy-freezes/999").status_code == 404

    def test_a_window_is_listed_before_it_starts(self, client: TestClient) -> None:
        client.post(
            "/api/deploy-freezes",
            json={
                "reason": "holiday",
                "starts_at": "2099-12-24T18:00",
                "ends_at": "2099-12-26T08:00",
            },
        )

        listed = client.get("/api/deploy-freezes").json()["freezes"]

        assert [f["state"] for f in listed] == ["scheduled"]
