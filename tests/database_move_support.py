# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Two PostgreSQL servers in memory, one the host's and one an instance, for moving databases.

:class:`TwinEngine` is the fake engine of ``test_database_service`` with its own
state and port per server, so a database that is moved really leaves one dict and
arrives in the other. The application is a real store row with a real ``.env``;
only its health gate is scripted, which is what decides whether a move stands.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.db_instance_store import READY, DbInstanceStore
from noust.core.secrets import SecretStore
from noust.core.store import App, Database, NoustStore
from noust.deployers.helpers import app_env as app_env_module
from noust.deployers.helpers.databases import _password_secret
from noust.managers.database import linking
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.database.instance_records import key_of, stub_instance
from noust.managers.database.records import DatabaseLink, DatabaseRecords
from noust.managers.database.service import DatabaseService
from tests.test_database_service import FakeEngine

DOMAIN = "shop.example.com"
HOST = "postgresql"
INSTANCE = "pg17"
INSTANCE_KEY = f"postgresql@{INSTANCE}"
APP_PASSWORD = "app-secret"


class TwinEngine(FakeEngine):
    """A fake PostgreSQL whose state, port and version belong to the server it speaks for."""

    servers: dict[str, dict[str, Any]] = {}

    def _server(self) -> dict[str, Any]:
        # An instance Noust makes during a test has no entry yet: it starts empty.
        return self.servers.setdefault(
            self.ENGINE_NAME, {"state": new_state(), "version": "17.2", "port": None}
        )

    def bind(self, instance):  # type: ignore[no-untyped-def]
        super().bind(instance)
        self.state = self._server()["state"]
        # Each server writes its dumps in a directory of its own, as the real ones do.
        self.BACKUP_DIR = self.BACKUP_DIR / "instance"
        return self

    def get_version(self) -> str | None:
        return self._server()["version"]

    def server_port(self) -> int:
        port = self._server()["port"]
        return int(port) if port is not None else BaseDatabaseManager.server_port(self)

    def _restore_input(self, database, backup_path, **kwargs):  # type: ignore[no-untyped-def]
        @contextmanager
        def plain() -> Iterator[Path]:
            yield backup_path

        return plain()

    def installed_flavour(self) -> str | None:
        return "postgresql"


def new_state() -> dict[str, Any]:
    """
    Returns:
        An empty server: no database, the superuser, nothing recorded.
    """
    return {
        "dbs": {},
        "users": {"postgres": "x"},
        "profiles": {},
        "calls": [],
        "dumps": 0,
        "fail_load": False,
    }


class FakePolicies:
    """The backup policies, as far as a move touches them."""

    def __init__(self, policy: Any = None) -> None:
        self.policy = policy
        self.set_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.removed: list[tuple[str, str]] = []
        self.fail = False

    def get_policy(self, engine: str, database: str) -> Any:
        return SimpleNamespace(policy=self.policy if engine == HOST else None)

    def set_policy(self, engine: str, database: str, **kwargs: Any) -> None:
        if self.fail:
            from noust.core.exceptions import BackupError

            raise BackupError("the timer could not be made")
        self.set_calls.append((engine, database, kwargs))

    def remove_policy(self, engine: str, database: str) -> bool:
        self.removed.append((engine, database))
        return True


class Rig:
    """
    The servers, the application and the gate of one test.

    Attributes:
        service: The database service over both servers.
        store: The store.
        host: The host's server state.
        instance: The instance's server state.
        app: The application that uses ``shop_db``.
        gate: What the health gate answers, in order, and the environments it saw.
    """

    def __init__(
        self,
        service: DatabaseService,
        store: NoustStore,
        host: dict[str, Any],
        instance: dict[str, Any],
        app: App,
        gate: dict[str, Any],
    ) -> None:
        self.service = service
        self.store = store
        self.host = host
        self.instance = instance
        self.app = app
        self.gate = gate

    def env(self) -> dict[str, str]:
        """
        Returns:
            The application's variables as they are on disk.
        """
        return app_env_module.read_app_env(self.app)


def install_app(
    store: NoustStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[App, dict[str, Any]]:
    """
    Register the application that uses ``shop_db`` on the host, and script its gate.

    Args:
        store: The store.
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The application and the gate's answers (``results``, in order) with the
        environments it saw (``restarts``).
    """
    app_path = tmp_path / "apps" / "shop-example-com"
    app_path.mkdir(parents=True)
    (app_path / ".env").write_text(
        "APP_KEY=base64:abc\n"
        f"DATABASE_URL=postgresql://shop_user:{APP_PASSWORD}@localhost:5432/shop_db\n"
    )
    monkeypatch.setattr(
        app_env_module,
        "Config",
        lambda: SimpleNamespace(
            apps_directory=tmp_path / "apps", service_user="www-data", service_group="www-data"
        ),
    )
    app = store.create_app(App(domain=DOMAIN, app_path=str(app_path)))
    store.create_database(
        Database(app_id=app.id, name="shop_db", engine=HOST, port=5432, username="shop_user")
    )
    DatabaseRecords(store).save_link(
        DatabaseLink(app_id=app.id or 0, engine=HOST, db_name="shop_db", username="shop_user")
    )
    gate: dict[str, Any] = {"results": [], "restarts": []}

    def restart_behind_gate(app: App, store: NoustStore, log: Any) -> tuple[bool, str]:
        gate["restarts"].append(app_env_module.read_app_env(app))
        return gate["results"].pop(0) if gate["results"] else (True, "")

    monkeypatch.setattr(linking, "restart_behind_gate", restart_behind_gate)
    return app, gate


def build_rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Rig]:
    """
    Build the host server, an instance beside it, and an application on the host's database.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Yields:
        The rig.
    """
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    host = new_state()
    host["dbs"]["shop_db"] = {"owner": "shop_user", "rows": "live"}
    host["users"]["shop_user"] = APP_PASSWORD
    host["users"]["reporter"] = "theirs"
    host["profiles"][("shop_user", "shop_db")] = "owner"
    host["profiles"][("reporter", "shop_db")] = "read_only"
    instance = new_state()
    TwinEngine.servers = {
        HOST: {"state": host, "version": "16.4", "port": 5432},
        INSTANCE_KEY: {"state": instance, "version": "17.2", "port": 5434},
    }
    record = DbInstanceStore(store).create(
        name=INSTANCE,
        flavour="postgresql",
        version="17",
        image="postgres",
        digest="sha256:" + "a" * 64,
        host_port=5434,
        memory_limit_mb=512,
        created_by=None,
    )
    DbInstanceStore(store).set_state(INSTANCE, READY)
    backups = tmp_path / "dumps"

    def resolve(name: str) -> BaseDatabaseManager | None:
        return TwinEngine(host, backups) if name in ("postgresql", "pg") else None

    service = DatabaseService(
        store=store,
        secrets=SecretStore(root=tmp_path / "secrets"),
        resolve=resolve,
        engines=lambda: ["postgresql"],
        instances=lambda: [stub_instance(record, state="running")],
    )
    assert key_of(record) == INSTANCE_KEY
    service.secrets.write(_password_secret(HOST, "shop_user"), APP_PASSWORD)

    app, gate = install_app(store, tmp_path, monkeypatch)
    try:
        yield Rig(service, store, host, instance, app, gate)
    finally:
        store.close()
        NoustStore.reset_instance()
