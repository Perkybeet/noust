# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_database_ownership.py`` that other tests build on."""

from __future__ import annotations

from noust.core.exceptions import DatabaseError


class FakeManager:
    """A PostgreSQL manager stand-in whose grant can be made to fail."""

    ENGINE_NAME = "postgresql"
    DISPLAY_NAME = "PostgreSQL"
    MAX_DATABASE_NAME_LENGTH = 63
    MAX_USER_NAME_LENGTH = 63

    def __init__(self) -> None:
        self.databases: set[str] = set()
        self.users: dict[str, str] = {}
        self.calls: list[str] = []
        self.fail_grant = False

    def is_installed(self) -> bool:
        return True

    def validate_database_name(self, name: str) -> str:
        return name

    def validate_user_name(self, username: str) -> str:
        return username

    def database_exists(self, name: str) -> bool:
        return name in self.databases

    def create_database(self, name: str, **_kwargs: object) -> None:
        self.calls.append(f"create_database:{name}")
        self.databases.add(name)

    def user_exists(self, username: str, host: str = "localhost") -> bool:
        return username in self.users

    def create_user(self, username: str, password: str | None = None, **_kwargs: object) -> None:
        self.calls.append(f"create_user:{username}")
        self.users[username] = password or ""

    def grant_privileges(self, *, username: str, database: str) -> None:
        self.calls.append(f"grant:{username}:{database}")
        if self.fail_grant:
            raise DatabaseError("Failed to grant privileges", details="permission denied")

    def server_port(self) -> int:
        return 5432


class FakeRegistry:
    def __init__(self, manager: FakeManager) -> None:
        self._manager = manager

    def get(self, engine: str, verbose: bool = False) -> FakeManager | None:
        return self._manager if engine in ("postgresql", "postgres") else None
