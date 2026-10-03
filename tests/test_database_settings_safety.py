# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

# Fixtures imported from test_database_settings come back as parameters of
# the same name, which is how pytest hands them over.
# ruff: noqa: F811

"""
An engine's settings never leave it exposed, broken or emptier than it was.

The security and data-safety reviews of 3.3 found ways the settings sequence
could do each of those; every one is pinned here:

- removing a listen setting falls back to what the engine's other files say,
  which is an exposure unless it is known to be loopback, and what the engine
  reports once it answers is held to what was confirmed;
- a symbolic link where Noust reads or writes the configuration is refused;
- every file is put back, whatever stops the change half way, and a failed
  reload reads the previous files again;
- one change of an engine's settings at a time;
- a slow but healthy start (Redis loading, a unit still activating) is waited
  for, not rolled back;
- a Redis change that would refuse writes, drop keys or stop persisting needs
  a confirmation, Redis saves its data before Noust restarts it, and cache
  sizes stop at 90% of memory;
- a rehearsal never waits for an engine that was never restarted;
- every PostgreSQL step targets the one cluster, and ``include_dir`` is
  recognised however it is spelt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core import applock
from noust.core.exceptions import ConfirmationRequired, DatabaseEngineError, ValidationError
from noust.core.fs import DryRunFileSystem, set_fs
from noust.core.runner import CommandResult, DryRunRunner, set_runner
from noust.managers.database import settings as settings_module
from noust.managers.database.postgres import PostgresManager
from noust.managers.database.settings import PostgresSettings, RedisSettings
from tests.test_database_settings import (  # noqa: F401 - fixtures
    EIGHT_GB,
    GB,
    PG_SETTINGS,
    PSQL,
    ScriptedRunner,
    StubConfig,
    failed,
    mysql_settings,
    ok,
    pg_answers,
    postgres,
    redis_settings,
    root,
    scripted,
)

OURS = "etc/postgresql/16/main/conf.d/90-noust.conf"
MAIN = "etc/postgresql/16/main/postgresql.conf"


def pg_listening(addresses: str):
    """
    Answer psql as a cluster that listens on some addresses.

    Args:
        addresses: What ``listen_addresses`` reports.

    Returns:
        The answer function.
    """
    reported = PG_SETTINGS.replace(
        "listen_addresses|localhost|", f"listen_addresses|{addresses}|", 1
    )
    base = pg_answers()

    def answer(argv: tuple[str, ...], stdin: str | None) -> CommandResult | None:
        if stdin and "pg_settings" in stdin:
            return ok(reported)
        return base(argv, stdin)

    return answer


def new_postgres() -> PostgresSettings:
    manager = PostgresManager()
    manager.config = StubConfig()
    return PostgresSettings(manager, EIGHT_GB)


def restarts(scripted: ScriptedRunner, unit: str = "postgresql@16-main") -> int:
    return len([c for c in scripted.calls if c[:3] == ("systemctl", "restart", unit)])


# ------------------------------------------------- listening, finding 7


def test_removing_the_listen_setting_falls_back_to_the_distribution_s_wildcard(
    postgres: PostgresSettings, scripted: ScriptedRunner, root: Path
) -> None:
    (root / MAIN).write_text("listen_addresses = '*'\ninclude_dir = 'conf.d'\n")
    (root / OURS).write_text("listen_addresses = 'localhost'\n")

    with pytest.raises(ConfirmationRequired) as excinfo:
        postgres.apply({"listen_addresses": "default"})

    assert "beyond this server" in excinfo.value.warnings[0]
    assert (root / OURS).read_text() == "listen_addresses = 'localhost'\n"
    assert restarts(scripted) == 0


def test_the_ens_profile_refuses_removing_a_listen_setting_that_exposes(
    postgres: PostgresSettings, root: Path
) -> None:
    (root / MAIN).write_text("listen_addresses = '*'\ninclude_dir = 'conf.d'\n")
    (root / OURS).write_text("listen_addresses = 'localhost'\n")

    with pytest.raises(ValidationError) as excinfo:
        postgres.apply({"listen_addresses": "default"}, confirm=True, remote_listen_allowed=False)

    assert "ENS profile" in excinfo.value.message


def test_a_fallback_noust_cannot_read_counts_as_an_exposure(
    postgres: PostgresSettings, root: Path
) -> None:
    (root / MAIN).write_text("include 'elsewhere.conf'\ninclude_dir = 'conf.d'\n")
    (root / OURS).write_text("listen_addresses = 'localhost'\n")

    with pytest.raises(ConfirmationRequired) as excinfo:
        postgres.apply({"listen_addresses": "default"})

    assert "cannot tell" in excinfo.value.warnings[0]


def test_a_loopback_fallback_needs_no_confirmation(postgres: PostgresSettings, root: Path) -> None:
    (root / OURS).write_text("listen_addresses = 'localhost,::1'\n")

    outcome = postgres.apply({"listen_addresses": "default"})

    assert outcome.exposed is False
    assert "listen_addresses" not in (root / OURS).read_text()


def test_an_engine_found_listening_beyond_loopback_after_the_change_is_put_back(
    scripted: ScriptedRunner, root: Path
) -> None:
    # The files say loopback; the running cluster, once restarted, says every
    # address (a command-line option, a file Noust did not see). It is held to
    # what was confirmed: nothing, so the previous file is back.
    scripted.answer = pg_listening("*")
    (root / OURS).write_text("listen_addresses = 'localhost,::1'\n")

    with pytest.raises(ConfirmationRequired):
        new_postgres().apply({"listen_addresses": "default"})

    assert (root / OURS).read_text() == "listen_addresses = 'localhost,::1'\n"
    assert restarts(scripted) == 2  # the change, and the one on the previous file


def test_the_outcome_says_exposed_when_the_running_engine_is(
    scripted: ScriptedRunner, root: Path
) -> None:
    scripted.answer = pg_listening("*")
    (root / OURS).write_text("listen_addresses = 'localhost,::1'\n")

    outcome = new_postgres().apply({"listen_addresses": "default"}, confirm=True)

    assert outcome.exposed is True
    assert any("beyond this server" in warning for warning in outcome.warnings)


def test_removing_redis_s_bind_with_none_in_its_file_is_an_exposure(
    scripted: ScriptedRunner, root: Path
) -> None:
    (root / "etc/redis/redis.conf").write_text("save 900 1\n")
    (root / "etc/redis/noust.conf").write_text("bind 127.0.0.1\n")

    with pytest.raises(ConfirmationRequired):
        redis_settings(scripted).apply({"bind": "default"})


# ---------------------------------------------------- links, finding 8


@pytest.mark.parametrize(
    "link",
    [
        "etc/postgresql/16/main/conf.d",
        "etc/postgresql/16/main/postgresql.conf",
        "etc/postgresql/16/main/conf.d/90-noust.conf",
        "etc/postgresql/16/main",
    ],
)
def test_a_symbolic_link_in_the_cluster_s_configuration_is_refused(
    postgres: PostgresSettings, scripted: ScriptedRunner, root: Path, tmp_path: Path, link: str
) -> None:
    elsewhere = tmp_path / "elsewhere"
    path = root / link
    if path.is_dir():
        path.rename(elsewhere)
    else:
        elsewhere.write_text(path.read_text() if path.exists() else "")
        path.unlink(missing_ok=True)
    path.symlink_to(elsewhere)

    with pytest.raises(DatabaseEngineError, match="symbolic link"):
        postgres.apply({"work_mem": "64MB"})
    with pytest.raises(DatabaseEngineError, match="symbolic link"):
        postgres.report()

    assert not [c for c in scripted.calls if c[0] == "systemctl"]


@pytest.mark.parametrize("link", ["etc/redis/redis.conf", "etc/redis/noust.conf", "etc/redis"])
def test_a_symbolic_link_in_redis_s_configuration_is_refused(
    scripted: ScriptedRunner, root: Path, tmp_path: Path, link: str
) -> None:
    elsewhere = tmp_path / "elsewhere"
    path = root / link
    if path.is_dir():
        path.rename(elsewhere)
    else:
        elsewhere.write_text(path.read_text() if path.exists() else "")
        path.unlink(missing_ok=True)
    path.symlink_to(elsewhere)

    with pytest.raises(DatabaseEngineError, match="symbolic link"):
        redis_settings(scripted).apply({"maxmemory-policy": "noeviction", "appendonly": "yes"})


# ------------------------------------------------- putting back, A1-A3


def test_a_file_replaced_before_its_hand_over_failed_is_still_put_back(
    scripted: ScriptedRunner, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main = root / "etc/redis/redis.conf"
    original = main.read_text()
    monkeypatch.setattr(settings_module, "hand_over_file", lambda *args, **kwargs: False)
    settings = redis_settings(scripted)
    # The test runs as an ordinary account, so redis.conf is not root's and
    # is handed back after it is replaced: that hand-over fails here.

    with pytest.raises(DatabaseEngineError):
        settings.apply({"appendonly": "yes"})

    assert main.read_text() == original


def test_an_interruption_half_way_puts_the_previous_file_back(
    scripted: ScriptedRunner, root: Path
) -> None:
    base = pg_answers()
    interrupted = []

    def answer(argv: tuple[str, ...], stdin: str | None) -> CommandResult | None:
        if argv[:2] == ("systemctl", "reload") and not interrupted:
            interrupted.append(argv)
            raise KeyboardInterrupt
        return base(argv, stdin)

    scripted.answer = answer
    (root / OURS).write_text("work_mem = '8MB'\n")

    with pytest.raises(KeyboardInterrupt):
        new_postgres().apply({"work_mem": "64MB"})

    assert (root / OURS).read_text() == "work_mem = '8MB'\n"
    reloads = [c for c in scripted.calls if c[:2] == ("systemctl", "reload")]
    assert len(reloads) == 2, "the cluster read the previous file again"


def test_a_running_value_noust_would_not_write_does_not_stop_the_way_back(
    scripted: ScriptedRunner, root: Path
) -> None:
    settings = mysql_settings(scripted, mariadb=False)
    reported = scripted.answer

    def answer(argv: tuple[str, ...], stdin: str | None) -> CommandResult | None:
        if stdin and stdin.startswith("SHOW GLOBAL VARIABLES"):
            return ok("max_connections\tlots\nlong_query_time\t10.000000\n")
        if stdin == "SET GLOBAL max_connections = 300;":
            return failed("ERROR 1231 (42000)")
        return reported(argv, stdin) if reported else None

    scripted.answer = answer

    with pytest.raises(DatabaseEngineError) as excinfo:
        settings.apply({"max_connections": "300"})

    assert "could not all be put back" in excinfo.value.details
    assert not (root / "etc/mysql/mysql.conf.d/99-noust.cnf").exists()


def test_a_failed_reload_reloads_again_on_the_previous_files(
    postgres: PostgresSettings, scripted: ScriptedRunner, root: Path
) -> None:
    scripted.queue(
        ("systemctl", "reload", "postgresql@16-main"),
        failed("Job for postgresql@16-main.service failed."),
        ok(),
    )

    with pytest.raises(DatabaseEngineError):
        postgres.apply({"work_mem": "64MB"})

    reloads = [c for c in scripted.calls if c[:2] == ("systemctl", "reload")]
    assert len(reloads) == 2
    assert not (root / OURS).exists()


# --------------------------------------------------------- the lock, A4


def test_one_change_of_an_engine_s_settings_at_a_time(
    postgres: PostgresSettings, scripted: ScriptedRunner, root: Path
) -> None:
    held = applock._acquire("database-settings@postgresql", "settings change")
    try:
        with pytest.raises(DatabaseEngineError, match="Another change"):
            postgres.apply({"work_mem": "64MB"})
    finally:
        applock._release(held)

    assert not (root / OURS).exists()
    assert postgres.apply({"work_mem": "64MB"}).changed == ["work_mem"]


# ------------------------------------------------------- slow starts, A5


def test_a_unit_still_activating_is_waited_for(
    scripted: ScriptedRunner, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings_module, "WAIT_SECONDS", 2)
    monkeypatch.setattr(settings_module, "LOADING_SECONDS", 50)
    base = pg_answers()
    pings = {"count": 0}

    def answer(argv: tuple[str, ...], stdin: str | None) -> CommandResult | None:
        if stdin == "SELECT 1;":
            pings["count"] += 1
            return ok("1") if pings["count"] > 10 else failed("the database system is starting up")
        if argv[:2] == ("systemctl", "is-active"):
            return CommandResult(argv=(), exit_code=3, stdout="activating\n")
        return base(argv, stdin)

    scripted.answer = answer

    outcome = new_postgres().apply({"shared_buffers": "1GB"})

    assert outcome.action == "restart"
    assert restarts(scripted) == 1


def test_redis_loading_its_data_set_is_waited_for(
    scripted: ScriptedRunner, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings_module, "WAIT_SECONDS", 2)
    monkeypatch.setattr(settings_module, "LOADING_SECONDS", 50)
    settings = redis_settings(scripted)
    reported = scripted.answer
    pings = {"count": 0}

    def answer(argv: tuple[str, ...], stdin: str | None) -> CommandResult | None:
        if argv[-1] == "PING":
            pings["count"] += 1
            if pings["count"] <= 10:
                return ok("LOADING Redis is loading the dataset in memory")
        return reported(argv, stdin) if reported else None

    scripted.answer = answer

    outcome = settings.apply({"appendonly": "yes"})

    assert outcome.changed == ["appendonly"]
    assert (root / "etc/redis/noust.conf").exists()


def test_the_restart_deadline_lets_a_long_recovery_finish() -> None:
    assert settings_module.RESTART_TIMEOUT >= 900
    assert settings_module.LOADING_SECONDS >= 900


# ------------------------------------------------------- Redis data, B


def redis_with(scripted: ScriptedRunner, extra) -> RedisSettings:
    settings = redis_settings(scripted)
    reported = scripted.answer

    def answer(argv: tuple[str, ...], stdin: str | None) -> CommandResult | None:
        answered = extra(argv)
        if answered is not None:
            return answered
        return reported(argv, stdin) if reported else None

    scripted.answer = answer
    return settings


def holding(used: int):
    def extra(argv: tuple[str, ...]) -> CommandResult | None:
        if argv[3:] == ("INFO", "memory"):
            return ok(f"# Memory\r\nused_memory:{used}\r\nused_memory_human:x\r\n")
        return None

    return extra


def test_maxmemory_below_what_redis_holds_needs_a_confirmation(
    scripted: ScriptedRunner, root: Path
) -> None:
    settings = redis_with(scripted, holding(2 * GB))

    with pytest.raises(ConfirmationRequired) as excinfo:
        settings.apply({"maxmemory": "1GB"})

    assert "refuses every write" in excinfo.value.warnings[0]
    assert not (root / "etc/redis/noust.conf").exists()

    with pytest.raises(ConfirmationRequired) as excinfo:
        settings.apply({"maxmemory": "1GB", "maxmemory-policy": "allkeys-lru"})

    assert "drops keys" in excinfo.value.warnings[0]

    assert settings.apply({"maxmemory": "1GB"}, confirm=True).changed == ["maxmemory"]


def test_maxmemory_above_what_redis_holds_needs_nothing(
    scripted: ScriptedRunner, root: Path
) -> None:
    settings = redis_with(scripted, holding(100 * 1024**2))

    assert settings.apply({"maxmemory": "1GB"}).changed == ["maxmemory"]


def test_turning_persistence_off_needs_a_confirmation(scripted: ScriptedRunner, root: Path) -> None:
    settings = redis_settings(scripted)
    reported = scripted.answer

    def answer(argv: tuple[str, ...], stdin: str | None) -> CommandResult | None:
        if argv[3:6] == ("CONFIG", "GET", "appendonly"):
            return ok("appendonly\nyes\n")
        return reported(argv, stdin) if reported else None

    scripted.answer = answer

    with pytest.raises(ConfirmationRequired) as excinfo:
        settings.apply({"appendonly": "no", "save": "off"})

    assert len(excinfo.value.warnings) == 2
    assert "appendonly no" in excinfo.value.warnings[0]
    assert "save off" in excinfo.value.warnings[1]
    assert not (root / "etc/redis/noust.conf").exists()


def persistence(*states: str, status: str = "ok"):
    queue = list(states)

    def extra(argv: tuple[str, ...]) -> CommandResult | None:
        if argv[3:] == ("INFO", "persistence"):
            state = queue.pop(0) if len(queue) > 1 else queue[0]
            return ok(
                f"# Persistence\r\nrdb_bgsave_in_progress:{state}\r\n"
                f"rdb_last_bgsave_status:{status}\r\n"
            )
        if argv[3:] == ("BGSAVE",):
            return ok("Background saving started")
        return None

    return extra


def test_redis_saves_its_data_before_noust_restarts_it(
    scripted: ScriptedRunner, root: Path
) -> None:
    settings = redis_with(scripted, persistence("0", "1", "1", "0"))

    outcome = settings.apply({"bind": "127.0.0.1"})

    assert outcome.action == "restart"
    unit = settings.unit()
    saved = scripted.calls.index(("redis-cli", "-n", "0", "BGSAVE"))
    restarted = scripted.calls.index(("systemctl", "restart", unit))
    assert saved < restarted
    waits = [c for c in scripted.calls[saved:restarted] if c[3:] == ("INFO", "persistence")]
    assert len(waits) >= 3, "the snapshot is waited for until it has finished"


def test_a_snapshot_that_fails_stops_the_restart(scripted: ScriptedRunner, root: Path) -> None:
    settings = redis_with(scripted, persistence("0", status="err"))

    with pytest.raises(DatabaseEngineError) as excinfo:
        settings.apply({"bind": "127.0.0.1"})

    assert "snapshot before the restart failed" in excinfo.value.details
    assert not [c for c in scripted.calls if c[:2] == ("systemctl", "restart")]
    assert not (root / "etc/redis/noust.conf").exists()


@pytest.mark.parametrize(
    ("engine", "key", "value"),
    [
        ("postgresql", "shared_buffers", "7600MB"),
        ("mysql", "innodb_buffer_pool_size", "7600MB"),
        ("redis", "maxmemory", "7600MB"),
    ],
)
def test_a_cache_stops_at_nine_tenths_of_memory(engine: str, key: str, value: str) -> None:
    specs = {
        "postgresql": settings_module.POSTGRES_SPECS,
        "mysql": settings_module.MYSQL_SPECS,
        "redis": settings_module.REDIS_SPECS,
    }[engine]
    spec = next(spec for spec in specs if spec.key == key)

    with pytest.raises(ValidationError) as excinfo:
        settings_module.parse_value(spec, value, engine, EIGHT_GB)

    assert "90% of this server's memory" in excinfo.value.message
    assert settings_module.parse_value(spec, "7GB", engine, EIGHT_GB) == 7 * GB


# ----------------------------------------------------------- rehearsal, C


def test_a_rehearsal_of_a_redis_change_never_waits(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inner = ScriptedRunner()
    rehearsal = DryRunRunner(inner)
    set_runner(rehearsal)
    set_fs(DryRunFileSystem())

    def never(seconds: float) -> None:
        raise AssertionError("a rehearsal waited for an engine it never restarted")

    monkeypatch.setattr(settings_module, "_sleep", never)
    try:
        settings = redis_settings(inner)
        outcome = settings.apply({"maxmemory-policy": "allkeys-lru", "bind": "127.0.0.1"})
    finally:
        set_runner(None)
        set_fs(None)

    assert outcome.changed == ["bind", "maxmemory-policy"]
    assert not (root / "etc/redis/noust.conf").exists()


# ------------------------------------------------------- one cluster, D


def test_several_running_clusters_are_refused_by_name(scripted: ScriptedRunner, root: Path) -> None:
    base = pg_answers()

    def answer(argv: tuple[str, ...], stdin: str | None) -> CommandResult | None:
        if argv[:2] == ("pg_lsclusters", "--no-header"):
            return ok(
                "14  main    5432 online postgres /var/lib/postgresql/14/main log\n"
                "16  main    5433 online postgres /var/lib/postgresql/16/main log\n"
            )
        return base(argv, stdin)

    scripted.answer = answer

    with pytest.raises(DatabaseEngineError) as excinfo:
        new_postgres().apply({"work_mem": "64MB"})

    assert "14/main, 16/main" in excinfo.value.message
    assert not (root / OURS).exists()


def test_every_psql_targets_the_cluster_being_configured(
    scripted: ScriptedRunner, root: Path
) -> None:
    base = pg_answers()

    def answer(argv: tuple[str, ...], stdin: str | None) -> CommandResult | None:
        if argv[:2] == ("pg_lsclusters", "--no-header"):
            # 14/main is pg_wrapper's default (port 5432) but is down.
            return ok(
                "14  main    5432 down   postgres /var/lib/postgresql/14/main log\n"
                "16  main    5433 online postgres /var/lib/postgresql/16/main log\n"
            )
        return base(argv, stdin)

    scripted.answer = answer

    new_postgres().apply({"work_mem": "64MB"})

    psql = [
        env
        for call, env in zip(scripted.calls, scripted.envs, strict=True)
        if call[: len(PSQL)] == PSQL
    ]
    assert psql and all(env and env.get("PGCLUSTER") == "16/main" for env in psql)
    assert ("systemctl", "reload", "postgresql@16-main") in scripted.calls


@pytest.mark.parametrize(
    "line",
    [
        "include_dir 'conf.d'",
        "include_dir='conf.d'",
        "INCLUDE_DIR = 'conf.d'",
        "include_dir = conf.d",
        "include_dir = './conf.d'   # Noust's file is in here",
        "include_dir = '{root}/etc/postgresql/16/main/conf.d'",
    ],
)
def test_include_dir_is_recognised_however_it_is_spelt(
    postgres: PostgresSettings, root: Path, line: str
) -> None:
    main = root / MAIN
    text = f"port = 5432\n{line.format(root='')}\n"
    main.write_text(text)

    postgres.apply({"work_mem": "64MB"})

    assert main.read_text() == text
