# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_backup_restore_safety.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.runner import FakeRunner
from noust.managers.backup_manager import BackupManager

DOMAIN = "shop.example.com"
APP_NAME = "shop-example-com"


class FakeApp:
    """Minimal stand-in for a store application record."""

    def __init__(self, app_type: str = "nodejs", layout: str | None = None) -> None:
        """
        Args:
            app_type: Type the store records for the application.
            layout: Layout the store records, when a mismatch check matters.
        """
        self.app_type = app_type
        self.layout = layout


class FakeStore:
    """Store double answering the one query these tests need: the app row."""

    def __init__(self, app: FakeApp | None) -> None:
        """
        Args:
            app: Application record to return, or None for an unknown domain.
        """
        self._app = app

    def get_app(self, domain: str) -> FakeApp | None:
        """
        Args:
            domain: Domain being looked up.

        Returns:
            The configured application record.
        """
        return self._app

    def list_databases(self, app_id: int | None = None) -> list[object]:
        """
        Args:
            app_id: Application the databases belong to.

        Returns:
            No databases: these tests do not exercise database restore.
        """
        return []

    def list_deployments(self, domain: str | None = None, limit: int = 50) -> list[object]:
        """
        Args:
            domain: Application whose history is asked for.
            limit: Most rows returned.

        Returns:
            No history: nothing here depends on a deployment's snapshot.
        """
        return []


def _use_store(monkeypatch: pytest.MonkeyPatch, app: FakeApp | None) -> None:
    """
    Point the backup manager's own store lookups at a double.

    This does not affect :mod:`noust.core.applock`, which reads the real,
    process-wide store to find where lock files live; tests that exercise the
    lock provide one of their own.

    Args:
        monkeypatch: Patching helper, scoped to the test.
        app: Application record the store should return.
    """
    store = FakeStore(app)
    monkeypatch.setattr("noust.managers.backup_manager.get_store", lambda: store)
    monkeypatch.setattr("noust.managers.rollback_manager.get_store", lambda: store)


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
    app = root / APP_NAME
    (app / "src").mkdir(parents=True)
    (app / "src" / "index.js").write_text("console.log('original')\n")
    (app / ".env").write_text("SECRET=1\n")
    return root


@pytest.fixture
def manager(runner: FakeRunner, tmp_path: Path, apps_dir: Path) -> Iterator[BackupManager]:
    """
    Provide a backup manager wired to temporary directories and a fake runner.

    Args:
        runner: The FakeRunner fixture, installed process-wide.
        tmp_path: Per-test temporary directory.
        apps_dir: Applications root holding the deployed application.

    Returns:
        A backup manager that cannot touch the real machine.
    """
    backup_manager = BackupManager(verbose=False, runner=runner)
    backup_manager.backup_dir = tmp_path / "backups"
    previous = backup_manager.config.get("apps_directory")
    backup_manager.config.set("apps_directory", str(apps_dir))
    yield backup_manager
    backup_manager.config.set("apps_directory", previous)
