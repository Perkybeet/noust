# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/server/restore`` (checklist H-28, H-30).

The contract: the plan answers at once and changes nothing; restoring installs software and puts
every application back, so it asks for sudo mode and runs as a job; and the job never touches the
state (restoring it replaces the store the console runs on), whatever the request says.
"""

# The fixtures are imported from the tests of the thing they set up.

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import noust.web.api.databases.common as db_common
import noust.web.api.server_restore as restore_module
from noust.core import audit
from noust.core.store import App, Database, NoustStore
from noust.managers.backup_manager import BackupMetadata
from noust.managers.restore_databases import DatabaseOutcome
from noust.managers.restore_server import RestoreServer, ServerRestoreReport
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager
from tests.test_restore_server import Machine

pytestmark = pytest.mark.allow_subprocess


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    opened = NoustStore(tmp_path / "noust.db")
    NoustStore._instance = opened
    yield opened
    opened.close()
    NoustStore.reset_instance()


class Backups:
    def list_backups(self, domain: str | None = None, **_: Any) -> list[BackupMetadata]:
        if domain != "shop.example.com":
            return []
        return [
            BackupMetadata(
                id="shop-example-com_20260101_030000",
                domain=domain,
                app_name="shop-example-com",
                created_at="2026-01-01T03:00:00+00:00",
                size_bytes=10,
                app_type="nodejs",
                version="1",
                description="",
                includes_env=True,
                includes_node_modules=False,
            )
        ]


class Orphans:
    def __init__(self) -> None:
        self.asked: list[str | None] = []

    def plan(self, store: NoustStore, *, from_destination: str | None = None) -> list[Any]:
        self.asked.append(from_destination)
        return [DatabaseOutcome("postgresql", "shared", "ready", source="nas", dump="s.dump")]


@pytest.fixture
def orphans() -> Orphans:
    return Orphans()


@pytest.fixture
def app(
    tmp_path: Path,
    store: NoustStore,
    orphans: Orphans,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    monkeypatch.setattr(audit, "record", lambda *args, **kwargs: None)
    shop = store.create_app(App(domain="shop.example.com", app_type="nodejs", app_path="/srv/shop"))
    store.create_database(Database(name="shop", engine="postgresql", app_id=shop.id))
    store.create_database(Database(name="shared", engine="postgresql", app_id=None))

    def build(**kwargs: Any) -> RestoreServer:
        return RestoreServer(
            backups=Backups(),  # type: ignore[arg-type]
            provisioner=Machine(),  # type: ignore[arg-type]
            orphans=orphans,  # type: ignore[arg-type]
            engine_installed=lambda engine: True,
            webserver_name=lambda: "nginx",
            **kwargs,
        )

    monkeypatch.setattr(restore_module, "RestoreServer", build)
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


class TestThePlan:
    def test_it_says_what_is_missing_what_waits_and_where_each_thing_would_come_from(
        self, plain: TestClient, orphans: Orphans
    ) -> None:
        response = plain.get("/api/server/restore/plan", params={"from_destination": "nas"})

        assert response.status_code == 200, response.text
        body = response.json()
        assert [r["name"] for r in body["requirements"]] == ["nginx"]
        assert body["requirements"][0]["state"] == "present"
        assert body["databases"][0]["status"] == "ready"
        assert body["databases"][0]["dump"] == "s.dump"
        assert body["apps"][0]["domain"] == "shop.example.com"
        assert body["apps"][0]["backup_id"] == "shop-example-com_20260101_030000"
        assert body["apps"][0]["source"] == "local"
        assert orphans.asked == ["nas"]

    def test_it_changes_nothing(self, plain: TestClient, store: NoustStore) -> None:
        before = [(a.domain, a.app_type) for a in store.list_apps()]

        plain.get("/api/server/restore/plan")

        assert [(a.domain, a.app_type) for a in store.list_apps()] == before


class TestRestoring:
    def test_it_asks_for_sudo_mode(self, plain: TestClient, queued: list[dict[str, Any]]) -> None:
        response = plain.post("/api/server/restore", json={})

        assert response.status_code in (401, 403) and queued == []

    def test_it_is_a_job_with_what_was_asked_for(
        self, admin: TestClient, queued: list[dict[str, Any]]
    ) -> None:
        response = admin.post(
            "/api/server/restore",
            json={
                "install": True,
                "databases": False,
                "from_destination": "nas",
                "apps": ["shop.example.com"],
                "skip": ["blog.example.com"],
                "accept_schema_changes": True,
            },
        )

        assert response.status_code == 202, response.text
        [job] = queued
        assert job["kwargs"]["install"] is True and job["kwargs"]["databases"] is False
        assert job["kwargs"]["apps"] == ["shop.example.com"]
        assert job["kwargs"]["from_destination"] == "nas"
        assert job["metadata"]["kind"] == "server-restore"
        assert str(job["job_type"].value) == "custom", "a failed one is not announced as a backup"

    def test_the_job_never_touches_the_state_here(
        self, monkeypatch: pytest.MonkeyPatch, store: NoustStore
    ) -> None:
        seen: dict[str, Any] = {}

        def run(self: RestoreServer, *args: Any, **kwargs: Any) -> ServerRestoreReport:
            seen.update(kwargs, args=args)
            return ServerRestoreReport()

        monkeypatch.setattr(RestoreServer, "run", run)

        result = restore_module.restore_job(install=True, apps=["a.example.com"])

        assert seen["skip_state"] is True and seen["args"] == ()
        assert seen["only"] == ["a.example.com"] and seen["install"] is True
        assert result["ok"] is True

    def test_a_request_cannot_ask_for_the_state_to_be_restored(self, admin: TestClient) -> None:
        response = admin.post("/api/server/restore", json={"skip_state": False, "force": True})

        # Unknown fields are not a way to reach the store: the job takes the state as it is.
        assert response.status_code in (202, 422)
        fields = getattr(restore_module.RestoreRequest, "model_fields", None)
        if fields is None:
            fields = restore_module.RestoreRequest.__fields__
        assert "skip_state" not in fields and "force" not in fields
