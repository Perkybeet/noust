# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/engines/catalog``, ``.../install`` with a choice, and ``.../settings`` (3.3, item 71).

The routes are a client of the service: what is pinned here is the HTTP side
of it. Reading needs ``databases.read``; changing settings needs
``databases.manage`` and sudo mode; an install's choice reaches the job, and
a choice this server cannot have is refused before anything is queued.
"""

# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.exceptions import ValidationError
from noust.managers.database import flavours
from noust.managers.database.service import DatabaseService
from noust.managers.server.host import HostPaths
from noust.web.permissions import Permission
from noust.web.permissions.routes_databases import ROUTES
from tests.test_web_databases_api import (  # noqa: F401 - fixtures
    anonymous,
    app,
    client,
    elevate,
    engines,
)


@pytest.fixture
def noble(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Make the server Ubuntu 24.04.

    Returns:
        The system root.
    """
    root = tmp_path / "root"
    (root / "etc").mkdir(parents=True)
    (root / "etc" / "os-release").write_text(
        'ID=ubuntu\nVERSION_CODENAME=noble\nPRETTY_NAME="Ubuntu 24.04.3 LTS"\n'
    )
    monkeypatch.setattr(flavours, "HOST", HostPaths(root))
    return root


def capture_jobs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """
    Stand in for the job manager.

    Returns:
        The keyword arguments of every job created.
    """
    import noust.web.api.databases.common as db_api

    created: list[dict[str, Any]] = []

    def create_job(**kwargs: Any) -> SimpleNamespace:
        created.append(kwargs)
        return SimpleNamespace(
            id="job-71",
            status=SimpleNamespace(value="pending"),
            to_dict=lambda: {"id": "job-71", "status": "pending"},
        )

    monkeypatch.setattr(db_api, "get_job_manager", lambda: SimpleNamespace(create_job=create_job))
    return created


def test_every_new_route_declares_its_permission() -> None:
    assert ROUTES[("GET", "/api/databases/engines/catalog")] == Permission.DATABASES_READ
    assert ROUTES[("GET", "/api/databases/engines/{engine}/settings")] == Permission.DATABASES_READ
    assert (
        ROUTES[("PUT", "/api/databases/engines/{engine}/settings")] == Permission.DATABASES_MANAGE
    )


def test_the_catalog_tells_the_console_what_this_server_can_have(
    client: TestClient, engines, noble: Path
) -> None:
    response = client.get("/api/databases/engines/catalog")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["distribution"]["codename"] == "noble"
    by_name = {entry["flavour"]: entry for entry in body["flavours"]}
    assert set(by_name) == {"postgresql", "mysql", "mariadb", "redis", "valkey", "mongodb"}
    assert by_name["postgresql"]["blocked"] == "installed"
    assert [v["version"] for v in by_name["postgresql"]["versions"]] == [
        "14",
        "15",
        "16",
        "17",
        "18",
    ]


def test_the_catalog_needs_a_session(anonymous: TestClient, engines) -> None:
    assert anonymous.get("/api/databases/engines/catalog").status_code in (401, 403)


def test_an_install_with_a_choice_hands_it_to_the_job(
    client: TestClient, engines, noble: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = capture_jobs(monkeypatch)

    response = client.post(
        "/api/databases/engines/mysql/install", json={"flavour": "mariadb", "version": "11.4"}
    )

    assert response.status_code == 202, response.text
    assert created[0]["kwargs"] == {
        "engine": "mysql",
        "action": "install",
        "flavour": "mariadb",
        "version": "11.4",
    }
    assert created[0]["name"] == "Install MariaDB 11.4"
    assert created[0]["metadata"]["plan"]["source"] == "upstream"


def test_the_path_can_name_the_flavour(
    client: TestClient, engines, noble: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import noust.managers.database.service as db_service

    created = capture_jobs(monkeypatch)
    resolve = db_service.get_db_manager
    # The registry answers to "mariadb" with the MySQL manager; so does the fake.
    monkeypatch.setattr(
        db_service,
        "get_db_manager",
        lambda engine, verbose=False: resolve("mysql" if engine == "mariadb" else engine, verbose),
    )

    response = client.post("/api/databases/engines/mariadb/install")

    assert response.status_code == 202, response.text
    assert created[0]["kwargs"] == {"engine": "mysql", "action": "install", "flavour": "mariadb"}


def test_a_version_this_server_cannot_have_is_refused_before_queueing(
    client: TestClient, engines, noble: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = capture_jobs(monkeypatch)

    response = client.post(
        "/api/databases/engines/mysql/install", json={"flavour": "mariadb", "version": "10.3"}
    )

    assert response.status_code == 400, response.text
    assert response.json()["fields"] == {"version": "MariaDB 10.3 is not a version Noust installs"}
    assert created == []


def test_reading_settings_returns_the_report(
    client: TestClient, engines, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = {
        "engine": "postgresql",
        "display_name": "PostgreSQL",
        "file": "/etc/postgresql/16/main/conf.d/90-noust.conf",
        "running": True,
        "memory_bytes": 8 * 1024**3,
        "cpus": 4,
        "settings": [
            {
                "key": "shared_buffers",
                "kind": "size",
                "unit": "MB",
                "description": "Memory PostgreSQL keeps for its own cache.",
                "current": "128MB",
                "configured": None,
                "recommended": "2GB",
                "restart": True,
                "choices": [],
                "minimum": 16777216.0,
                "maximum": None,
                "listen": False,
                "editable": True,
                "locked_reason": None,
                "source": "/etc/postgresql/16/main/postgresql.conf",
            }
        ],
    }
    monkeypatch.setattr(
        DatabaseService,
        "engine_settings",
        lambda self, engine: SimpleNamespace(to_dict=lambda: report),
    )

    response = client.get("/api/databases/engines/postgresql/settings")

    assert response.status_code == 200, response.text
    assert response.json() == report


def test_changing_settings_needs_sudo_mode(
    client: TestClient, engines, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, dict[str, str], bool]] = []

    def change(self, engine, values, *, confirm_exposure=False):
        calls.append((engine, dict(values), confirm_exposure))
        return SimpleNamespace(
            to_dict=lambda: {
                "engine": "postgresql",
                "display_name": "PostgreSQL",
                "file": "/etc/postgresql/16/main/conf.d/90-noust.conf",
                "changed": ["work_mem"],
                "action": "reload",
                "exposed": False,
                "warnings": [],
            }
        )

    monkeypatch.setattr(DatabaseService, "change_engine_settings", change)
    body = {"values": {"work_mem": "64MB"}}

    refused = client.put("/api/databases/engines/postgresql/settings", json=body)
    assert refused.status_code == 403, refused.text
    assert calls == []

    elevate(client)
    accepted = client.put("/api/databases/engines/postgresql/settings", json=body)

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["action"] == "reload"
    assert calls == [("postgresql", {"work_mem": "64MB"}, False)]


def test_an_exposure_to_confirm_comes_back_on_its_field(
    client: TestClient, engines, monkeypatch: pytest.MonkeyPatch
) -> None:
    def change(self, engine, values, *, confirm_exposure=False):
        raise ValidationError(
            "PostgreSQL will accept connections from beyond this server (10.0.0.5).",
            details="Confirm it to go ahead.",
            field="confirm_exposure",
        )

    monkeypatch.setattr(DatabaseService, "change_engine_settings", change)
    elevate(client)

    response = client.put(
        "/api/databases/engines/postgresql/settings",
        json={"values": {"listen_addresses": "10.0.0.5"}},
    )

    assert response.status_code == 400
    assert "confirm_exposure" in response.json()["fields"]
