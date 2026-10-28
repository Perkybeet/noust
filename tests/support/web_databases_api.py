# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_web_databases_api.py`` that other tests build on."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.managers.database.base import (
    BackupInfo,
    BaseDatabaseManager,
    DatabaseInfo,
    RestoreOutcome,
    UserInfo,
)
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager

#: The password the fake engine issues when the operator does not choose one.
ISSUED_PASSWORD = "Once-0nly-Generated-9!"

#: What the fake engine answers to any statement.
QUERY_OUTPUT = " count \n-------\n    42\n(1 row)"


def make_engine(
    backup_dir: Path,
    *,
    engine: str = "postgresql",
    display: str = "PostgreSQL",
    installed: bool = True,
    running: bool = True,
    seeded: bool = True,
) -> type[BaseDatabaseManager]:
    """
    Build a fresh fake engine manager class.

    State lives on the class because the API instantiates a manager per call:
    two requests must see the same databases, or a create would vanish on the
    very next read. A fresh class per test keeps tests isolated.

    Args:
        backup_dir: Where the engine pretends to keep its dumps.
        engine: Engine name the registry would answer to.
        display: Human-readable engine name.
        installed: Whether the engine starts installed.
        running: Whether the engine starts running.
        seeded: Whether to start with one database and one user.

    Returns:
        The fake manager class.
    """

    class Fake(BaseDatabaseManager):
        ENGINE_NAME = engine
        DISPLAY_NAME = display
        DEFAULT_PORT = 5432
        SERVICE_NAME = engine
        CLIENT_BINARY = "fake-client"
        VALID_PRIVILEGES = frozenset({"ALL PRIVILEGES", "DELETE", "INSERT", "SELECT", "UPDATE"})
        DEFAULT_PRIVILEGES = ("ALL PRIVILEGES",)
        CAPABILITIES = frozenset({"sql", "read_only", "users", "dump"})
        BACKUP_DIR = backup_dir

        state = {"installed": installed, "running": running}
        dbs: dict[str, dict[str, Any]] = (
            {"appdb": {"owner": "app", "size": "12 MB"}} if seeded else {}
        )
        users: dict[str, dict[str, Any]] = (
            {"app": {"databases": ["appdb"], "privileges": ["ALL PRIVILEGES"]}} if seeded else {}
        )
        calls: list[tuple[Any, ...]] = []

        def is_installed(self) -> bool:
            return type(self).state["installed"]

        def is_running(self) -> bool:
            return type(self).state["running"]

        def get_version(self) -> str | None:
            return "16.3" if self.is_installed() else None

        def start(self) -> None:
            type(self).calls.append(("start",))
            type(self).state["running"] = True

        def stop(self) -> None:
            type(self).calls.append(("stop",))
            type(self).state["running"] = False

        def restart(self) -> None:
            type(self).calls.append(("restart",))
            type(self).state["running"] = True

        def create_database(self, name, owner=None, encoding=None, **kwargs):
            cls = type(self)
            cls.calls.append(("create_database", name, owner))
            cls.dbs[name] = {"owner": owner, "size": None}
            return DatabaseInfo(name=name, engine=cls.ENGINE_NAME, owner=owner)

        def drop_database(self, name, force=False):
            cls = type(self)
            cls.calls.append(("drop_database", name))
            cls.dbs.pop(name, None)

        def database_exists(self, name):
            return name in type(self).dbs

        def list_databases(self):
            cls = type(self)
            return [
                DatabaseInfo(
                    name=name,
                    engine=cls.ENGINE_NAME,
                    size=entry.get("size"),
                    owner=entry.get("owner"),
                )
                for name, entry in cls.dbs.items()
            ]

        def get_database_info(self, name):
            entry = type(self).dbs[name]
            return DatabaseInfo(
                name=name,
                engine=type(self).ENGINE_NAME,
                size=entry.get("size"),
                owner=entry.get("owner"),
            )

        def create_user(self, username, password=None, host="localhost", **kwargs):
            cls = type(self)
            cls.calls.append(("create_user", username, password))
            cls.users[username] = {"databases": [], "privileges": []}
            user = UserInfo(username=username, engine=cls.ENGINE_NAME, host=host)
            return user, password or ISSUED_PASSWORD

        def drop_user(self, username, host="localhost"):
            cls = type(self)
            cls.calls.append(("drop_user", username))
            cls.users.pop(username, None)

        def user_exists(self, username, host="localhost"):
            return username in type(self).users

        def list_users(self):
            cls = type(self)
            return [
                UserInfo(
                    username=name,
                    engine=cls.ENGINE_NAME,
                    databases=list(entry["databases"]),
                    privileges=list(entry["privileges"]),
                )
                for name, entry in cls.users.items()
            ]

        def grant_privileges(self, username, database, privileges=None, host="localhost"):
            type(self).calls.append(
                ("grant", username, database, tuple(privileges) if privileges else None)
            )

        def revoke_privileges(self, username, database, privileges=None, host="localhost"):
            type(self).calls.append(
                ("revoke", username, database, tuple(privileges) if privileges else None)
            )

        def backup(self, database, output_path=None, compress=True, **kwargs):
            cls = type(self)
            cls.calls.append(("backup", database))
            # A real dump is a file: the dump job hashes and checks it.
            cls.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            (cls.BACKUP_DIR / f"{cls.ENGINE_NAME}-{database}-20260101_120000.sql.gz").write_bytes(
                b"gzipped dump"
            )
            return BackupInfo(
                path=cls.BACKUP_DIR / f"{cls.ENGINE_NAME}-{database}-20260101_120000.sql.gz",
                database=database,
                engine=cls.ENGINE_NAME,
                size=2048,
                created=datetime(2026, 1, 1, 12, 0),
                compressed=True,
            )

        def restore(self, database, backup_path, drop_existing=False, **kwargs):
            type(self).calls.append(("restore", database, Path(backup_path).name, drop_existing))
            return RestoreOutcome(
                database=database,
                source=Path(backup_path),
                safety_copy=None,
                replaced=drop_existing,
            )

        def _load_backup(self, database, backup_path, **kwargs):
            type(self).calls.append(("load", database, Path(backup_path).name))

        def execute_query(self, database, query, **kwargs):
            type(self).calls.append(("query", database, query, kwargs.get("read_only")))
            return True, QUERY_OUTPUT

        def get_connection_string(self, database, username, password, host="localhost"):
            port = type(self).DEFAULT_PORT
            return f"postgresql://{username}:{password}@{host}:{port}/{database}"

    return Fake


def wire(monkeypatch: pytest.MonkeyPatch, engine_classes: list[type]) -> None:
    """
    Stand the fake engines in front of the database service the API calls.

    Args:
        monkeypatch: Patching helper, scoped to the test.
        engine_classes: The fake manager classes to expose.
    """
    import noust.managers.database.service as db_service

    by_name = {cls.ENGINE_NAME: cls for cls in engine_classes}

    def fake_get(engine: str, verbose: bool = False):
        cls = by_name.get(engine.lower())
        return cls(verbose=verbose) if cls else None

    class FakeRegistry:
        @staticmethod
        def list_engines() -> list[str]:
            return list(by_name)

        @staticmethod
        def get_installed(verbose: bool = False) -> list[Any]:
            return [cls() for cls in by_name.values() if cls.state["installed"]]

    monkeypatch.setattr(db_service, "get_db_manager", fake_get)
    monkeypatch.setattr(db_service, "DatabaseRegistry", FakeRegistry)


@pytest.fixture
def engines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> dict[str, type[BaseDatabaseManager]]:
    """
    A running PostgreSQL fake and an uninstalled MySQL fake, wired in.

    Args:
        monkeypatch: Patching helper, scoped to the test.
        tmp_path: Per-test temporary directory, for the dump directories.

    Returns:
        The fake classes by engine name.
    """
    postgres = make_engine(tmp_path / "pg-dumps")
    mysql = make_engine(
        tmp_path / "my-dumps",
        engine="mysql",
        display="MySQL",
        installed=False,
        running=False,
        seeded=False,
    )
    wire(monkeypatch, [postgres, mysql])
    return {"postgresql": postgres, "mysql": mysql}


@pytest.fixture
def app(tmp_path: Path, runner: object) -> FastAPI:
    """
    Args:
        tmp_path: Per-test temporary directory.
        runner: The fake command runner, so no manager reaches a real process.

    Returns:
        The application.
    """
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """
    Args:
        app: The application.

    Returns:
        A signed-in client carrying the CSRF header.
    """
    signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    response = signed_in.post("/api/auth/login", json={"token": token})
    assert response.status_code == 200, response.text
    signed_in.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return signed_in


@pytest.fixture
def anonymous(app: FastAPI) -> TestClient:
    """
    Args:
        app: The application.

    Returns:
        A client with no session.
    """
    return TestClient(app, client=("testclient", 50000), follow_redirects=False)


def elevate(client: TestClient) -> None:
    """
    Confirm sudo mode for the signed-in client.

    Args:
        client: A signed-in client.
    """
    response = client.post(
        "/api/auth/elevate", json={"token": get_token_manager().generate_master_token()}
    )
    assert response.status_code == 200, response.text


def _capture_queued_jobs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """
    Stand in for the job manager, so uninstalling an engine never really runs.

    Args:
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The keyword arguments of every job the endpoint tried to queue - empty
        when a request never got past the elevation gate.
    """
    import noust.web.api.databases.common as db_api

    created: list[dict[str, Any]] = []

    def create_job(**kwargs: Any) -> SimpleNamespace:
        created.append(kwargs)
        return SimpleNamespace(
            id="job-42",
            status=SimpleNamespace(value="pending"),
            to_dict=lambda: {"id": "job-42", "status": "pending"},
        )

    monkeypatch.setattr(db_api, "get_job_manager", lambda: SimpleNamespace(create_job=create_job))
    return created
