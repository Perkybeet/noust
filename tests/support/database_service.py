# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_database_service.py`` that other tests build on."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from noust.core.exceptions import DatabaseBackupError, DatabaseExistsError, DatabaseNotFoundError
from noust.managers.database.base import (
    AccessEntry,
    BackupInfo,
    BaseDatabaseManager,
    DatabaseInfo,
    UserInfo,
)


class FakeEngine(BaseDatabaseManager):
    """An in-memory engine: databases, users, grants and dumps, no processes."""

    ENGINE_NAME = "postgresql"
    DISPLAY_NAME = "PostgreSQL"
    DEFAULT_PORT = 5432
    SERVICE_NAME = "postgresql"
    CLIENT_BINARY = "psql"
    VALID_PRIVILEGES = frozenset({"ALL PRIVILEGES", "SELECT"})
    DEFAULT_PRIVILEGES = ("ALL PRIVILEGES",)
    CAPABILITIES = frozenset({"sql", "read_only", "users", "profiles", "dump"})
    INTERNAL_USERS = frozenset({"postgres"})

    def __init__(self, state: dict[str, Any], backup_dir: Path) -> None:
        """
        Args:
            state: The shared in-memory state, so every instance sees it.
            backup_dir: Where dumps are written.
        """
        super().__init__()
        self.state = state
        self.BACKUP_DIR = backup_dir

    def _calls(self, *entry: Any) -> None:
        self.state["calls"].append(entry)

    def is_installed(self) -> bool:
        return True

    def is_running(self) -> bool:
        return True

    def get_version(self) -> str | None:
        return "16.4"

    def server_port(self) -> int:
        return 5433

    def warnings(self) -> list[str]:
        return []

    def create_database(self, name, owner=None, encoding=None, **kwargs):
        self._calls("create_database", name, owner)
        if name in self.state["dbs"]:
            raise DatabaseExistsError(f"Database '{name}' already exists")
        self.state["dbs"][name] = {"owner": owner, "rows": "fresh"}
        return DatabaseInfo(name=name, engine=self.ENGINE_NAME, owner=owner)

    def drop_database(self, name, force=False):
        self._calls("drop_database", name)
        self.state["dbs"].pop(name, None)

    def database_exists(self, name):
        return name in self.state["dbs"]

    def list_databases(self):
        return [
            DatabaseInfo(name=name, engine=self.ENGINE_NAME, owner=entry.get("owner"), size="1 MB")
            for name, entry in self.state["dbs"].items()
        ]

    def get_database_info(self, name):
        if name not in self.state["dbs"]:
            raise DatabaseNotFoundError(f"Database '{name}' does not exist")
        entry = self.state["dbs"][name]
        return DatabaseInfo(name=name, engine=self.ENGINE_NAME, owner=entry.get("owner"))

    def create_user(self, username, password=None, host="localhost", **kwargs):
        self._calls("create_user", username)
        secret = password or self.generate_password()
        self.state["users"][username] = secret
        return UserInfo(username=username, engine=self.ENGINE_NAME, host=host), secret

    def drop_user(self, username, host="localhost"):
        self._calls("drop_user", username)
        self.state["users"].pop(username, None)

    def user_exists(self, username, host="localhost"):
        return username in self.state["users"]

    def list_users(self):
        return [UserInfo(username=name, engine=self.ENGINE_NAME) for name in self.state["users"]]

    def grant_privileges(self, username, database, privileges=None, host="localhost"):
        self._calls("grant", username, database)

    def revoke_privileges(self, username, database, privileges=None, host="localhost"):
        self._calls("revoke", username, database)

    def set_user_password(self, username, password, host="localhost"):
        self._calls("set_password", username, password)
        self.state["users"][username] = password

    def apply_profile(self, username, database, profile, host="localhost"):
        self._calls("profile", username, database, profile)
        self.state["profiles"][(username, database)] = profile

    def list_access(self, database):
        return [
            AccessEntry(username=user, profile=profile)
            for (user, db), profile in self.state["profiles"].items()
            if db == database
        ]

    def drop_read_only_account(self, database):
        self._calls("drop_ro", database)

    def backup(self, database, output_path=None, compress=True, **kwargs):
        self._calls("backup", database)
        self.state.setdefault("compress", []).append(compress)
        self.state["dumps"] += 1
        path = output_path or (
            self.BACKUP_DIR / f"postgresql-{database}-20260101_12000{self.state['dumps']}.dump"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(self.state["dbs"].get(database, {}).get("rows")))
        return BackupInfo(
            path=path,
            database=database,
            engine=self.ENGINE_NAME,
            size=path.stat().st_size,
            created=datetime(2026, 1, 1, 12, 0),
        )

    def _load_backup(self, database, backup_path, **kwargs):
        self._calls("load", database, Path(backup_path).name)
        content = Path(backup_path).read_text()
        if self.state["fail_load"] and content != self.state.get("good"):
            raise DatabaseBackupError("Failed to restore", details="pg_restore: truncated dump")
        self.state["dbs"].setdefault(database, {})["rows"] = content

    def execute_query(self, database, query, **kwargs):
        return True, ""
