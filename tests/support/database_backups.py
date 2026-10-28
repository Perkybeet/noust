# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_database_backups.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import set_runner
from noust.core.store import NoustStore
from noust.managers.backup_destination_files import DestinationFileManager
from noust.managers.database.backups import DatabaseBackups
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.database.service import DatabaseService
from tests.support.database_backup_support import (
    PG_LISTING,
    RcloneRunner,
    age,
    make_engine,
    make_service,
)


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    """Force a fresh store singleton per test; see test_backup_destinations.py."""
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def runner(tmp_path: Path) -> Iterator[RcloneRunner]:
    """
    A runner that answers rclone from a directory, with pg_restore content to list.

    Args:
        tmp_path: Per-test temporary directory.

    Yields:
        The runner, installed as the process-wide one.
    """
    fake = RcloneRunner(tmp_path / "remote")
    fake.script(("pg_restore", "--list"), stdout=PG_LISTING)
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def dumps_dir(tmp_path: Path) -> Path:
    """
    Args:
        tmp_path: Per-test temporary directory.

    Returns:
        The engine's dump directory.
    """
    path = tmp_path / "dumps"
    path.mkdir()
    return path


@pytest.fixture
def engine(dumps_dir: Path) -> type[BaseDatabaseManager]:
    """
    Args:
        dumps_dir: The engine's dump directory.

    Returns:
        A PostgreSQL fake with one database, ``shop``, writing custom archives.
    """
    return make_engine(dumps_dir)


@pytest.fixture
def service(
    tmp_path: Path, engine: type[BaseDatabaseManager], runner: RcloneRunner
) -> DatabaseService:
    """
    Args:
        tmp_path: Per-test temporary directory.
        engine: The fake engine.
        runner: The fake runner, installed.

    Returns:
        A service over the fake engine and an isolated store.
    """
    return make_service(tmp_path, [engine])


class FakeScheduler:
    """Stands in for the timer installer: records what it was asked, touches no unit."""

    def __init__(self) -> None:
        self.installed: dict[tuple[str, str], str] = {}
        self.removed: list[tuple[str, str]] = []

    def install_database_timer(
        self, engine: str, database: str, schedule: str, *, options: Any = None
    ) -> str:
        self.installed[(engine, database)] = schedule
        return f"noust-backup-db-{engine}-{database}"

    def remove_database_timer(self, engine: str, database: str) -> bool:
        self.removed.append((engine, database))
        self.installed.pop((engine, database), None)
        return True

    def database_timer_state(self, engine: str, database: str) -> dict[str, Any]:
        installed = (engine, database) in self.installed
        return {"installed": installed, "next_run": "Tue 2026-09-30 02:00:00 UTC", "last_run": None}


@pytest.fixture
def backups(service: DatabaseService, runner: RcloneRunner) -> DatabaseBackups:
    """
    Args:
        service: The service over the fake engine.
        runner: The fake runner, which the destination manager runs rclone through.

    Returns:
        The backups class, with a scheduler that only records.
    """
    return DatabaseBackups(
        service,
        scheduler=FakeScheduler(),  # type: ignore[arg-type]
        destinations=DestinationFileManager(runner=runner),
    )


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """
    Record what reaches the audit trail.

    Args:
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        One dict per event: ``event``, ``target``, ``outcome`` and ``details``.
    """
    events: list[dict[str, Any]] = []

    def record(event: str, **kwargs: Any) -> None:
        events.append({"event": event, **kwargs})

    monkeypatch.setattr("noust.core.audit.record", record)
    return events


def add_destination(runner: RcloneRunner, name: str = "nas", *, encrypted: bool = False) -> None:
    """
    Create a backup destination the fake rclone answers for.

    Args:
        runner: The fake runner.
        name: The destination's name.
        encrypted: Wrap it in a crypt remote.
    """
    DestinationFileManager(runner=runner).add(
        name,
        "sftp",
        {"host": "nas.example.com", "user": "noust", "pass": "s3cr3t"},
        encrypted=encrypted,
    )


def set_up_history(backups: DatabaseBackups, dumps_dir: Path) -> dict[str, Path]:
    """
    Lay down a history of dumps to prune: five scheduled, one manual, one safety copy.

    Args:
        backups: The backups class.
        dumps_dir: The dump directory.

    Returns:
        The files by role.
    """
    files: dict[str, Path] = {}
    for index, days in enumerate((50, 40, 30, 20, 10)):
        view = backups.dump("postgresql", "shop", origin="scheduled")
        age(view.info.path, days)
        files[f"scheduled-{index}"] = view.info.path
    manual = backups.dump("postgresql", "shop")
    age(manual.info.path, 400)
    files["manual"] = manual.info.path
    safety = backups.dump("postgresql", "shop", origin="manual")
    record = safety.record
    assert record is not None
    record.origin = "safety"
    backups.records.save_dump(record)
    age(safety.info.path, 500)
    files["safety"] = safety.info.path
    return files
