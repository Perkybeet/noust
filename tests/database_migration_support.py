# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Docker host and the host's PostgreSQL, with an application on one of its databases.

Where :mod:`tests.database_move_support` has two servers in memory, this has the
real :class:`~noust.managers.database.managed.ManagedInstances` over
:class:`~tests.database_instance_support.DockerHost`, so a new instance is really
made (its Compose file, its unit, its container) and the engines that run in it
are the same in-memory servers.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.secrets import SecretStore
from noust.deployers.helpers.databases import _password_secret
from noust.managers.database.backups import DatabaseBackups
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.database.instance_migration import InstanceMigrations
from noust.managers.database.managed import ManagedInstances
from noust.managers.database.moves import DatabaseMover
from noust.managers.database.service import DatabaseService
from noust.managers.database.settings import Resources
from tests.database_instance_support import GIB, World, build_world, close_world
from tests.database_move_support import (
    APP_PASSWORD,
    HOST,
    FakePolicies,
    TwinEngine,
    install_app,
    new_state,
)


class Lab:
    """
    The host's PostgreSQL, a Docker host, the application and the migrations under test.

    Attributes:
        world: Docker and systemd, in memory.
        host: The host server's state.
        service: The database service over both.
        managed: The instances manager.
        migrations: What is under test.
        app: The application that uses ``shop_db`` on the host.
        gate: What the health gate answers, in order, and the environments it saw.
    """

    def __init__(self, world: World, host: dict[str, Any], app: Any, gate: dict[str, Any]) -> None:
        self.world = world
        self.host = host
        self.app = app
        self.gate = gate
        self.service = world.managed.service
        self.managed = world.managed
        self.policies = FakePolicies()
        self.migrations = InstanceMigrations(
            self.managed, DatabaseMover(self.service, backups=self.policies)
        )

    def env(self) -> dict[str, str]:
        from noust.deployers.helpers.app_env import read_app_env

        return read_app_env(self.app)

    def server(self, key: str) -> dict[str, Any]:
        """
        Args:
            key: An engine key.

        Returns:
            That server's in-memory state.
        """
        return TwinEngine.servers[key]["state"]


def build_lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Lab]:
    """
    Build the host's PostgreSQL, a Docker host and an application on the host's database.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Yields:
        The lab; the process-wide state is put back afterwards.
    """
    world = build_world(tmp_path, monkeypatch)
    host = new_state()
    host["dbs"]["shop_db"] = {"owner": "shop_user", "rows": "live"}
    host["users"]["shop_user"] = APP_PASSWORD
    host["profiles"][("shop_user", "shop_db")] = "owner"
    TwinEngine.servers = {HOST: {"state": host, "version": "16.4", "port": 5432}}
    dumps = tmp_path / "dumps"

    def resolve(name: str) -> BaseDatabaseManager | None:
        return TwinEngine(host, dumps) if name in ("postgresql", "pg") else None

    service = DatabaseService(
        store=world.store, secrets=SecretStore(root=tmp_path / "secrets"), resolve=resolve
    )
    world.managed = ManagedInstances(service, resources=lambda: Resources(8 * GIB, 4))
    service.secrets.write(_password_secret(HOST, "shop_user"), APP_PASSWORD)
    app, gate = install_app(world.store, tmp_path, monkeypatch)
    # The fake engine writes text, not a pg_dump archive: the check is not what is under test.
    monkeypatch.setattr(DatabaseBackups, "_check", lambda self, view: view)
    try:
        yield Lab(world, host, app, gate)
    finally:
        close_world()
