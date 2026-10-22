# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``DELETE /api/apps/{domain}`` takes the same explicit choices the command does (checklist B-83).

The volumes and the databases of an application hold its data, so a request that does not name
them removes neither.
"""

# The env API harness is imported rather than replicated.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.store import NoustStore
from tests.test_web_apps_env_secrets import (  # noqa: F401  (pytest resolves fixtures by name)
    app,
    client,
    deployed_env,
    elevate,
    store,
)


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def create_job(**kwargs: Any) -> Any:
        captured.append(kwargs)

        class Queued:
            id = "job-1"
            status = type("Status", (), {"value": "pending"})()

            def to_dict(self) -> dict[str, Any]:
                return {"id": self.id}

        return Queued()

    manager = type("FakeJobs", (), {"create_job": staticmethod(create_job)})()
    monkeypatch.setattr("noust.web.api.apps.get_job_manager", lambda: manager)
    return captured


def test_a_bare_request_removes_neither_the_volumes_nor_the_databases(
    client: TestClient, store: NoustStore, tmp_path: Path, queued: list[dict[str, Any]]
) -> None:
    deployed_env(store, tmp_path)
    elevate(client)

    response = client.delete("/api/apps/example.com")

    assert response.status_code == 202, response.text
    kwargs = queued[0]["kwargs"]
    assert kwargs["remove_volumes"] is False and kwargs["remove_databases"] is False


def test_each_choice_reaches_the_job(
    client: TestClient, store: NoustStore, tmp_path: Path, queued: list[dict[str, Any]]
) -> None:
    deployed_env(store, tmp_path)
    elevate(client)

    response = client.delete(
        "/api/apps/example.com?remove_volumes=true&remove_databases=true&remove_ssl=true"
    )

    assert response.status_code == 202, response.text
    kwargs = queued[0]["kwargs"]
    assert kwargs["remove_volumes"] is True
    assert kwargs["remove_databases"] is True
    assert kwargs["remove_ssl"] is True


def test_it_needs_sudo_mode(client: TestClient, store: NoustStore, tmp_path: Path) -> None:
    deployed_env(store, tmp_path)

    assert client.delete("/api/apps/example.com?remove_databases=true").status_code == 403


def test_the_plan_names_the_databases_and_what_would_become_of_each(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    from noust.core.store import App, Database
    from noust.managers.database.records import DatabaseLink, DatabaseRecords

    directory = deployed_env(store, tmp_path)
    mine = store.get_app("example.com")
    assert mine is not None and mine.id is not None
    store.create_database(Database(app_id=mine.id, name="shop", engine="postgresql"))
    store.create_database(Database(app_id=mine.id, name="shared", engine="postgresql"))
    other = store.create_app(App(domain="other.example.com", app_type="nodejs", app_path="/x"))
    assert other.id is not None
    DatabaseRecords(store).save_link(
        DatabaseLink(app_id=other.id, engine="postgresql", db_name="shared")
    )

    response = client.get("/api/apps/example.com/deletion")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["path"] == str(directory)
    by_name = {item["name"]: item for item in data["databases"]}
    assert by_name["shop"]["will_drop"] is True
    assert by_name["shared"]["will_drop"] is False
    assert by_name["shared"]["shared_with"] == ["other.example.com"]
