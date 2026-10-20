# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application backup carries the roles of the PostgreSQL server its database lives on (checklist H-28).

The dump of a database names its owners; the roles are the server's. A backup that takes the
dump and not the roles restores into a server that lacks them, and the owners are lost. So
the archive holds the globals beside the dump (once per server, however many databases use
it), the manifest lists them apart from the dumps, and a restore puts the missing roles back
before it loads the dump.
"""

# The fixtures are imported from the tests of the thing they set up.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import DatabaseBackupError
from noust.managers.backup_manager import BackupManager
from noust.managers.database.registry import DatabaseRegistry
from tests.test_backup_integrity import (  # noqa: F401
    FakeApp,
    FakeDatabase,
    FakeDatabaseManager,
    _use_store,
    apps_dir,
    manager,
)

DOMAIN = "shop.example.com"


class PostgresDouble(FakeDatabaseManager):
    """A PostgreSQL double that also knows the roles of its server."""

    HAS_GLOBALS = True
    taken: list[str] = []
    restored_roles: list[tuple[Path, str]] = []
    refuse = False

    def backup_globals(self, output_path: Path | None = None, compress: bool = True) -> Any:
        assert output_path is not None
        output_path.write_text("CREATE ROLE shopper;\n")
        PostgresDouble.taken.append(self.engine)

        class _Info:
            path = output_path
            size = output_path.stat().st_size

        return _Info()

    def restore_globals(self, source: Path) -> Any:
        if PostgresDouble.refuse:
            raise DatabaseBackupError("could not list the roles")
        PostgresDouble.restored_roles.append((source, source.read_text()))

        class _Result:
            created = ("shopper",)
            present = ("postgres",)
            left_out = ()
            errors = ()

        return _Result()


@pytest.fixture
def roles(manager: BackupManager, monkeypatch: pytest.MonkeyPatch) -> type[PostgresDouble]:
    """Make every PostgreSQL engine the backup manager asks for a double that has roles."""
    PostgresDouble.taken = []
    PostgresDouble.restored_roles = []
    PostgresDouble.refuse = False
    FakeDatabaseManager.restored = []
    FakeDatabaseManager.restore_options = []
    monkeypatch.setattr(
        DatabaseRegistry,
        "get",
        classmethod(lambda cls, engine, verbose=False: PostgresDouble(engine)),
    )
    return PostgresDouble


def take(manager: BackupManager, monkeypatch: pytest.MonkeyPatch, *databases: FakeDatabase) -> Any:
    """Take a backup of an application that has these databases."""
    _use_store(monkeypatch, FakeApp(1, DOMAIN), list(databases))
    manager.runner.script(["pg_dump"], stdout="-- dump of a database\n")
    return manager.create(DOMAIN, include_databases=True)


class TestTheRolesInTheArchive:
    def test_they_are_taken_once_per_server_however_many_databases_use_it(
        self, manager: BackupManager, roles: type[PostgresDouble], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backup = take(
            manager,
            monkeypatch,
            FakeDatabase("shop", "postgresql"),
            FakeDatabase("blog", "postgresql"),
        )

        assert roles.taken == ["postgresql"]
        with manager.unpacked(backup.id) as unpacked:
            assert unpacked.manifest is not None
            [entry] = unpacked.manifest["globals"]
            assert entry["engine"] == "postgresql"
            assert (unpacked.root / entry["archive_path"]).read_text() == "CREATE ROLE shopper;\n"
            assert len(unpacked.dumps) == 2

    def test_the_manifest_lists_them_apart_from_the_dumps(
        self, manager: BackupManager, roles: type[PostgresDouble], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backup = take(manager, monkeypatch, FakeDatabase("shop", "postgresql"))

        with manager.unpacked(backup.id) as unpacked:
            assert unpacked.manifest is not None
            names = [entry["name"] for entry in unpacked.manifest["databases"]]
            assert names == ["shop"]

    def test_an_engine_that_has_no_roles_is_not_asked_for_any(
        self, manager: BackupManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backup = take(manager, monkeypatch, FakeDatabase("shop", "mysql"))

        with manager.unpacked(backup.id) as unpacked:
            assert unpacked.manifest is not None
            assert unpacked.manifest.get("globals", []) == []

    def test_a_server_whose_roles_cannot_be_dumped_does_not_fail_the_backup(
        self, manager: BackupManager, roles: type[PostgresDouble], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(
            self: PostgresDouble, output_path: Path | None = None, compress: bool = True
        ) -> Any:
            raise DatabaseBackupError("pg_dumpall: connection refused")

        monkeypatch.setattr(PostgresDouble, "backup_globals", refuse)

        backup = take(manager, monkeypatch, FakeDatabase("shop", "postgresql"))

        with manager.unpacked(backup.id) as unpacked:
            assert unpacked.manifest is not None
            assert unpacked.manifest.get("globals", []) == []
            assert [name for _engine, name, _path in unpacked.dumps] == ["shop"]


class TestPuttingThemBack:
    def test_a_restore_creates_the_missing_roles_before_it_loads_the_dump(
        self, manager: BackupManager, roles: type[PostgresDouble], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backup = take(manager, monkeypatch, FakeDatabase("shop", "postgresql"))
        order: list[str] = []
        original = PostgresDouble.restore_globals

        def roles_first(self: PostgresDouble, source: Path) -> Any:
            order.append("roles")
            return original(self, source)

        def dump_second(
            self: PostgresDouble, database: str, backup_path: Path, **options: Any
        ) -> None:
            order.append("dump")

        monkeypatch.setattr(PostgresDouble, "restore_globals", roles_first)
        monkeypatch.setattr(PostgresDouble, "restore", dump_second)

        manager.restore(backup.id, restore_env=True, stop_service=False)

        assert order == ["roles", "dump"]
        assert roles.restored_roles[0][1] == "CREATE ROLE shopper;\n"

    def test_roles_that_cannot_be_put_back_are_a_warning_not_a_failed_restore(
        self, manager: BackupManager, roles: type[PostgresDouble], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backup = take(manager, monkeypatch, FakeDatabase("shop", "postgresql"))
        roles.refuse = True

        manager.restore(backup.id, restore_env=True, stop_service=False)

        assert [database for database, _path, _content in FakeDatabaseManager.restored] == ["shop"]

    def test_a_backup_from_before_the_roles_were_taken_restores_as_it_always_did(
        self, manager: BackupManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backup = take(manager, monkeypatch, FakeDatabase("shop", "postgresql"))

        manager.restore(backup.id, restore_env=True, stop_service=False)

        assert [database for database, _path, _content in FakeDatabaseManager.restored] == ["shop"]
