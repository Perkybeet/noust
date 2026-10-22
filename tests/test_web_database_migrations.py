# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/moves`` and ``/api/databases/instances/migrate`` (checklist C-13, C-21).

The contract: planning answers at once and changes nothing; what could not succeed
is refused when the request arrives, not in a failed job; moving and rolling back
need sudo mode and run as jobs; the moves are listed with whether they can still be
rolled back; and nothing the API returns carries a password.
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
import noust.web.api.databases.jobs as jobs_module
import noust.web.api.databases.migrations as migrations_module
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager
from tests.database_migration_support import Lab, build_lab
from tests.database_move_support import APP_PASSWORD, DOMAIN, HOST

MIGRATE = {"source": "postgresql", "to_version": "17", "memory_mb": 1024}


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Lab]:
    yield from build_lab(tmp_path, monkeypatch)


@pytest.fixture
def app(tmp_path: Path, lab: Lab, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    # Every request builds its service over the lab's engines and its Docker host.
    monkeypatch.setattr(migrations_module, "service", lambda session=None, logger=None: lab.service)
    monkeypatch.setattr(
        jobs_module, "_service", lambda job_context, step, actor: lab.service, raising=True
    )
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
    def test_a_database_plan_names_its_applications_and_changes_nothing(
        self, plain: TestClient, lab: Lab
    ) -> None:
        # The instance has to exist for a database to move to it.
        lab.managed.create("pg17", "postgresql", version="17", memory="1g")

        response = plain.post(
            "/api/databases/moves/plan",
            json={"source": HOST, "database": "shop_db", "target": "postgresql@pg17"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["apps"] == [DOMAIN] and body["blockers"] == []
        assert (body["source_version"], body["target_version"]) == ("16.4", "17.2")
        assert lab.env()["DATABASE_URL"].endswith("5432/shop_db"), "nothing was pointed"

    def test_an_engine_plan_says_what_will_be_made_and_changes_nothing(
        self, plain: TestClient, lab: Lab
    ) -> None:
        response = plain.post("/api/databases/instances/migrate/plan", json=MIGRATE)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["target"] == "postgresql17" and body["new_instance"]["memory_limit_mb"] == 1024
        assert [entry["database"] for entry in body["databases"]] == ["shop_db"]
        assert body["apps"] == [DOMAIN]
        assert lab.managed.records.all() == []

    def test_a_version_the_catalog_lacks_is_a_400_listing_what_it_has(
        self, plain: TestClient
    ) -> None:
        response = plain.post(
            "/api/databases/instances/migrate/plan", json={**MIGRATE, "to_version": "99"}
        )

        assert response.status_code == 400
        assert "15, 16, 17, 18" in response.text

    def test_the_moves_are_listed_with_whether_they_can_be_rolled_back(
        self, plain: TestClient, lab: Lab
    ) -> None:
        assert plain.get("/api/databases/instances/migrations").json() == {"migrations": []}
        lab.migrations.run(HOST, "17", memory="1g")

        body = plain.get("/api/databases/instances/migrations").json()

        [entry] = body["migrations"]
        assert entry["state"] == "done" and entry["can_roll_back"] is True
        assert entry["moved"] == [{"database": "shop_db", "apps": [DOMAIN]}]


class TestMoving:
    def test_it_asks_for_sudo_mode(self, plain: TestClient, queued: list[dict[str, Any]]) -> None:
        response = plain.post("/api/databases/instances/migrate", json=MIGRATE)

        assert response.status_code in (401, 403)
        assert response.json().get("detail", {}) != "" and queued == []

    def test_what_cannot_succeed_is_refused_before_a_job_exists(
        self, admin: TestClient, lab: Lab, queued: list[dict[str, Any]]
    ) -> None:
        from noust.managers.database.records import DatabaseRecords

        DatabaseRecords(lab.world.store).delete_link(lab.app.id or 0, HOST, "shop_db")

        response = admin.post("/api/databases/instances/migrate", json=MIGRATE)

        assert response.status_code == 400
        assert "without a link" in response.text
        assert queued == []

    def test_the_job_moves_the_engine_and_its_result_carries_no_password(
        self, admin: TestClient, lab: Lab, queued: list[dict[str, Any]]
    ) -> None:
        response = admin.post("/api/databases/instances/migrate", json=MIGRATE)

        assert response.status_code == 202, response.text
        [job] = queued
        assert job["kwargs"]["name"] == "postgresql17" and job["kwargs"]["memory_mb"] == 1024
        result = run(job)
        assert result["migration"] == 1 and result["moved"][0]["apps"] == [DOMAIN]
        assert APP_PASSWORD not in str(result)
        assert lab.managed.get("postgresql17").state == "ready"

    def test_a_database_is_moved_by_a_job_too(
        self, admin: TestClient, lab: Lab, queued: list[dict[str, Any]]
    ) -> None:
        lab.managed.create("pg17", "postgresql", version="17", memory="1g")

        response = admin.post(
            "/api/databases/moves",
            json={"source": HOST, "database": "shop_db", "target": "postgresql@pg17"},
        )

        assert response.status_code == 202, response.text
        result = run(queued[0])
        assert result["apps"] == [DOMAIN] and result["source_dropped"] is False

    def test_a_blocked_database_move_is_refused_with_every_reason(
        self, admin: TestClient, lab: Lab, queued: list[dict[str, Any]]
    ) -> None:
        response = admin.post(
            "/api/databases/moves", json={"source": HOST, "database": "shop_db", "target": HOST}
        )

        assert response.status_code == 400
        assert "same engine" in response.text
        assert queued == []


class TestRollingBack:
    def test_it_is_a_job_and_points_the_applications_back(
        self, admin: TestClient, lab: Lab, queued: list[dict[str, Any]]
    ) -> None:
        done = lab.migrations.run(HOST, "17", memory="1g")

        response = admin.post(f"/api/databases/instances/migrations/{done.migration}/rollback")

        assert response.status_code == 202, response.text
        result = run(queued[0])
        assert result["repointed"] == [{"database": "shop_db", "apps": [DOMAIN]}]
        assert lab.env()["DATABASE_URL"].split("@")[1].startswith("localhost:5432/")

    def test_a_move_that_cannot_be_rolled_back_is_refused_at_once(
        self, admin: TestClient, queued: list[dict[str, Any]]
    ) -> None:
        response = admin.post("/api/databases/instances/migrations/7/rollback")

        assert response.status_code == 404
        assert queued == []
