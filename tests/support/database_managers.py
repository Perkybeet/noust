# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_database_managers.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.managers.database.mysql import MySQLManager
from noust.managers.database.postgres import PostgresManager

PSQL_PREFIX = ("runuser", "-u", "postgres", "--", "psql")


class StubConfig:
    """A configuration that answers with whatever the test put in it."""

    def __init__(self, data: dict[str, Any]):
        """
        Args:
            data: Values keyed by top level configuration key.
        """
        self._data = data

    def get(self, key: str, default: Any = None) -> Any:
        """
        Read a configuration key.

        Args:
            key: Configuration key.
            default: Value returned when the key is absent.

        Returns:
            The configured value or the default.
        """
        return self._data.get(key, default)


@pytest.fixture
def postgres(runner: FakeRunner, tmp_path: Path) -> Iterator[PostgresManager]:
    """
    Provide a PostgreSQL manager whose backups land in a temporary directory.

    The store is reopened for the test, because the read-only console keeps
    its role passwords beside it and the store is a process-wide singleton:
    without this, one test would read the password another test stored.

    Args:
        runner: The fake runner installed process-wide.
        tmp_path: Per-test temporary directory.

    Yields:
        A configured manager.
    """
    NoustStore.reset_instance()
    manager = PostgresManager()
    manager.BACKUP_DIR = tmp_path / "backups"
    # The host's own /etc/wasm/config.yaml must not leak into the argv tables.
    manager.config = StubConfig({})
    try:
        yield manager
    finally:
        NoustStore.reset_instance()


@pytest.fixture
def mysql(runner: FakeRunner, tmp_path: Path) -> MySQLManager:
    """
    Provide a MySQL manager whose backups land in a temporary directory.

    Args:
        runner: The fake runner installed process-wide.
        tmp_path: Per-test temporary directory.

    Returns:
        A configured manager.
    """
    runner.only_knows("mysql", "mysqldump")
    manager = MySQLManager()
    manager.BACKUP_DIR = tmp_path / "backups"
    # The host's own /etc/wasm/config.yaml must not leak into the argv tables.
    manager.config = StubConfig({})
    return manager


def existing_database(manager, exists: bool = True) -> None:
    """
    Pin what the manager believes about a database, without scripting SQL.

    Args:
        manager: The manager to patch.
        exists: What ``database_exists`` should answer.
    """
    manager.database_exists = lambda name, _exists=exists: _exists
