# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_backup_integrity.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest

from noust.core.runner import FakeRunner, get_runner
from noust.managers.backup_manager import BackupManager


class FakeApp:
    """Minimal stand-in for a store application record."""

    def __init__(self, app_id: int, domain: str) -> None:
        """
        Args:
            app_id: Primary key the store would have assigned.
            domain: Domain the application answers on.
        """
        self.id = app_id
        self.domain = domain


class FakeDatabase:
    """Minimal stand-in for a store database record."""

    def __init__(self, name: str, engine: str) -> None:
        """
        Args:
            name: Database name.
            engine: Engine that owns it.
        """
        self.name = name
        self.engine = engine


class FakeStore:
    """Store double that answers the two queries the backup manager makes."""

    def __init__(self, app: FakeApp | None, databases: list[FakeDatabase]) -> None:
        """
        Args:
            app: Application record to return, or None when unknown.
            databases: Databases attached to that application.
        """
        self._app = app
        self._databases = databases

    def get_app(self, domain: str) -> FakeApp | None:
        """
        Args:
            domain: Domain being looked up.

        Returns:
            The configured application record.
        """
        return self._app

    def list_databases(self, app_id: int | None = None) -> list[FakeDatabase]:
        """
        Args:
            app_id: Application the databases belong to.

        Returns:
            The configured database records.
        """
        return self._databases

    def list_deployments(self, domain: str | None = None, limit: int = 50) -> list[object]:
        """
        Args:
            domain: Application whose history is asked for.
            limit: Most rows returned.

        Returns:
            No history: nothing here depends on a deployment's snapshot.
        """
        return []


class FakeDatabaseManager:
    """
    Database engine double that really writes and really reads a dump file.

    It goes through the process-wide runner, so the argv a real engine would
    build is still asserted on, but the dump content is a plain marker string
    the test can follow from creation to restore.
    """

    restored: ClassVar[list[tuple[str, Path, str]]] = []
    #: The options each restore was told, in order: a dump Noust itself kept is loaded as the
    #: superuser (checklist H-19), and the double has to see that to be able to say so.
    restore_options: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, engine: str = "postgresql") -> None:
        """
        Args:
            engine: Engine name reported in metadata.
        """
        self.engine = engine
        self.BACKUP_SUFFIX = ".sql"

    def is_installed(self) -> bool:
        """
        Returns:
            Always True; installation is not what these tests exercise.
        """
        return True

    def backup(self, database: str, output_path: Path | None = None, compress: bool = True) -> Any:
        """
        Write a dump to ``output_path`` through the runner.

        Args:
            database: Database to dump.
            output_path: Destination chosen by the backup manager.
            compress: Whether the dump is gzipped.

        Returns:
            An object with the same attributes as ``BackupInfo``.
        """
        assert output_path is not None
        get_runner().capture_to_file(
            ["pg_dump", "--no-password", database],
            output_path,
            compress=compress,
            timeout=3600,
        )

        class _Info:
            path = output_path
            size = output_path.stat().st_size if output_path.exists() else 0
            created = datetime.now()

        return _Info()

    def restore(self, database: str, backup_path: Path, **options: Any) -> None:
        """
        Record the restore and read the dump back.

        Args:
            database: Target database.
            backup_path: Dump handed over by the backup manager.
            **options: What the caller told the engine about the dump (``as_superuser``).
        """
        content = Path(backup_path).read_text()
        FakeDatabaseManager.restored.append((database, Path(backup_path), content))
        FakeDatabaseManager.restore_options.append(options)
        get_runner().run(["psql", "--no-password", "-d", database], timeout=3600)


@pytest.fixture
def apps_dir(tmp_path: Path) -> Path:
    """
    Provide an applications root with one deployed application in it.

    Args:
        tmp_path: Per-test temporary directory.

    Returns:
        The applications root.
    """
    root = tmp_path / "apps"
    app = root / "shop-example-com"
    (app / "src").mkdir(parents=True)
    (app / "src" / "index.js").write_text("console.log('hello')\n")
    (app / ".env").write_text("SECRET=1\n")
    (app / "node_modules").mkdir()
    (app / "node_modules" / "junk.js").write_text("x" * 100)
    return root


@pytest.fixture
def manager(
    runner: FakeRunner, tmp_path: Path, apps_dir: Path, monkeypatch
) -> Iterator[BackupManager]:
    """
    Provide a backup manager wired to temporary directories and a fake runner.

    Args:
        runner: The FakeRunner fixture, installed process-wide.
        tmp_path: Per-test temporary directory.
        apps_dir: Applications root holding the deployed application.
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        A backup manager that cannot touch the real machine.
    """
    from noust.managers.database.registry import DatabaseRegistry

    FakeDatabaseManager.restored = []
    FakeDatabaseManager.restore_options = []
    backup_manager = BackupManager(verbose=False, runner=runner)
    backup_manager.backup_dir = tmp_path / "backups"

    previous = backup_manager.config.get("apps_directory")
    backup_manager.config.set("apps_directory", str(apps_dir))

    monkeypatch.setattr(
        DatabaseRegistry,
        "get",
        classmethod(lambda cls, engine, verbose=False: FakeDatabaseManager(engine)),
    )

    yield backup_manager

    backup_manager.config.set("apps_directory", previous)


def _use_store(monkeypatch, app: FakeApp | None, databases: list[FakeDatabase]) -> None:
    """
    Point the backup manager at a store double.

    Args:
        monkeypatch: Patching helper, scoped to the test.
        app: Application record the store should return.
        databases: Databases attached to it.
    """
    store = FakeStore(app, databases)
    monkeypatch.setattr("noust.managers.backup_manager.get_store", lambda: store)
    monkeypatch.setattr("noust.managers.rollback_manager.get_store", lambda: store)
