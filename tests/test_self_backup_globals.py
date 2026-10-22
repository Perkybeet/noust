# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The roles of each PostgreSQL server travel in the bundle of Noust's state (checklist H-28, H-30).

A database no application owns is restored by ``restore-server`` from its dump, and the dump needs
its owners. The bundle is the one file that always reaches the new server, sealed under the
operator's passphrase, so the roles (with their password hashes) travel in it and are put in the
state directory, where the restore reads them.
"""

# The fixtures are imported from the tests of the thing they set up.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import DatabaseBackupError
from noust.core.store import Database, NoustStore
from noust.managers.self_backup import SelfBackupManager
from noust.managers.self_restore import SelfRestore
from tests.test_self_backup import (  # noqa: F401
    PASSPHRASE,
    Runner,
    _reset_store,
    cheap_scrypt,
    origin,
    runner,
    server,
)

pytestmark = pytest.mark.allow_subprocess


class Engine:
    """A PostgreSQL manager that can dump its roles."""

    HAS_GLOBALS = True

    def __init__(self, key: str, fail: bool = False) -> None:
        self.key = key
        self.fail = fail

    def backup_globals(self, output_path: Path | None = None, compress: bool = True) -> Any:
        if self.fail:
            raise DatabaseBackupError("pg_dumpall: connection refused")
        assert output_path is not None and not compress
        output_path.write_text(f"CREATE ROLE owner_of_{self.key.replace('@', '_')};\n")

        class _Info:
            path = output_path

        return _Info()


class Mysql:
    HAS_GLOBALS = False


class Service:
    """The database service the bundle asks for each engine's manager."""

    def __init__(self, failing: set[str] | None = None) -> None:
        self.failing = failing or set()
        self.asked: list[str] = []

    def manager(self, engine: str) -> Any:
        self.asked.append(engine)
        if engine == "mysql":
            return Mysql()
        return Engine(engine, fail=engine in self.failing)


def with_databases(made: Any, *rows: tuple[str, str]) -> NoustStore:
    """Give the origin server these databases, none of them an application's."""
    NoustStore.reset_instance()
    store = NoustStore(made.state / "noust.db")
    for engine, name in rows:
        store.create_database(Database(name=name, engine=engine, app_id=None))
    return store


class TestTheBundle:
    def test_each_postgres_server_with_a_database_has_its_roles_in_it(
        self, tmp_path: Path, runner: Runner
    ) -> None:
        made = origin(tmp_path)
        store = with_databases(
            made, ("postgresql", "shop"), ("postgresql", "blog"), ("mysql", "wiki")
        )
        service = Service()
        manager = made.manager(runner, store=store, database_service=lambda: service)
        try:
            bundle = manager.create(PASSPHRASE).path

            _root, manifest = manager.open_bundle(bundle, PASSPHRASE, tmp_path / "open")
            assert manifest["pg_globals"] == [
                {"engine": "postgresql", "path": "pg-globals/postgresql.sql"}
            ]
            paths = {entry["path"] for entry in manifest["files"]}
            assert "pg-globals/postgresql.sql" in paths
            assert "pg-globals" in manifest["sources"]
            assert manifest["contains"]["pg_globals"] == 1
            # Only a PostgreSQL server has roles of its own to ask for.
            assert set(service.asked) == {"postgresql"}
        finally:
            store.close()
            for connection in made.held:
                connection.close()

    def test_a_server_whose_roles_cannot_be_dumped_is_said_and_does_not_fail_the_bundle(
        self, tmp_path: Path, runner: Runner
    ) -> None:
        made = origin(tmp_path)
        store = with_databases(made, ("postgresql", "shop"))
        service = Service(failing={"postgresql"})
        manager = made.manager(runner, store=store, database_service=lambda: service)
        try:
            bundle = manager.create(PASSPHRASE).path

            _root, manifest = manager.open_bundle(bundle, PASSPHRASE, tmp_path / "open")
            assert manifest["pg_globals"] == []
            [skipped] = [s for s in manifest["skipped"] if s["path"].startswith("pg-globals")]
            assert "connection refused" in skipped["reason"]
        finally:
            store.close()
            for connection in made.held:
                connection.close()

    def test_a_server_with_no_postgres_database_has_no_roles_to_dump(
        self, tmp_path: Path, runner: Runner
    ) -> None:
        made = origin(tmp_path)
        store = with_databases(made)
        service = Service()
        manager = made.manager(runner, store=store, database_service=lambda: service)
        try:
            bundle = manager.create(PASSPHRASE).path

            _root, manifest = manager.open_bundle(bundle, PASSPHRASE, tmp_path / "open")
            assert manifest["pg_globals"] == []
            assert service.asked == []
        finally:
            store.close()
            for connection in made.held:
                connection.close()


class TestARestore:
    def test_the_roles_land_in_the_state_directory_private(
        self, tmp_path: Path, runner: Runner
    ) -> None:
        made = origin(tmp_path)
        store = with_databases(made, ("postgresql", "shop"))
        service = Service()
        manager = made.manager(runner, store=store, database_service=lambda: service)
        try:
            bundle = manager.create(PASSPHRASE).path
            fresh = server(tmp_path, "fresh")
            replacement = fresh.manager(runner)

            SelfRestore(replacement, restart=False).restore(str(bundle), PASSPHRASE)

            placed = fresh.state / "pg-globals" / "postgresql.sql"
            assert placed.read_text() == "CREATE ROLE owner_of_postgresql;\n"
            assert placed.stat().st_mode & 0o077 == 0
        finally:
            store.close()
            for connection in made.held:
                connection.close()


def test_the_manager_still_builds_without_a_database_service_argument(runner: Runner) -> None:
    assert SelfBackupManager(runner=runner, sources={}, webservers={}).webservers == {}
