# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/cron`` and the tasks that run once (item 164).

Pinned: a moment is judged in the request model with the manager's own words (a 422 naming
``once_at``), what is written is the manager's, a task of one time lists as that, the preview shows
the moment on the server's clock with its offset, and the history is read like the rest of the page
and is not mistaken for a task called ``history``.

This file is also read by the ``pydantic-v1`` CI job.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.cron_once_history import CronOnceHistory, OnceRun
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.managers.cron_manager import CronManager
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager

CET = "CET-1CEST,M3.5.0,M10.5.0/3"


@pytest.fixture(autouse=True)
def madrid(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TZ", CET)
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture(autouse=True)
def noust_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "noust.managers.cron_manager.find_noust_executable", lambda: "/usr/bin/noust"
    )


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
def app(tmp_path: Path, store: NoustStore, runner: FakeRunner) -> FastAPI:
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    response = signed_in.post("/api/auth/login", json={"token": token})
    assert response.status_code == 200, response.text
    signed_in.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    elevated = signed_in.post("/api/auth/elevate", json={"token": token})
    assert elevated.status_code == 200, elevated.text
    return signed_in


@pytest.fixture
def systemd_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "systemd"
    path.mkdir()
    monkeypatch.setattr(CronManager, "SYSTEMD_DIR", path)
    return path


def ahead(**delta: float) -> datetime:
    moment = datetime.now().astimezone() + timedelta(**delta)
    return moment.replace(second=0, microsecond=0)


def body(at: datetime | str, **extra: object) -> dict:
    return {
        "name": "reindex",
        "command": "/usr/bin/true",
        "user": "www-data",
        "once_at": at if isinstance(at, str) else at.strftime("%Y-%m-%d %H:%M"),
        **extra,
    }


class TestCreate:
    def test_a_task_of_one_time_is_created_and_comes_back_as_that(
        self, client: TestClient, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        at = ahead(days=2)
        runner.script(["systemctl", "is-enabled"], stdout="enabled\n")

        response = client.post("/api/cron", json=body(at))

        assert response.status_code == 201, response.text
        job = response.json()["job"]
        assert job["once"] is True and job["schedule"] == "once"
        assert job["once_at"] == at.isoformat(timespec="seconds")
        assert (
            f"OnCalendar={at.strftime('%Y-%m-%d %H:%M:00')}"
            in (systemd_dir / "noust-cron-reindex.timer").read_text()
        )

    def test_a_moment_that_has_passed_is_a_422_that_names_the_field_and_the_time_here(
        self, client: TestClient, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        response = client.post("/api/cron", json=body(ahead(hours=-3)))

        assert response.status_code == 422, response.text
        message = response.json()["fields"]["once_at"]
        assert "has already passed" in message and "It is " in message
        assert list(systemd_dir.iterdir()) == []

    @pytest.mark.parametrize(
        "text", ["tomorrow", "2030-01-01 10:00\nOnBootSec=1", "2030-13-01 10:00"]
    )
    def test_what_is_not_a_moment_is_a_422_and_writes_nothing(
        self, client: TestClient, runner: FakeRunner, systemd_dir: Path, text: str
    ) -> None:
        response = client.post("/api/cron", json=body(text))

        assert response.status_code == 422
        assert "once_at" in response.json()["fields"]
        assert list(systemd_dir.iterdir()) == []

    def test_a_task_that_repeats_is_created_as_it_always_was(
        self, client: TestClient, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        response = client.post(
            "/api/cron",
            json={
                "name": "reindex",
                "command": "/usr/bin/true",
                "user": "www-data",
                "schedule": "daily",
            },
        )

        assert response.status_code == 201, response.text
        assert response.json()["job"]["once"] is False


class TestPreview:
    def test_the_moment_is_shown_with_the_offset_of_the_servers_clock(
        self, client: TestClient, runner: FakeRunner
    ) -> None:
        at = ahead(days=3)

        response = client.post("/api/cron/preview", json={"once_at": at.strftime("%Y-%m-%d %H:%M")})

        assert response.status_code == 200, response.text
        assert response.json() == {
            "calendar": at.strftime("%Y-%m-%d %H:%M:00"),
            "next_runs": [at.isoformat(timespec="seconds")],
        }

    def test_a_moment_that_has_passed_is_refused_like_in_create(
        self, client: TestClient, runner: FakeRunner
    ) -> None:
        response = client.post(
            "/api/cron/preview", json={"once_at": ahead(hours=-1).strftime("%Y-%m-%d %H:%M")}
        )

        assert response.status_code == 422
        assert "has already passed" in response.json()["fields"]["once_at"]


class TestHistory:
    def seed(self, store: NoustStore) -> None:
        CronOnceHistory(store).record(
            OnceRun(
                name="reindex",
                command="/usr/bin/php artisan search:reindex",
                run_at="2026-12-01T14:30:00+01:00",
                result="exit-code",
                exit_code=2,
                late=True,
                app_domain="shop.example.com",
                started_at="2026-12-01T13:40:00+00:00",
                finished_at="2026-12-01T13:41:00+00:00",
                output="ERROR: disk full\n",
            )
        )

    def test_the_runs_come_back_newest_first_with_what_they_printed(
        self, client: TestClient, store: NoustStore, runner: FakeRunner
    ) -> None:
        self.seed(store)

        response = client.get("/api/cron/history")

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["total"] == 1 and data["days"] == 30 and data["zone"].startswith("UTC+0")
        [item] = data["items"]
        assert item["name"] == "reindex" and item["ok"] is False and item["late"] is True
        assert item["exit_code"] == 2 and item["output"] == "ERROR: disk full\n"
        assert item["app_domain"] == "shop.example.com"
        assert item["run_at"] == "2026-12-01T14:30:00+01:00"

    def test_nothing_has_run(self, client: TestClient, runner: FakeRunner) -> None:
        assert client.get("/api/cron/history").json()["items"] == []

    def test_it_needs_a_session(self, app: FastAPI) -> None:
        anonymous = TestClient(app, client=("testclient", 50000), follow_redirects=False)

        assert anonymous.get("/api/cron/history").status_code == 401

    def test_a_task_named_history_is_still_deletable_and_the_route_is_not_its_name(
        self, client: TestClient, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        # GET /history is the record; DELETE /history is the task of that name, which does not exist.
        assert client.delete("/api/cron/history").status_code == 404
