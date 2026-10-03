# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An engine's settings (3.3, item 71): checked strictly, applied without leaving it broken.

What is pinned here:

- a value that could break out of its line (a quote, a newline, a ``#``) is
  refused whatever the setting, and so is a size without its unit, a value
  out of range or a choice outside the list;
- the recommendations follow the server's memory;
- the settings go to Noust's own file, the distribution's only gaining the
  include line once;
- the sequence is write, check with the engine's own tool, restart or reload
  or apply at runtime, wait; and on any failure the previous file is back,
  the engine restarted on it, and the error carries its journal verbatim;
- a rehearsal writes nothing;
- opening an engine beyond loopback needs a confirmation and is refused
  under the ENS profile; an engine in a container is never configured.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from noust.core.exceptions import DatabaseEngineError, ValidationError
from noust.core.fs import DryRunFileSystem, set_fs
from noust.core.runner import CommandResult, DryRunRunner, FakeRunner, set_runner
from noust.managers.database import flavours
from noust.managers.database import mongodb as mongodb_module
from noust.managers.database import settings as settings_module
from noust.managers.database.mongodb import MongoDBManager
from noust.managers.database.mysql import MySQLManager
from noust.managers.database.postgres import PostgresManager
from noust.managers.database.redis import RedisManager
from noust.managers.database.settings import (
    POSTGRES_SPECS,
    REDIS_SPECS,
    MongoSettings,
    MySQLSettings,
    PostgresSettings,
    RedisSettings,
    Resources,
    format_size,
    parse_value,
)
from noust.managers.server.host import HostPaths

GB = 1024**3
MB = 1024**2
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


# ----------------------------------------------------------------- values


SPEC = {spec.key: spec for spec in POSTGRES_SPECS}


@pytest.mark.parametrize(
    "value",
    [
        "256MB'\nlisten_addresses = '*",
        "256MB\nlisten_addresses = '*'",
        "256MB # comment",
        "256MB; DROP TABLE x",
        '"256MB"',
        "256MB\\",
        "x=1",
        "256MB\r\nport = 1",
    ],
)
def test_a_value_that_could_break_out_of_its_line_is_refused(value: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        parse_value(SPEC["shared_buffers"], value, "postgresql", EIGHT_GB)

    assert excinfo.value.field == "shared_buffers"


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("shared_buffers", "256MB", 256 * MB),
        ("shared_buffers", "1gb", GB),
        ("shared_buffers", "2G", 2 * GB),
        ("work_mem", "512kB", 512 * 1024),
        ("log_min_duration_statement", "1s", 1000),
        ("log_min_duration_statement", "-1", -1),
        ("listen_addresses", "localhost, 10.0.0.5", ("localhost", "10.0.0.5")),
        ("listen_addresses", "*", ("*",)),
        ("port", "5433", 5433),
        ("timezone", "Europe/Madrid", "Europe/Madrid"),
    ],
)
def test_values_are_brought_to_their_canonical_form(key: str, value: str, expected: Any) -> None:
    assert parse_value(SPEC[key], value, "postgresql", EIGHT_GB) == expected


@pytest.mark.parametrize(
    ("key", "value", "why"),
    [
        ("shared_buffers", "1024", "with its unit"),
        ("shared_buffers", "64GB", "at most"),
        ("shared_buffers", "1MB", "at least"),
        ("port", "80", "at least"),
        ("port", "70000", "at most"),
        ("max_connections", "lots", "whole number"),
        ("listen_addresses", "db.example.com", "not an address"),
        ("listen_addresses", "127.0.0.1 ; 0.0.0.0", "characters"),
        ("timezone", "../../etc/passwd", "time zone"),
    ],
)
def test_values_out_of_range_or_shape_are_refused(key: str, value: str, why: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        parse_value(SPEC[key], value, "postgresql", EIGHT_GB)

    assert why in excinfo.value.message


def test_choices_and_snapshots_of_redis() -> None:
    specs = {spec.key: spec for spec in REDIS_SPECS}
    assert parse_value(specs["save"], "3600 1 300 100", "redis", EIGHT_GB) == (3600, 1, 300, 100)
    assert parse_value(specs["save"], "off", "redis", EIGHT_GB) == ()
    assert parse_value(specs["maxmemory"], "0", "redis", EIGHT_GB) == 0
    assert parse_value(specs["bind"], "127.0.0.1 -::1", "redis", EIGHT_GB) == ("127.0.0.1", "-::1")
    with pytest.raises(ValidationError):
        parse_value(specs["save"], "3600", "redis", EIGHT_GB)
    with pytest.raises(ValidationError):
        parse_value(specs["maxmemory-policy"], "allkeys-everything", "redis", EIGHT_GB)


def test_sizes_are_shown_in_the_largest_exact_unit() -> None:
    assert format_size(256 * MB) == "256MB"
    assert format_size(GB) == "1GB"
    assert format_size(1536 * MB) == "1536MB"
    assert format_size(512 * 1024) == "512kB"


# --------------------------------------------------------------- PostgreSQL


def test_postgresql_recommends_from_the_server_s_memory(postgres: PostgresSettings) -> None:
    report = {item.spec.key: item for item in postgres.report().settings}

    assert report["shared_buffers"].recommended == "2GB"
    assert report["effective_cache_size"].recommended == "6GB"
    assert report["work_mem"].recommended == "20MB"
    assert report["maintenance_work_mem"].recommended == "512MB"
    assert report["timezone"].recommended is None
    assert report["shared_buffers"].current == "128MB"
    assert report["shared_buffers"].restart is True
    assert report["work_mem"].restart is False
    assert report["timezone"].current == "Etc/UTC"


def test_a_reloadable_setting_is_written_checked_and_reloaded(
    postgres: PostgresSettings, scripted: ScriptedRunner, root: Path
) -> None:
    outcome = postgres.apply({"work_mem": "64MB"})

    written = (root / "etc/postgresql/16/main/conf.d/90-noust.conf").read_text()
    assert written.startswith("# Generated by Noust")
    assert "work_mem = '64MB'" in written
    assert outcome.changed == ["work_mem"] and outcome.action == "reload"
    check = next(call for call in scripted.calls if "-C" in call)
    assert check[:4] == ("runuser", "-u", "postgres", "--")
    assert check[4:] == (
        "/usr/lib/postgresql/16/bin/postgres",
        "-D",
        "/var/lib/postgresql/16/main",
        "-c",
        "config_file=/etc/postgresql/16/main/postgresql.conf",
        "-C",
        "work_mem",
    )
    assert ("systemctl", "reload", "postgresql@16-main") in scripted.calls
    assert not [call for call in scripted.calls if call[:2] == ("systemctl", "restart")]


def test_a_postmaster_setting_restarts_the_cluster(
    postgres: PostgresSettings, scripted: ScriptedRunner
) -> None:
    outcome = postgres.apply({"shared_buffers": "2GB"})

    assert outcome.action == "restart"
    assert ("systemctl", "restart", "postgresql@16-main") in scripted.calls


def test_a_setting_back_to_default_leaves_noust_s_file(
    postgres: PostgresSettings, root: Path
) -> None:
    postgres.apply({"work_mem": "64MB", "maintenance_work_mem": "256MB"})
    postgres.apply({"work_mem": "default"})

    written = (root / "etc/postgresql/16/main/conf.d/90-noust.conf").read_text()
    assert "work_mem = '64MB'" not in written
    assert "maintenance_work_mem = '256MB'" in written


def test_nothing_changed_touches_nothing(
    postgres: PostgresSettings, scripted: ScriptedRunner
) -> None:
    postgres.apply({"work_mem": "64MB"})
    before = len(scripted.calls)

    outcome = postgres.apply({"work_mem": "64MB"})

    assert outcome.action == "none" and outcome.changed == []
    assert not [c for c in scripted.calls[before:] if c[0] == "systemctl"]


def test_include_dir_is_added_once_when_postgresql_conf_has_none(
    postgres: PostgresSettings, root: Path
) -> None:
    main = root / "etc/postgresql/16/main/postgresql.conf"
    main.write_text("port = 5432\n#include_dir = 'conf.d'\n")

    postgres.apply({"work_mem": "64MB"})
    postgres.apply({"work_mem": "32MB"})

    text = main.read_text()
    assert text.count("\ninclude_dir = 'conf.d'") == 1
    assert text.startswith("port = 5432\n")


def test_a_cluster_that_does_not_come_back_gets_the_previous_file_and_the_journal(
    postgres: PostgresSettings, scripted: ScriptedRunner, root: Path
) -> None:
    ours = root / "etc/postgresql/16/main/conf.d/90-noust.conf"
    postgres.apply({"shared_buffers": "1GB"})
    previous = ours.read_text()
    scripted.queue(
        ("systemctl", "restart", "postgresql@16-main"),
        failed("Job for postgresql@16-main.service failed."),
        ok(),
    )
    scripted.script(
        ["journalctl", "-u", "postgresql@16-main"],
        stdout="FATAL:  could not map anonymous shared memory: Cannot allocate memory",
    )

    with pytest.raises(DatabaseEngineError) as excinfo:
        postgres.apply({"shared_buffers": "7GB"})

    error = excinfo.value
    assert "previous ones are back" in error.message
    assert "could not map anonymous shared memory" in (error.output or "")
    assert ours.read_text() == previous
    restarts = [
        c for c in scripted.calls if c[:3] == ("systemctl", "restart", "postgresql@16-main")
    ]
    assert len(restarts) == 3  # the first apply, the failed one, the one on the previous file
    assert (
        "journalctl",
        "-u",
        "postgresql@16-main",
        "-n",
        "50",
        "--no-pager",
    ) in scripted.calls


def test_an_engine_that_never_answers_is_said_to_be_down(
    scripted: ScriptedRunner, root: Path
) -> None:
    scripted.answer = pg_answers({"SELECT 1;": failed("could not connect")})
    manager = PostgresManager()
    manager.config = StubConfig()

    with pytest.raises(DatabaseEngineError) as excinfo:
        PostgresSettings(manager, EIGHT_GB).apply({"shared_buffers": "1GB"})

    assert "did not come back on the previous settings either" in excinfo.value.details
    assert not (root / "etc/postgresql/16/main/conf.d/90-noust.conf").exists()


def test_a_configuration_the_checker_refuses_never_reaches_the_engine(
    postgres: PostgresSettings, scripted: ScriptedRunner, root: Path
) -> None:
    scripted.script(
        ["runuser", "-u", "postgres", "--", "/usr/lib/postgresql/16/bin/postgres"],
        exit_code=1,
        stderr='FATAL:  invalid value for parameter "work_mem": "64MB"',
    )

    with pytest.raises(DatabaseEngineError) as excinfo:
        postgres.apply({"work_mem": "64MB"})

    assert 'invalid value for parameter "work_mem"' in (excinfo.value.output or "")
    assert not (root / "etc/postgresql/16/main/conf.d/90-noust.conf").exists()
    assert not [
        c for c in scripted.calls if c[:2] in (("systemctl", "restart"), ("systemctl", "reload"))
    ]


def test_a_value_alter_system_still_overrides_is_warned_about(
    scripted: ScriptedRunner, root: Path
) -> None:
    overridden = PG_SETTINGS.replace(
        "work_mem|4MB|user|", "work_mem|8MB|user|/var/lib/postgresql/16/main/postgresql.auto.conf"
    )
    base = pg_answers()

    def answer(argv, stdin):
        if stdin and "pg_settings" in stdin:
            return ok(overridden)
        return base(argv, stdin)

    scripted.answer = answer
    manager = PostgresManager()
    manager.config = StubConfig()

    outcome = PostgresSettings(manager, EIGHT_GB).apply({"work_mem": "64MB"})

    assert any("postgresql.auto.conf" in warning for warning in outcome.warnings)


def test_a_rehearsal_writes_nothing_and_restarts_nothing(root: Path, monkeypatch) -> None:
    inner = ScriptedRunner()
    inner.answer = pg_answers()
    rehearsal = DryRunRunner(inner)
    set_runner(rehearsal)
    set_fs(DryRunFileSystem())
    monkeypatch.setattr(settings_module, "_sleep", lambda seconds: None)
    try:
        manager = PostgresManager()
        manager.config = StubConfig()
        outcome = PostgresSettings(manager, EIGHT_GB).apply({"shared_buffers": "1GB"})
    finally:
        set_runner(None)
        set_fs(None)

    assert outcome.changed == ["shared_buffers"]
    assert not (root / "etc/postgresql/16/main/conf.d/90-noust.conf").exists()
    assert [call[0] for call in inner.calls] == ["pg_lsclusters"]
    assert ("systemctl", "restart", "postgresql@16-main") in rehearsal.skipped


# ---------------------------------------------------------------- exposure


def test_opening_an_engine_needs_a_confirmation(postgres: PostgresSettings, root: Path) -> None:
    with pytest.raises(ValidationError) as excinfo:
        postgres.apply({"listen_addresses": "10.0.0.5"})

    assert excinfo.value.field == "confirm_exposure"
    assert "beyond this server" in excinfo.value.message
    assert not (root / "etc/postgresql/16/main/conf.d/90-noust.conf").exists()

    outcome = postgres.apply({"listen_addresses": "10.0.0.5"}, confirm_exposure=True)
    assert outcome.exposed and any("firewall" in warning for warning in outcome.warnings)


def test_the_ens_profile_refuses_opening_an_engine(postgres: PostgresSettings) -> None:
    with pytest.raises(ValidationError) as excinfo:
        postgres.apply(
            {"listen_addresses": "10.0.0.5"}, confirm_exposure=True, remote_listen_allowed=False
        )

    assert "ENS profile" in excinfo.value.message


def test_loopback_needs_no_confirmation(postgres: PostgresSettings) -> None:
    assert postgres.apply({"listen_addresses": "localhost,::1"}).exposed is False


def test_an_unknown_setting_is_refused_naming_the_ones_there_are(
    postgres: PostgresSettings,
) -> None:
    with pytest.raises(ValidationError) as excinfo:
        postgres.apply({"fsync": "off"})

    assert excinfo.value.field == "fsync"
    assert "shared_buffers" in excinfo.value.details


# ------------------------------------------------------------ MySQL/MariaDB


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


def test_mysql_dynamic_settings_are_applied_with_set_global(
    scripted: ScriptedRunner, root: Path
) -> None:
    settings = mysql_settings(scripted, mariadb=False)

    outcome = settings.apply(
        {"max_connections": "300", "slow_query_log": "on", "innodb_buffer_pool_size": "2GB"}
    )

    assert outcome.action == "runtime"
    written = (root / "etc/mysql/mysql.conf.d/99-noust.cnf").read_text()
    assert (
        "[mysqld]\nmax_connections = 300\ninnodb_buffer_pool_size = 2G\nslow_query_log = ON"
        in written
    )
    assert ("mysqld", "--validate-config") in scripted.calls
    statements = [stdin for stdin in scripted.inputs if stdin and stdin.startswith("SET GLOBAL")]
    assert sorted(statements) == [
        "SET GLOBAL innodb_buffer_pool_size = 2147483648;",
        "SET GLOBAL max_connections = 300;",
        "SET GLOBAL slow_query_log = ON;",
    ]
    assert not [c for c in scripted.calls if c[:2] == ("systemctl", "restart")]


def test_mariadb_writes_its_own_directory_checks_with_help_and_binds_one_address(
    scripted: ScriptedRunner, root: Path
) -> None:
    settings = mysql_settings(scripted, mariadb=True)

    with pytest.raises(ValidationError, match="takes one address"):
        settings.apply({"bind-address": "127.0.0.1,10.0.0.5"}, confirm_exposure=True)

    outcome = settings.apply({"port": "3307"})

    assert outcome.action == "restart"
    assert (root / "etc/mysql/mariadb.conf.d/99-noust.cnf").read_text().endswith("port = 3307\n")
    assert ("mariadbd", "--help", "--verbose") in scripted.calls
    assert ("systemctl", "restart", "mariadb") in scripted.calls


def test_a_refused_set_global_puts_back_the_file_and_the_running_values(
    scripted: ScriptedRunner, root: Path
) -> None:
    settings = mysql_settings(scripted, mariadb=False)
    scripted.queue(
        ("mysql",),
        failed("ERROR 1231 (42000): Variable 'max_connections'"),
        stdin="SET GLOBAL max_connections = 300;",
    )

    with pytest.raises(DatabaseEngineError) as excinfo:
        settings.apply({"long_query_time": "2", "max_connections": "300"})

    assert not (root / "etc/mysql/mysql.conf.d/99-noust.cnf").exists()
    assert "ERROR 1231" in (excinfo.value.details + (excinfo.value.output or ""))
    assert "SET GLOBAL long_query_time = 10;" in scripted.inputs


def test_mysql_time_zone_is_an_offset() -> None:
    spec = next(spec for spec in settings_module.MYSQL_SPECS if spec.key == "default-time-zone")

    assert parse_value(spec, "+01:00", "mysql", EIGHT_GB) == "+01:00"
    with pytest.raises(ValidationError, match="zone tables"):
        parse_value(spec, "Europe/Madrid", "mysql", EIGHT_GB)


# -------------------------------------------------------------- Redis/Valkey


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


def test_redis_settings_go_to_an_include_kept_last(scripted: ScriptedRunner, root: Path) -> None:
    settings = redis_settings(scripted)

    outcome = settings.apply({"maxmemory": "1GB", "save": "3600 1 300 100", "appendonly": "yes"})

    assert outcome.action == "runtime"
    ours = (root / "etc/redis/noust.conf").read_text().splitlines()
    assert ours[1:] == ["maxmemory 1gb", "appendonly yes", 'save ""', "save 3600 1", "save 300 100"]
    main = (root / "etc/redis/redis.conf").read_text()
    assert main.rstrip().endswith("include /etc/redis/noust.conf")
    assert ("redis-cli", "-n", "0", "CONFIG", "SET", "maxmemory", str(GB)) in scripted.calls
    assert ("redis-cli", "-n", "0", "CONFIG", "SET", "save", "3600 1 300 100") in scripted.calls

    # A CONFIG REWRITE appends after the include; the next change moves it back last.
    (root / "etc/redis/redis.conf").write_text(main + "maxmemory 512mb\n")
    settings.apply({"maxmemory": "2GB"})
    lines = [line for line in (root / "etc/redis/redis.conf").read_text().splitlines() if line]
    assert lines[-1] == "include /etc/redis/noust.conf"
    assert lines.count("include /etc/redis/noust.conf") == 1


def test_redis_port_is_not_changed_by_noust(scripted: ScriptedRunner, root: Path) -> None:
    with pytest.raises(ValidationError) as excinfo:
        redis_settings(scripted).apply({"port": "6380"})

    assert "cut Noust off" in excinfo.value.details


def test_the_redis_report_reads_noust_s_file_back(scripted: ScriptedRunner, root: Path) -> None:
    settings = redis_settings(scripted)
    settings.apply({"save": "off", "maxmemory-policy": "allkeys-lru"})

    report = {item.spec.key: item for item in settings.report().settings}

    assert report["save"].configured == "off"
    assert report["save"].current == "900 1"
    assert report["maxmemory-policy"].configured == "allkeys-lru"
    assert report["maxmemory"].recommended == "1GB"


# ------------------------------------------------------------------ MongoDB


def test_mongodb_edits_its_yaml_keeping_everything_else(
    scripted: ScriptedRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conf = tmp_path / "mongod.conf"
    conf.write_text(
        "storage:\n  dbPath: /var/lib/mongodb\nnet:\n  port: 27017\n  bindIp: myhost\n"
        "security:\n  authorization: enabled\n"
    )
    monkeypatch.setattr(mongodb_module, "MONGOD_CONF", conf)
    scripted.only_knows("mongosh", "mongod")
    scripted.answer = lambda argv, stdin: ok("1") if stdin and "ping" in stdin else None
    settings = MongoSettings(MongoDBManager(), EIGHT_GB)

    outcome = settings.apply({"storage.wiredTiger.engineConfig.cacheSizeGB": "1.5"})

    document = yaml.safe_load(conf.read_text())
    assert document["storage"] == {
        "dbPath": "/var/lib/mongodb",
        "wiredTiger": {"engineConfig": {"cacheSizeGB": 1.5}},
    }
    assert document["net"]["bindIp"] == "myhost", "a value Noust would not write is kept"
    assert document["security"] == {"authorization": "enabled"}
    assert outcome.action == "restart"
    assert ("systemctl", "restart", "mongod") in scripted.calls

    settings.apply({"storage.wiredTiger.engineConfig.cacheSizeGB": "default"})
    assert yaml.safe_load(conf.read_text())["storage"] == {"dbPath": "/var/lib/mongodb"}


# ------------------------------------------------------------------ service


class ServiceEngine(PostgresManager):
    """A PostgreSQL manager reported installed and running."""

    def is_installed(self) -> bool:
        return True

    def is_running(self) -> bool:
        return True


def test_the_service_audits_the_keys_never_the_values(
    postgres: PostgresSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core import audit
    from noust.managers.database.service import DatabaseService

    recorded: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(audit, "record", lambda event, **kwargs: recorded.append((event, kwargs)))
    monkeypatch.setattr(settings_module, "server_resources", lambda: EIGHT_GB)

    def resolve(name: str) -> ServiceEngine:
        engine = ServiceEngine()
        engine.config = StubConfig()
        return engine

    service = DatabaseService(resolve=resolve, engines=lambda: ["postgresql"])
    service.change_engine_settings("postgresql", {"work_mem": "64MB"})

    [(event, kwargs)] = recorded
    assert event == "db.settings.change"
    assert kwargs["target"] == "db:postgresql"
    assert kwargs["details"]["keys"] == ["work_mem"]
    assert "64MB" not in repr(kwargs)


def test_the_service_never_configures_an_engine_in_a_container() -> None:
    from noust.managers.database.service import DatabaseService

    with pytest.raises(ValidationError, match="in a container"):
        DatabaseService().engine_settings("postgresql@proggest.db")


def test_the_settings_change_event_is_declared() -> None:
    from noust.core.audit.catalog import EVENTS

    assert "db.settings.change" in EVENTS
