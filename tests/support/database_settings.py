# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_database_settings.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import CommandResult, FakeRunner, set_runner
from noust.managers.database import flavours
from noust.managers.database import settings as settings_module
from noust.managers.database.mysql import MySQLManager
from noust.managers.database.postgres import PostgresManager
from noust.managers.database.redis import RedisManager
from noust.managers.database.settings import (
    MySQLSettings,
    PostgresSettings,
    RedisSettings,
    Resources,
)
from noust.managers.server.host import HostPaths

GB = 1024**3

EIGHT_GB = Resources(memory_bytes=8 * GB, cpus=4)

PSQL = ("runuser", "-u", "postgres", "--", "psql")

PG_SETTINGS = "\n".join(
    [
        "listen_addresses|localhost|postmaster|/etc/postgresql/16/main/postgresql.conf",
        "port|5432|postmaster|/etc/postgresql/16/main/postgresql.conf",
        "max_connections|100|postmaster|/etc/postgresql/16/main/postgresql.conf",
        "shared_buffers|128MB|postmaster|/etc/postgresql/16/main/postgresql.conf",
        "effective_cache_size|4GB|user|",
        "work_mem|4MB|user|",
        "maintenance_work_mem|64MB|user|",
        "log_min_duration_statement|-1|superuser|",
        "timezone|Etc/UTC|user|/etc/postgresql/16/main/postgresql.conf",
    ]
)


class StubConfig:
    """A configuration with nothing in it, so the host's never leaks in."""

    def get(self, key: str, default: Any = None) -> Any:
        """
        Args:
            key: Key.
            default: Default.

        Returns:
            The default.
        """
        return default


class ScriptedRunner(FakeRunner):
    """A fake runner that can also answer by what a command reads on stdin, and in turn."""

    def __init__(self) -> None:
        super().__init__()
        #: (argv prefix, stdin or None, results to hand out in order).
        self.turns: list[tuple[tuple[str, ...], str | None, list[CommandResult]]] = []
        self.answer: Callable[[tuple[str, ...], str | None], CommandResult | None] | None = None

    def queue(self, prefix: tuple[str, ...], *results: CommandResult, stdin: str | None = None):
        """
        Hand out results in order to commands that start with a prefix.

        Args:
            prefix: The argv prefix.
            *results: The results, one per matching call; then the scripts.
            stdin: Only calls given exactly this on stdin.
        """
        self.turns.append((prefix, stdin, list(results)))

    def run(self, argv, *, input=None, **kwargs):  # type: ignore[override]
        result = super().run(argv, input=input, **kwargs)
        recorded = self.calls[-1]
        for prefix, stdin, results in self.turns:
            if recorded[: len(prefix)] == prefix and (stdin is None or stdin == input) and results:
                return replace(results.pop(0), argv=recorded)
        if self.answer is not None:
            answered = self.answer(recorded, input)
            if answered is not None:
                return replace(answered, argv=recorded)
        return result


def ok(stdout: str = "") -> CommandResult:
    return CommandResult(argv=(), exit_code=0, stdout=stdout)


def failed(stderr: str = "", stdout: str = "") -> CommandResult:
    return CommandResult(argv=(), exit_code=1, stdout=stdout, stderr=stderr)


@pytest.fixture
def scripted(monkeypatch: pytest.MonkeyPatch) -> Iterator[ScriptedRunner]:
    """
    Install a scripted runner, and never sleep while waiting for an engine.

    Yields:
        The runner.
    """
    fake = ScriptedRunner()
    set_runner(fake)
    monkeypatch.setattr(settings_module, "_sleep", lambda seconds: None)
    monkeypatch.setattr(settings_module, "WAIT_SECONDS", 3)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    A system root with a PostgreSQL 16 cluster, MySQL and MariaDB include
    directories, and Redis's file.

    Returns:
        The root.
    """
    conf = tmp_path / "etc/postgresql/16/main"
    (conf / "conf.d").mkdir(parents=True)
    (conf / "postgresql.conf").write_text(
        "port = 5432\nshared_buffers = 128MB\n#include_dir = 'conf.d'\ninclude_dir = 'conf.d'\n"
    )
    (tmp_path / "etc/mysql/mysql.conf.d").mkdir(parents=True)
    (tmp_path / "etc/mysql/mariadb.conf.d").mkdir(parents=True)
    (tmp_path / "etc/redis").mkdir(parents=True)
    (tmp_path / "etc/redis/redis.conf").write_text("bind 127.0.0.1 -::1\nsave 900 1\n")
    monkeypatch.setattr(flavours, "HOST", HostPaths(tmp_path))
    return tmp_path


def pg_answers(stdin_selects: dict[str, CommandResult] | None = None):
    """
    Answer psql by what it is asked.

    Args:
        stdin_selects: Overrides by statement.

    Returns:
        The answer function.
    """

    def answer(argv: tuple[str, ...], stdin: str | None) -> CommandResult | None:
        if argv[:2] == ("pg_lsclusters", "--no-header"):
            return ok(
                "16  main    5432 online postgres /var/lib/postgresql/16/main "
                "/var/log/postgresql/postgresql-16-main.log\n"
            )
        if argv[: len(PSQL)] == PSQL and stdin:
            if stdin_selects and stdin in stdin_selects:
                return stdin_selects[stdin]
            if "pg_settings" in stdin:
                return ok(PG_SETTINGS)
            return ok("1")
        return None

    return answer


@pytest.fixture
def postgres(scripted: ScriptedRunner, root: Path) -> PostgresSettings:
    scripted.answer = pg_answers()
    manager = PostgresManager()
    manager.config = StubConfig()
    return PostgresSettings(manager, EIGHT_GB)


def mysql_answers(argv: tuple[str, ...], stdin: str | None) -> CommandResult | None:
    if argv[0] == "mysql" and stdin and stdin.startswith("SHOW GLOBAL VARIABLES"):
        return ok(
            "bind_address\t127.0.0.1\nport\t3306\nmax_connections\t151\n"
            "innodb_buffer_pool_size\t134217728\ninnodb_log_file_size\t50331648\n"
            "slow_query_log\tOFF\nlong_query_time\t10.000000\n"
            "character_set_server\tutf8mb4\ntime_zone\tSYSTEM\n"
        )
    return None


def mysql_settings(scripted: ScriptedRunner, *, mariadb: bool) -> MySQLSettings:
    scripted.only_knows(*(["mysql", "mariadb", "mariadbd"] if mariadb else ["mysql", "mysqld"]))
    scripted.answer = mysql_answers
    manager = MySQLManager()
    manager.config = StubConfig()
    return MySQLSettings(manager, EIGHT_GB)


def redis_settings(scripted: ScriptedRunner) -> RedisSettings:
    scripted.only_knows("redis-cli", "redis-server")

    def answer(argv, stdin):
        if argv[:4] == ("redis-cli", "-n", "0", "CONFIG") and argv[4] == "GET":
            values = {
                "bind": "127.0.0.1 -::1",
                "port": "6379",
                "maxmemory": "0",
                "maxmemory-policy": "noeviction",
                "appendonly": "no",
                "save": "900 1",
            }
            return ok(f"{argv[5]}\n{values[argv[5]]}\n")
        if argv[-1] == "PING":
            return ok("PONG")
        return None

    scripted.answer = answer
    manager = RedisManager()
    manager.config = StubConfig()
    manager._password_loaded = True
    return RedisSettings(manager, EIGHT_GB)
