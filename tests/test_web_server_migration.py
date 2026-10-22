# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/server/migration`` (checklist H-30).

The contract: planning and checking answer at once and change nothing; preparing copies the whole
server, so it asks for sudo mode and runs as a job; what could not succeed (a destination that
does not exist or cannot be reached, no passphrase to seal the state under) is refused when the
request arrives, not in a failed job; and the passphrase is never in a response or a result.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import noust.web.api.databases.common as db_common
import noust.web.api.server_migration as migration_module
from noust.core import audit
from noust.core.app_state import AppState
from noust.core.store import NoustStore
from noust.managers.server_migration import ServerMigration
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager
from tests.test_server_migration import HOST, Recorder, add_app, checker

PASSPHRASE = "correct horse battery staple"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    opened = NoustStore(tmp_path / "noust.db")
    yield opened
    opened.close()
    NoustStore.reset_instance()


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def app(
    tmp_path: Path, store: NoustStore, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> FastAPI:
    monkeypatch.setattr(audit, "record", lambda *args, **kwargs: None)

    def build(**kwargs: Any) -> ServerMigration:
        migration = checker(store, recorder)
        migration._databases = lambda engine, database, destination: f"{database}.sql.gz"
        return migration

    monkeypatch.setattr(migration_module, "ServerMigration", build)
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []

    def create_job(**kwargs: Any) -> SimpleNamespace:
        created.append(kwargs)
        return SimpleNamespace(
            id=f"job-{len(created)}",
            status=SimpleNamespace(value="pending"),
            to_dict=lambda: {"id": f"job-{len(created)}", "status": "pending"},
        )

    monkeypatch.setattr(
        db_common, "get_job_manager", lambda: SimpleNamespace(create_job=create_job)
    )
    return created


def sign_in(app: FastAPI, *, elevate: bool) -> TestClient:
    client = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    login = client.post("/api/auth/login", json={"token": token})
    assert login.status_code == 200, login.text
    client.headers[CSRF_HEADER_NAME] = login.json()["csrf_token"]
    if elevate:
        assert client.post("/api/auth/elevate", json={"token": token}).status_code == 200
    return client


@pytest.fixture
def plain(app: FastAPI) -> TestClient:
    return sign_in(app, elevate=False)


@pytest.fixture
def admin(app: FastAPI) -> TestClient:
    return sign_in(app, elevate=True)


def run(job: dict[str, Any]) -> dict[str, Any]:
    return job["func"](**job["kwargs"])


class TestPlanning:
    def test_it_lists_what_would_move(
        self, plain: TestClient, store: NoustStore, recorder: Recorder
    ) -> None:
        add_app(store, "shop.example.com")
        recorder.targets["shop.example.com"] = ["uploads"]

        response = plain.get("/api/server/migration/plan", params={"destination": "nas"})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["host"] == HOST and body["blockers"] == []
        assert body["applications"][0]["domain"] == "shop.example.com"
        assert body["applications"][0]["data"] == 1
        assert recorder.calls == []

    def test_a_destination_that_does_not_exist_is_a_blocker_not_an_error(
        self, plain: TestClient, recorder: Recorder
    ) -> None:
        recorder.destination_exists = False

        body = plain.get("/api/server/migration/plan", params={"destination": "nas"}).json()

        assert any("does not exist" in blocker for blocker in body["blockers"])


class TestPreparing:
    def test_it_asks_for_sudo_mode(self, plain: TestClient, queued: list[dict[str, Any]]) -> None:
        response = plain.post("/api/server/migration/prepare", json={"destination": "nas"})

        assert response.status_code in (401, 403) and queued == []

    def test_a_destination_that_cannot_be_used_is_refused_before_a_job_exists(
        self, admin: TestClient, recorder: Recorder, queued: list[dict[str, Any]]
    ) -> None:
        recorder.reachable = False

        response = admin.post(
            "/api/server/migration/prepare",
            json={"destination": "nas", "passphrase": PASSPHRASE},
        )

        assert response.status_code == 400
        assert "cannot be reached" in response.text
        assert queued == []

    def test_without_a_passphrase_given_or_stored_it_is_refused(
        self, admin: TestClient, queued: list[dict[str, Any]]
    ) -> None:
        response = admin.post("/api/server/migration/prepare", json={"destination": "nas"})

        assert response.status_code == 400
        assert "No passphrase" in response.text and queued == []

    def test_the_job_copies_everything_and_the_passphrase_is_nowhere_in_the_result(
        self,
        admin: TestClient,
        store: NoustStore,
        recorder: Recorder,
        queued: list[dict[str, Any]],
    ) -> None:
        add_app(store, "shop.example.com")

        response = admin.post(
            "/api/server/migration/prepare",
            json={"destination": "nas", "passphrase": PASSPHRASE},
        )

        assert response.status_code == 202, response.text
        assert PASSPHRASE not in response.text
        result = run(queued[0])
        assert result["ok"] is True and result["bundle"].startswith(f"noust-self-{HOST}")
        assert [step["kind"] for step in result["steps"]] == ["application", "noust"]
        assert ("seal", PASSPHRASE) in recorder.calls
        assert PASSPHRASE not in str(result)
        assert any("restore-server" in step for step in result["next_steps"])

    def test_the_stored_passphrase_is_used_when_the_request_brings_none(
        self,
        admin: TestClient,
        store: NoustStore,
        recorder: Recorder,
        queued: list[dict[str, Any]],
    ) -> None:
        add_app(store, "shop.example.com")
        recorder.passphrase = "the stored one"

        response = admin.post("/api/server/migration/prepare", json={"destination": "nas"})

        assert response.status_code == 202, response.text
        run(queued[0])
        assert ("seal", "the stored one") in recorder.calls

    def test_the_applications_and_the_data_can_be_chosen(
        self,
        admin: TestClient,
        store: NoustStore,
        recorder: Recorder,
        queued: list[dict[str, Any]],
    ) -> None:
        add_app(store, "shop.example.com")
        add_app(store, "blog.example.com")
        recorder.targets["blog.example.com"] = ["uploads"]

        admin.post(
            "/api/server/migration/prepare",
            json={
                "destination": "nas",
                "passphrase": PASSPHRASE,
                "apps": ["blog.example.com"],
                "data": False,
            },
        )
        result = run(queued[0])

        states = {step["name"]: step["state"] for step in result["steps"]}
        assert states["shop.example.com"] == "skipped" and states["blog.example.com"] == "done"
        assert not any(step["kind"] == "data" for step in result["steps"])


class TestChecking:
    def test_it_reports_each_application_and_the_names_still_elsewhere(
        self, plain: TestClient, store: NoustStore
    ) -> None:
        add_app(store, "shop.example.com")

        response = plain.get("/api/server/migration/check")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["host"] == HOST and body["ok"] is True
        assert body["applications"][0]["domain"] == "shop.example.com"
        assert body["pending"][0]["name"] == "shop.example.com"
        assert body["counts"]["down"] == 0

    def test_an_application_that_is_down_makes_the_report_not_ok(
        self,
        plain: TestClient,
        store: NoustStore,
        recorder: Recorder,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        add_app(store, "shop.example.com")
        down = checker(
            store, recorder, states={"shop.example.com": AppState("Stopped", False, "inactive")}
        )
        monkeypatch.setattr(migration_module, "ServerMigration", lambda **kwargs: down)

        body = plain.get("/api/server/migration/check").json()

        assert body["ok"] is False and body["counts"]["down"] == 1
