# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The server of the tests of database servers: fake system engines and a Docker a test controls."""

from __future__ import annotations

from pathlib import Path

from noust.core.db_instance_store import READY, DbInstanceStore, InstanceRecord
from noust.core.exceptions import DatabaseQueryError
from noust.core.store import NoustStore
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.database.instance_catalog import SPECS, resolve_pin
from noust.managers.database.instance_records import assign_managed
from noust.managers.database.instances import DatabaseInstance
from noust.managers.database.managed import ManagedInstances
from noust.managers.database.servers import DatabaseServers
from noust.managers.database.service import DatabaseService
from noust.managers.database.settings import Resources
from tests.support.database_choice import engine_classes

GIB = 1024**3


def stack_container(project: str, service: str, image: str) -> DatabaseInstance:
    """
    Args:
        project: The Compose project.
        service: Its service.
        image: The image.

    Returns:
        A database container an application's Compose project runs.
    """
    return DatabaseInstance(
        key=f"postgresql@{project}.{service}",
        engine="postgresql",
        flavour="postgres",
        container=f"{project}-{service}-1",
        container_id="abc123",
        image=image,
        project=project,
        service=service,
        state="running",
        app=f"{project}.example.com",
    )


def make_instance(
    store: NoustStore,
    name: str,
    flavour: str,
    version: str,
    *,
    state: str = READY,
    port: int = 5440,
) -> InstanceRecord:
    """
    Args:
        store: The store.
        name: The instance's name.
        flavour: What it runs.
        version: Its version.
        state: How far it got.
        port: Its port on 127.0.0.1.

    Returns:
        The row, written the way a creation writes it.
    """
    spec = SPECS[flavour]
    pin = resolve_pin(spec, version)
    row = DbInstanceStore(store).create(
        name=name,
        flavour=flavour,
        version=pin.version,
        image=spec.repository,
        digest=pin.digest,
        host_port=port,
        memory_limit_mb=1024,
        created_by="t",
    )
    DbInstanceStore(store).set_state(name, state)
    return DbInstanceStore(store).get(row.name) or row


class World:
    """A server with fake system engines, whose Docker holds what a test says."""

    def __init__(self, tmp_path: Path, store: NoustStore, **engines: str | None) -> None:
        self.store = store
        self.classes = engine_classes(
            tmp_path, postgres=engines.get("postgres", True) is not None, mysql=engines.get("mysql")
        )
        self.containers: list[DatabaseInstance] = []
        self.docker_down = False

    def resolve(self, name: str) -> BaseDatabaseManager | None:
        aliases = {"mariadb": "mysql", "pg": "postgresql", "valkey": "redis"}
        cls = self.classes.get(aliases.get(name.lower(), name.lower()))
        return cls() if cls else None

    def found(self) -> list[DatabaseInstance]:
        if self.docker_down:
            raise DatabaseQueryError("Docker does not answer", details="systemctl status docker")
        return assign_managed(list(self.containers), DbInstanceStore(self.store).all())

    def service(self) -> DatabaseService:
        return DatabaseService(
            store=self.store,
            resolve=self.resolve,
            engines=lambda: list(self.classes),
            instances=self.found,
        )

    def servers(self) -> DatabaseServers:
        service = self.service()
        return DatabaseServers(
            service, managed=ManagedInstances(service, resources=lambda: Resources(8 * GIB, 4))
        )
