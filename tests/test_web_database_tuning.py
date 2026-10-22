# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/instances/{name}/tuning`` (checklist C-30, C-31).

The contract: reading the settings and planning answer at once and change nothing; changing them
restarts an engine, so it asks for sudo mode and runs as a job; what the server could not honour
is refused when the request arrives, not in a failed job.
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
import noust.web.api.databases.tuning as tuning_module
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager
from tests.database_instance_support import World, build_world, close_world

URL = "/api/databases/instances/pg16/tuning"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    built = build_world(tmp_path, monkeypatch)
    built.managed.create("pg16", "postgresql", version="16", memory="1g")
    yield built
    close_world()


@pytest.fixture
def app(tmp_path: Path, world: World, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr(
        tuning_module, "service", lambda session=None, logger=None: world.managed.service
    )
    monkeypatch.setattr(
        jobs_module, "_service", lambda job_context, step, actor: world.managed.service
    )
    monkeypatch.setattr(
        "noust.managers.database.managed.server_resources", world.managed._resources
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


class TestReading:
    def test_it_says_what_the_instance_is_held_to_and_how_it_is_doing(
        self, plain: TestClient
    ) -> None:
        response = plain.get(URL)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["key"] == "postgresql@pg16"
        assert body["limits"] == {"memory_mb": 1024, "cpus": None, "swap_mb": None}
        assert (
            body["probe"] == body["defaults"] == {"interval_s": 10, "timeout_s": 5, "retries": 12}
        )
        assert body["server"]["cpus"] == 4
        assert body["health"]["status"] == "healthy"

    def test_an_unknown_instance_is_a_404(self, plain: TestClient) -> None:
        assert plain.get("/api/databases/instances/nope/tuning").status_code == 404

    def test_a_plan_lists_what_changes_and_changes_nothing(
        self, plain: TestClient, world: World
    ) -> None:
        response = plain.post(URL + "/plan", json={"memory_mb": 2048, "cpus": 1.5, "retries": 5})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["restarts"] is True and body["blockers"] == []
        assert {change["setting"]: change["to"] for change in body["changes"]} == {
            "memory": 2048,
            "cpus": 1.5,
            "retries": 5,
        }
        assert world.managed.get("pg16").memory_limit_mb == 1024

    def test_what_the_server_could_not_honour_is_a_400_saying_why(self, plain: TestClient) -> None:
        response = plain.post(URL + "/plan", json={"cpus": 64})

        assert response.status_code == 400
        assert "more than this server has" in response.text

    def test_an_interval_shorter_than_the_timeout_is_refused(self, plain: TestClient) -> None:
        response = plain.post(URL + "/plan", json={"interval_s": 3, "timeout_s": 4})

        assert response.status_code == 400
        assert "longer than the time between probes" in response.text

    def test_a_default_that_does_not_exist_is_refused(self, plain: TestClient) -> None:
        response = plain.post(URL + "/plan", json={"clear": ["memory"]})

        assert response.status_code == 400


class TestChanging:
    def test_it_asks_for_sudo_mode(self, plain: TestClient, queued: list[dict[str, Any]]) -> None:
        response = plain.put(URL, json={"cpus": 1})

        assert response.status_code in (401, 403) and queued == []

    def test_the_job_restarts_the_engine_on_the_new_numbers(
        self, admin: TestClient, world: World, queued: list[dict[str, Any]]
    ) -> None:
        response = admin.put(URL, json={"memory_mb": 2048, "cpus": 2, "swap_mb": 0})

        assert response.status_code == 202, response.text
        [job] = queued
        result = run(job)
        assert result["limits"] == {"memory_mb": 2048, "cpus": 2.0, "swap_mb": 0}
        assert world.host.containers["noust-db-pg16"]["_limits"] == {
            "memory": "2048M",
            "cpus": "2",
        }

    def test_a_default_is_put_back_by_the_job(
        self, admin: TestClient, queued: list[dict[str, Any]]
    ) -> None:
        admin.put(URL, json={"cpus": 2, "interval_s": 20})
        run(queued[0])

        admin.put(URL, json={"clear": ["cpus", "interval"]})
        result = run(queued[1])

        assert result["limits"]["cpus"] is None and result["probe"]["interval_s"] == 10

    def test_what_cannot_succeed_is_refused_before_a_job_exists(
        self, admin: TestClient, world: World, queued: list[dict[str, Any]]
    ) -> None:
        world.managed.records.set_state("pg16", "upgrading")

        response = admin.put(URL, json={"cpus": 1})

        assert response.status_code == 400
        assert "upgrading" in response.text
        assert queued == []

    def test_a_value_out_of_range_is_a_validation_error_and_no_job(
        self, admin: TestClient, queued: list[dict[str, Any]]
    ) -> None:
        response = admin.put(URL, json={"cpus": 0})

        assert response.status_code in (400, 422)
        assert queued == []
