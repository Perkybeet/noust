# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_web_database_backups.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import set_runner
from noust.core.store import NoustStore
from noust.managers.backup_scheduler import BackupScheduler
from noust.managers.database.base import BaseDatabaseManager
from tests.support.database_backup_support import PG_LISTING, RcloneRunner, make_engine
from tests.support.web_databases_api import wire

UNIT = "noust-backup-db-postgresql-shop"


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    """Force a fresh store singleton per test."""
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def runner(tmp_path: Path) -> Iterator[RcloneRunner]:
    """
    Args:
        tmp_path: Per-test temporary directory.

    Yields:
        A fake rclone that also lists pg_restore content, installed as the runner.
    """
    fake = RcloneRunner(tmp_path / "remote")
    fake.script(("pg_restore", "--list"), stdout=PG_LISTING)
    fake.script(
        ("systemctl", "show", f"{UNIT}.timer"),
        stdout="LoadState=loaded\nNextElapseUSecRealtime=Tue 2026-09-30 02:03:11 UTC\n",
    )
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def systemd_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The unit directory, inside the sandbox.
    """
    path = tmp_path / "systemd"
    path.mkdir()
    monkeypatch.setattr(BackupScheduler, "SYSTEMD_DIR", path)
    return path


@pytest.fixture
def engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: RcloneRunner, systemd_dir: Path
) -> type[BaseDatabaseManager]:
    """
    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.
        runner: The installed fake runner.
        systemd_dir: The sandboxed unit directory.

    Returns:
        A PostgreSQL fake with one database, wired in front of the service.
    """
    (tmp_path / "dumps").mkdir()
    cls = make_engine(tmp_path / "dumps")
    wire(monkeypatch, [cls])
    return cls


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """
    Args:
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        What reaches the audit trail, one dict per event.
    """
    events: list[dict[str, Any]] = []

    def record(event: str, **kwargs: Any) -> None:
        events.append({"event": event, **kwargs})

    monkeypatch.setattr("noust.core.audit.record", record)
    return events


def take_dump(engine: type[BaseDatabaseManager], **_: Any) -> str:
    """
    Write a dump the way the fake engine does and return its file name.

    Args:
        engine: The fake engine.

    Returns:
        The dump's file name.
    """
    return engine().backup("shop").path.name
