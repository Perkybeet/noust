# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Turning point-in-time recovery on, taking base backups, sending the archive, recovering (H-32).

The engine is simulated here: a runner that answers the statements the manager sends and keeps
the little state they change (the settings, the archive, whether it runs), so what is pinned is
the order of the steps, what is asked of the engine, what is left on disk and what is undone when
a step fails. The same flows run against a real PostgreSQL in
``tests/integration/engines_run.py`` (``pitr_postgresql``), which is what proves the engine does
what the simulation says it does.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core.applock import app_lock
from noust.core.exceptions import (
    BackupError,
    DatabaseBackupError,
    DatabaseEngineError,
    ValidationError,
)
from noust.core.runner import CommandResult, set_runner
from noust.core.store import NoustStore, get_store
from noust.managers.backup_destination_files import DestinationFileManager, file_sha256
from noust.managers.database import flavours
from noust.managers.database.pitr import (
    BASE_FOLDER,
    ENGINE,
    LOCK_KEY,
    WAL_FOLDER,
    PitrManager,
)
from noust.managers.database.pitr_archive import archive_command, base_id_at, restore_command
from noust.managers.database.pitr_records import PitrRecords
from noust.managers.database.postgres import PostgresManager
from noust.managers.server.host import HostPaths
from tests.database_backup_support import RcloneRunner

SYSTEM_ID = "7430000000000000001"
START = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)


class SimulatedPostgres(RcloneRunner):
    """
    A runner that is a PostgreSQL, as far as the manager can tell.

    Attributes:
        archive: The archive directory the archive command would write to.
        data: The data directory.
        settings: What ``ALTER SYSTEM`` set.
        running: Whether the server runs.
        segment: The number of the segment being written.
        archiving: Whether segments reach the archive.
        recovered: Whether a started server has finished recovering.
        in_recovery_polls: How many times it says it is still recovering after a start.
        fail_start: Whether a started server dies (a recovery that cannot finish).
        facts: What the cluster says about itself.
    """

    def __init__(self, root: Path, archive: Path, data: Path) -> None:
        """
        Args:
            root: Where the destinations live.
            archive: The archive directory.
            data: The data directory.
        """
        super().__init__(root)
        self.archive = archive
        self.data = data
        self.settings: dict[str, str] = {}
        self.running = True
        self.segment = 2
        self.archiving = True
        self.recovered = True
        self.in_recovery_polls = 0
        self.fail_start = False
        self.now: Any = lambda: START
        self.statements: list[str] = []
        self.restarts = 0
        self.facts = {
            "wal_level": "replica",
            "archive_mode": "off",
            "archive_command": "(disabled)",
            "tablespaces": "0",
            "version": "160004",
        }
        self.only_knows("rclone", "psql", "systemctl", "pg_basebackup", "tar", "journalctl")

    def pid_file(self) -> None:
        """Write the file a running server keeps in its data directory."""
        self.data.mkdir(parents=True, exist_ok=True)
        (self.data / "postmaster.pid").write_text(f"{os.getpid()}\n")

    def archive_next(self) -> str:
        """
        Finish the segment being written and archive it.

        Returns:
            Its name.
        """
        name = f"{1:08X}{0:08X}{self.segment:08X}"
        self.segment += 1
        if self.archiving:
            self.archive.mkdir(parents=True, exist_ok=True)
            (self.archive / name).write_bytes(b"\0" * 64)
        return name

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = 120,
        input: str | None = None,
        stdin_path: Path | None = None,
        user: str | None = None,
        check: bool = False,
        secrets: Sequence[str] = (),
    ) -> CommandResult:
        scripted = super().run(
            argv,
            cwd=cwd,
            env=env,
            timeout=timeout,
            input=input,
            stdin_path=stdin_path,
            user=user,
            check=check,
            secrets=secrets,
        )
        words = tuple(argv)
        if words[:2] == ("systemctl", "is-active"):
            return CommandResult(
                argv=words,
                exit_code=0 if self.running else 3,
                stdout="active\n" if self.running else "inactive\n",
            )
        if words[:2] == ("systemctl", "stop"):
            self.running = False
            (self.data / "postmaster.pid").unlink(missing_ok=True)
            return scripted
        if words[:2] in (("systemctl", "start"), ("systemctl", "restart")):
            if words[1] == "restart":
                self.restarts += 1
            if self.fail_start and not self.recovered:
                self.running = False
                return scripted
            self.running = True
            self.pid_file()
            return scripted
        if input is None or ("psql" not in words and "psql" not in " ".join(words)):
            return scripted
        self.statements.append(input)
        if not self.running:
            return CommandResult(argv=words, exit_code=2, stderr="could not connect to server")
        return self.answer(words, input)

    def answer(self, words: tuple[str, ...], sql: str) -> CommandResult:
        """
        Answer a statement the way the engine would.

        Args:
            words: The command.
            sql: The statement sent on standard input.

        Returns:
            The result.
        """
        out = ""
        for statement in re.findall(
            r"ALTER SYSTEM (SET|RESET) (\w+)(?: = '((?:[^']|'')*)')?;", sql
        ):
            action, name, value = statement
            if action == "SET":
                self.settings[name] = value.replace("''", "'")
            else:
                self.settings.pop(name, None)
        if "pg_control_system" in sql:
            mode = self.settings.get("archive_mode", self.facts["archive_mode"])
            command = self.settings.get("archive_command", self.facts["archive_command"])
            level = self.settings.get("wal_level", self.facts["wal_level"])
            out = "|".join(
                [
                    str(self.data),
                    SYSTEM_ID,
                    self.facts["version"],
                    self.facts["tablespaces"],
                    level,
                    mode,
                    command,
                ]
            )
        elif "pg_stat_archiver" in sql:
            files = sorted(
                p.name for p in self.archive.glob("*") if re.fullmatch(r"[0-9A-F]{24}", p.name)
            )
            last = files[-1] if files else ""
            out = f"{len(files)}|0|{last}|{'2026-10-17T12:00:00Z' if last else ''}||"
        elif "pg_switch_wal" in sql or "pg_create_restore_point" in sql:
            if self.settings.get("archive_mode", self.facts["archive_mode"]) in ("on", "always"):
                self.archive_next()
        elif "pg_is_in_recovery" in sql:
            if self.in_recovery_polls > 0:
                self.in_recovery_polls -= 1
                out = "t"
            else:
                out = "f"
        return CommandResult(argv=words, exit_code=0, stdout=out + "\n" if out else "")

    def capture_to_file(
        self,
        argv: Sequence[str],
        destination: Path,
        *,
        compress: bool = False,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = 120,
        user: str | None = None,
        secrets: Sequence[str] = (),
    ) -> CommandResult:
        result = super().capture_to_file(
            argv,
            destination,
            compress=compress,
            cwd=cwd,
            env=env,
            timeout=timeout,
            user=user,
            secrets=secrets,
        )
        if argv[0] == "pg_basebackup" and result.success:
            label = argv[argv.index("--label") + 1]
            first = f"{1:08X}{0:08X}{self.segment:08X}"
            self.archive_next()
            (self.archive / f"{first}.00000028.backup").write_text(
                f"START WAL LOCATION: 0/{self.segment:X}000028 (file {first})\n"
                f"STOP WAL LOCATION: 0/{self.segment:X}000138 (file {first})\n"
                "CHECKPOINT LOCATION: 0/3000060\nBACKUP METHOD: streamed\nBACKUP FROM: primary\n"
                f"START TIME: {self.now():%Y-%m-%d %H:%M:%S} UTC\n"
                f"LABEL: {label}\nSTART TIMELINE: 1\n"
                f"STOP TIME: {self.now() + timedelta(seconds=9):%Y-%m-%d %H:%M:%S} UTC\n"
                "STOP TIMELINE: 1\n"
            )
        return result


class FakeTimer:
    """Stands in for the scheduler: records the timer it was asked for."""

    def __init__(self) -> None:
        self.installed = False

    def install_pitr_timer(self, engine: str) -> str:
        self.installed = True
        return f"noust-backup-pitr-{engine}"

    def remove_pitr_timer(self, engine: str) -> bool:
        was, self.installed = self.installed, False
        return was

    def pitr_timer_state(self, engine: str) -> dict[str, Any]:
        return {"installed": self.installed, "next_run": "soon", "last_run": None}


class Clock:
    """A clock a test moves."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A system root on Ubuntu with the engine's units, so the manager finds its service."""
    (tmp_path / "usr/lib/systemd/system").mkdir(parents=True)
    (tmp_path / "usr/lib/systemd/system/postgresql.service").write_text("")
    monkeypatch.setattr(flavours, "HOST", HostPaths(tmp_path))
    return tmp_path


@pytest.fixture
def engine(tmp_path: Path, host: Path, clock: Clock) -> Iterator[SimulatedPostgres]:
    """The simulated engine, installed as the process-wide runner."""
    fake = SimulatedPostgres(tmp_path / "remote", tmp_path / "wal" / SYSTEM_ID, tmp_path / "pgdata")
    fake.pid_file()
    fake.now = clock
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def clock() -> Clock:
    return Clock(START)


@pytest.fixture
def timer() -> FakeTimer:
    return FakeTimer()


@pytest.fixture
def pitr(tmp_path: Path, engine: SimulatedPostgres, clock: Clock, timer: FakeTimer) -> PitrManager:
    """The manager over the simulated engine, with a clock and a sleep that only move time."""
    return PitrManager(
        PostgresManager(),
        records=PitrRecords(get_store()),
        destinations=DestinationFileManager(runner=engine),
        scheduler=timer,  # type: ignore[arg-type]
        clock=clock,
        sleep=clock.sleep,
        archive_root=tmp_path / "wal",
        backup_root=tmp_path / "bases",
    )


@pytest.fixture
def enabled(pitr: PitrManager, engine: SimulatedPostgres) -> PitrManager:
    """The manager with archiving on and its first base backup taken."""
    pitr.enable(confirmed=True)
    return pitr


# ------------------------------------------------------------------ turning it on


def test_nothing_is_changed_by_planning(pitr: PitrManager, engine: SimulatedPostgres) -> None:
    plan = pitr.plan_enable()

    assert plan["restart"] is True
    assert plan["settings"]["archive_mode"] == "on"
    assert plan["settings"]["archive_command"] == archive_command(plan["archive_path"])
    assert plan["settings"]["archive_timeout"] == "60s"
    assert "wal_level" not in plan["settings"]
    assert engine.settings == {} and engine.restarts == 0
    assert pitr.records.get(ENGINE) is None


def test_a_minimal_wal_level_is_raised_and_remembered(
    pitr: PitrManager, engine: SimulatedPostgres
) -> None:
    engine.facts["wal_level"] = "minimal"

    plan = pitr.plan_enable()

    assert plan["settings"]["wal_level"] == "replica"
    assert plan["wal_level_was"] == "minimal"


def test_the_restart_has_to_be_confirmed_where_it_happens(
    pitr: PitrManager, engine: SimulatedPostgres
) -> None:
    with pytest.raises(ValidationError, match="restarts PostgreSQL") as caught:
        pitr.enable(confirmed=False)

    assert caught.value.field == "confirmed"
    assert engine.settings == {} and engine.restarts == 0


def test_turning_it_on_sets_the_engine_proves_the_archive_and_takes_a_base(
    pitr: PitrManager, engine: SimulatedPostgres, timer: FakeTimer, tmp_path: Path
) -> None:
    status = pitr.enable(confirmed=True, retention_count=3, retention_days=10)

    assert engine.settings["archive_mode"] == "on"
    assert engine.settings["archive_command"] == archive_command(tmp_path / "wal" / SYSTEM_ID)
    assert engine.restarts == 1
    assert timer.installed
    policy = pitr.records.get(ENGINE)
    assert policy is not None and policy.system_id == SYSTEM_ID
    assert (policy.retention_count, policy.retention_days) == (3, 10)
    assert len(status["bases"]) == 1
    assert status["enabled"] is True
    base = pitr.list_bases(SYSTEM_ID)[0]
    archive = pitr.base_path(SYSTEM_ID, base)
    assert archive.is_file()
    assert base.sha256 == file_sha256(archive)
    assert base.start_wal_file.startswith("00000001")


def test_an_archive_that_never_receives_a_segment_is_undone(
    pitr: PitrManager, engine: SimulatedPostgres, timer: FakeTimer
) -> None:
    engine.archiving = False

    with pytest.raises(
        DatabaseEngineError, match="No segment of the log reached the archive"
    ) as caught:
        pitr.enable(confirmed=True)

    assert engine.settings == {}, "the settings are put back"
    assert engine.restarts == 2, "and the engine restarted to take them off"
    assert "Noust put the engine's settings back" in str(caught.value.details)
    assert pitr.records.get(ENGINE) is None and not timer.installed


def test_an_archive_command_that_is_not_nousts_is_never_replaced(
    pitr: PitrManager, engine: SimulatedPostgres
) -> None:
    engine.facts["archive_mode"] = "on"
    engine.facts["archive_command"] = "wal-g wal-push %p"

    with pytest.raises(DatabaseEngineError, match="already archives its log") as caught:
        pitr.enable(confirmed=True)

    assert "wal-g wal-push" in str(caught.value.details)
    assert engine.settings == {}


@pytest.mark.parametrize(
    ("fact", "value", "message"),
    [
        ("tablespaces", "2", "tablespaces"),
        ("version", "110005", "PostgreSQL 12 or newer"),
    ],
)
def test_a_cluster_that_cannot_be_archived_is_refused_with_why(
    pitr: PitrManager, engine: SimulatedPostgres, fact: str, value: str, message: str
) -> None:
    engine.facts[fact] = value

    with pytest.raises(DatabaseEngineError, match=message):
        pitr.plan_enable()


def test_running_it_again_changes_the_policy_without_a_restart(
    enabled: PitrManager, engine: SimulatedPostgres
) -> None:
    engine.facts["archive_mode"] = "on"
    engine.facts["archive_command"] = archive_command(enabled.archive_directory(SYSTEM_ID))

    assert enabled.plan_enable(retention_days=30)["restart"] is False


@pytest.mark.parametrize(
    "values",
    [
        {"base_every_hours": 0},
        {"base_every_hours": 10_000},
        {"retention_count": 0},
        {"retention_days": 1_000_000},
    ],
)
def test_the_values_of_a_policy_are_checked(pitr: PitrManager, values: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        pitr.plan_enable(**values)


def test_a_destination_that_does_not_exist_is_refused_before_the_engine_is_touched(
    pitr: PitrManager, engine: SimulatedPostgres
) -> None:
    with pytest.raises(ValidationError, match="does not exist"):
        pitr.enable(confirmed=True, destinations=[{"name": "nowhere"}])

    assert engine.settings == {} and engine.restarts == 0


def test_a_rehearsal_changes_nothing(
    pitr: PitrManager, engine: SimulatedPostgres, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("noust.managers.database.pitr.is_rehearsal", lambda: True)

    result = pitr.enable(confirmed=True)

    assert result["enabled"] is False and "plan" in result
    assert engine.settings == {} and engine.restarts == 0


# ------------------------------------------------------------------ base backups


def test_a_base_backup_without_archiving_is_refused(pitr: PitrManager) -> None:
    with pytest.raises(DatabaseBackupError, match="Archiving is not on"):
        pitr.take_base()


def test_a_failed_pg_basebackup_leaves_no_file_behind(
    enabled: PitrManager, engine: SimulatedPostgres, clock: Clock
) -> None:
    engine.script(
        ["runuser", "-u", "postgres", "--", "pg_basebackup"],
        exit_code=1,
        stderr="FATAL: no replication slots",
    )
    clock.advance(hours=1)

    with pytest.raises(DatabaseBackupError, match="pg_basebackup failed") as caught:
        enabled.take_base()

    assert "no replication slots" in str(caught.value.details)
    names = [p.name for p in enabled.base_directory(SYSTEM_ID).iterdir()]
    assert not [n for n in names if n.endswith(".partial")]
    assert len(enabled.list_bases(SYSTEM_ID)) == 1


def test_a_base_backup_the_engine_archived_no_record_of_is_discarded(
    enabled: PitrManager, engine: SimulatedPostgres, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock.advance(hours=1)

    def without_record(argv: Any, destination: Path, **kwargs: Any) -> CommandResult:
        return RcloneRunner.capture_to_file(engine, argv, destination, **kwargs)

    monkeypatch.setattr(engine, "capture_to_file", without_record)

    with pytest.raises(DatabaseBackupError, match="archived no record"):
        enabled.take_base()

    assert len(enabled.list_bases(SYSTEM_ID)) == 1


def test_retention_by_number_deletes_the_base_backup_and_the_log_only_it_needed(
    enabled: PitrManager, engine: SimulatedPostgres, clock: Clock
) -> None:
    policy = enabled.records.get(ENGINE)
    assert policy is not None
    policy.retention_count = 2
    policy.retention_days = None
    enabled.records.save(policy)
    first = enabled.list_bases(SYSTEM_ID)[0]
    old_segment = enabled.archive_directory(SYSTEM_ID) / first.start_wal_file
    for _ in range(2):
        clock.advance(hours=3)
        engine.archive_next()
        enabled.take_base()

    bases = enabled.list_bases(SYSTEM_ID)

    assert len(bases) == 2 and first.id not in {b.id for b in bases}
    assert not old_segment.exists(), "the log before the oldest kept base backup is gone"
    assert not (enabled.base_directory(SYSTEM_ID) / f"{first.id}.json").exists()
    assert any(
        p.name.startswith(bases[0].start_wal_file)
        for p in enabled.archive_directory(SYSTEM_ID).iterdir()
    )


# ------------------------------------------------------------------ status


def test_the_status_of_an_engine_that_is_off_says_so(pitr: PitrManager) -> None:
    status = pitr.status()

    assert status["enabled"] is False and status["policy"] is None


def test_the_status_gives_the_window_a_recovery_can_reach(enabled: PitrManager) -> None:
    status = enabled.status()

    assert status["window"]["earliest"] is not None and status["window"]["latest"] is not None
    assert status["archive"]["files"] >= 2
    assert status["timer"]["installed"] is True
    assert status["warnings"] == []


def test_the_status_warns_when_the_engine_stopped_archiving(
    enabled: PitrManager, engine: SimulatedPostgres
) -> None:
    engine.settings["archive_mode"] = "off"

    warnings = enabled.status()["warnings"]

    assert any("not being archived" in w for w in warnings)


def test_the_status_warns_of_a_command_that_is_not_noust(
    enabled: PitrManager, engine: SimulatedPostgres
) -> None:
    engine.settings["archive_command"] = "true"

    assert any("not the one Noust set" in w for w in enabled.status()["warnings"])


def test_the_status_warns_when_the_engine_is_down_and_when_a_base_is_overdue(
    enabled: PitrManager, engine: SimulatedPostgres, clock: Clock
) -> None:
    engine.running = False
    clock.advance(hours=2 * 168 + 1)

    warnings = enabled.status()["warnings"]

    assert any("is not running" in w for w in warnings)
    assert any("twice as old" in w for w in warnings)


# ------------------------------------------------------------------ sending the archive


@pytest.fixture
def with_destination(pitr: PitrManager, engine: SimulatedPostgres) -> PitrManager:
    """The manager with archiving on and a destination in the policy."""
    pitr.destinations.add(
        "nas", "sftp", {"host": "nas.example.com", "user": "noust", "pass": "s3cr3t"}
    )
    pitr.enable(
        confirmed=True, destinations=[{"name": "nas"}], retention_count=2, retention_days=None
    )
    return pitr


def remote_dir(engine: SimulatedPostgres, kind: str) -> Path:
    """Where the fake destination keeps a kind of file."""
    return engine.root / "nas" / "wasm-backups" / "databases" / ENGINE / "pitr" / SYSTEM_ID / kind


def test_the_archive_and_the_base_backups_reach_the_destination(
    with_destination: PitrManager, engine: SimulatedPostgres
) -> None:
    report = with_destination.sync()

    wal = sorted(p.name for p in remote_dir(engine, WAL_FOLDER).iterdir())
    local = sorted(p.name for p in with_destination.archived_files(SYSTEM_ID))
    assert wal == local
    base = with_destination.list_bases(SYSTEM_ID)[0]
    assert (remote_dir(engine, BASE_FOLDER) / f"{base.id}.tar.gz").is_file()
    assert (remote_dir(engine, BASE_FOLDER) / f"{base.id}.tar.gz.json").is_file()
    assert report["nas"]["uploaded"] == [base.id]


def test_sending_again_sends_only_what_is_new(
    with_destination: PitrManager, engine: SimulatedPostgres
) -> None:
    with_destination.sync()
    engine.archive_next()

    report = with_destination.sync()

    assert report["nas"]["uploaded"] == []
    assert len(report["nas"]["segments"]) == 1


def test_a_destination_that_fails_does_not_stop_the_others_and_is_reported(
    with_destination: PitrManager, engine: SimulatedPostgres
) -> None:
    engine.fail["copyto"] = "Failed to copyto: connection refused"

    with pytest.raises(BackupError, match="could not be sent everywhere") as caught:
        with_destination.sync()

    assert "nas" in str(caught.value.details) and "connection refused" in str(caught.value.details)


def test_the_destination_is_pruned_of_what_retention_dropped(
    with_destination: PitrManager, engine: SimulatedPostgres, clock: Clock
) -> None:
    with_destination.sync()
    first = with_destination.list_bases(SYSTEM_ID)[0]
    for _ in range(2):
        clock.advance(hours=3)
        engine.archive_next()
        with_destination.take_base()

    report = with_destination.sync()

    held = {p.name for p in remote_dir(engine, BASE_FOLDER).iterdir() if p.name.endswith(".tar.gz")}
    assert f"{first.id}.tar.gz" not in held and len(held) == 2
    assert first.start_wal_file in report["nas"]["deleted"]
    assert not (remote_dir(engine, WAL_FOLDER) / first.start_wal_file).exists()


def test_the_upkeep_takes_a_base_when_it_is_due_and_records_the_run(
    with_destination: PitrManager, engine: SimulatedPostgres, clock: Clock
) -> None:
    clock.advance(hours=1)
    assert with_destination.run()["base"] is None

    clock.advance(hours=200)
    engine.archive_next()
    result = with_destination.run()

    assert result["base"] is not None
    policy = with_destination.records.get(ENGINE)
    assert policy is not None and policy.last_status == "ok" and policy.last_base_at
    assert policy.last_sync_at is not None


def test_a_failed_step_of_the_upkeep_is_recorded_and_raised_after_the_others_ran(
    with_destination: PitrManager, engine: SimulatedPostgres
) -> None:
    engine.fail["copyto"] = "Failed to copyto: disk full"

    with pytest.raises(BackupError, match="upkeep had problems"):
        with_destination.run()

    policy = with_destination.records.get(ENGINE)
    assert policy is not None and policy.last_status == "failed"
    assert "disk full" in (policy.last_error or "")


def test_the_archive_comes_back_from_the_destination(
    with_destination: PitrManager, engine: SimulatedPostgres
) -> None:
    with_destination.sync()
    expected = sorted(p.name for p in with_destination.archived_files(SYSTEM_ID))
    base = with_destination.list_bases(SYSTEM_ID)[0]
    for path in [*with_destination.archived_files(SYSTEM_ID)]:
        path.unlink()
    for path in with_destination.base_directory(SYSTEM_ID).iterdir():
        path.unlink()

    fetched = with_destination.fetch("nas")

    assert fetched["bases"] == [base.id]
    assert sorted(p.name for p in with_destination.archived_files(SYSTEM_ID)) == expected
    restored = with_destination.list_bases(SYSTEM_ID)[0]
    assert restored.sha256 == base.sha256 and restored.sha256 == file_sha256(
        with_destination.base_path(SYSTEM_ID, restored)
    )


def test_fetching_from_a_server_that_is_not_in_the_policy_is_refused(
    with_destination: PitrManager,
) -> None:
    with pytest.raises(ValidationError, match="not a destination of the archive"):
        with_destination.fetch("elsewhere")


# ------------------------------------------------------------------ recovering


def test_a_recovery_needs_a_base_old_enough_and_an_instant_the_archive_reaches(
    enabled: PitrManager, clock: Clock
) -> None:
    base = enabled.list_bases(SYSTEM_ID)[0]

    with pytest.raises(ValidationError, match="No base backup is old enough"):
        enabled.plan_restore(base.finished_at - timedelta(hours=1))
    with pytest.raises(ValidationError, match="later than the archive reaches"):
        enabled.plan_restore(datetime.now(timezone.utc) + timedelta(days=2))


def test_the_plan_names_the_base_and_where_the_data_goes(
    enabled: PitrManager, engine: SimulatedPostgres
) -> None:
    base = enabled.list_bases(SYSTEM_ID)[0]

    plan = enabled.plan_restore(None)

    assert plan.base.id == base.id and plan.target is None
    assert plan.data_directory == str(engine.data)
    assert plan.saved_as.startswith(f"{engine.data}.noust-before-recovery-")
    assert plan.missing == [] and plan.warnings


def test_a_gap_in_the_log_refuses_the_recovery_before_anything_is_touched(
    enabled: PitrManager, engine: SimulatedPostgres
) -> None:
    base = enabled.list_bases(SYSTEM_ID)[0]
    start = int(base.start_wal_file[16:], 16)
    for number in (start + 1, start + 2, start + 3):
        (enabled.archive_directory(SYSTEM_ID) / f"{1:08X}{0:08X}{number:08X}").write_bytes(b"\0")
    (enabled.archive_directory(SYSTEM_ID) / f"{1:08X}{0:08X}{start + 2:08X}").unlink()

    with pytest.raises(DatabaseBackupError, match="gap") as caught:
        enabled.plan_restore(None)

    assert f"{start + 2:08X}" in str(caught.value.details)
    assert engine.running and not any("systemctl stop" in " ".join(c) for c in engine.calls)


def test_a_base_backup_that_changed_since_it_was_taken_is_refused(enabled: PitrManager) -> None:
    base = enabled.list_bases(SYSTEM_ID)[0]
    enabled.base_path(SYSTEM_ID, base).write_bytes(b"tampered")

    with pytest.raises(DatabaseBackupError, match="does not match its digest"):
        enabled.plan_restore(None)


def test_a_recovery_must_be_confirmed_where_it_happens(
    enabled: PitrManager, engine: SimulatedPostgres
) -> None:
    with pytest.raises(ValidationError, match="replaces the whole cluster") as caught:
        enabled.restore(None, confirmed=False)

    assert caught.value.field == "confirmed"
    assert engine.data.is_dir() and engine.running


def test_a_recovery_replaces_the_cluster_and_leaves_the_old_one_aside(
    enabled: PitrManager, engine: SimulatedPostgres
) -> None:
    (engine.data / "marker").write_text("before")
    target = datetime.now(timezone.utc) - timedelta(seconds=1)
    engine.recovered = True
    engine.in_recovery_polls = 2

    result = enabled.restore(target, confirmed=True)

    saved = Path(result["saved_as"])
    assert (saved / "marker").read_text() == "before"
    assert not (engine.data / "marker").exists()
    assert engine.running
    stopped = [i for i, call in enumerate(engine.calls) if call[:2] == ("systemctl", "stop")]
    extracted = [i for i, call in enumerate(engine.calls) if "tar" in call and "-xzf" in call]
    started = [i for i, call in enumerate(engine.calls) if call[:2] == ("systemctl", "start")]
    assert stopped and extracted and started and stopped[0] < extracted[0] < started[0]
    auto = (engine.data / "postgresql.auto.conf").read_text()
    assert f"restore_command = '{restore_command(enabled.archive_directory(SYSTEM_ID))}'" in auto
    assert re.search(r"recovery_target_time = '[0-9-]+ [0-9:]+\+00'", auto)
    assert "recovery_target_action = 'promote'" in auto
    assert (engine.data / "recovery.signal").is_file()
    assert (engine.data / "pg_wal").is_dir()
    for name in (
        "restore_command",
        "recovery_target_time",
        "recovery_target_action",
        "recovery_target_timeline",
    ):
        assert any(f"ALTER SYSTEM RESET {name};" in s for s in engine.statements)


def test_a_recovery_to_the_end_of_the_log_names_no_target(
    enabled: PitrManager, engine: SimulatedPostgres
) -> None:
    enabled.restore(None, confirmed=True)

    auto = (engine.data / "postgresql.auto.conf").read_text()
    assert "recovery_target_time" not in auto and "recovery_target_action" not in auto


def test_the_timeline_to_follow_is_named_when_there_are_branches(
    enabled: PitrManager, engine: SimulatedPostgres
) -> None:
    history = enabled.archive_directory(SYSTEM_ID) / "00000002.history"
    history.write_text("1\t0/3000000\tbefore 2000-01-01 00:00:00+00\n")

    plan = enabled.plan_restore(datetime.now(timezone.utc) - timedelta(seconds=1))

    assert plan.timeline == 2
    enabled.restore(datetime.now(timezone.utc) - timedelta(seconds=1), confirmed=True)
    assert "recovery_target_timeline = '2'" in (engine.data / "postgresql.auto.conf").read_text()


def test_a_recovery_that_cannot_finish_puts_the_previous_data_back(
    enabled: PitrManager, engine: SimulatedPostgres
) -> None:
    (engine.data / "marker").write_text("before")
    engine.recovered = False
    engine.fail_start = True

    with pytest.raises(DatabaseBackupError, match="the previous data is back") as caught:
        enabled.restore(None, confirmed=True)

    assert "PostgreSQL stopped during the recovery" in str(caught.value.__cause__)
    assert (engine.data / "marker").read_text() == "before", "the previous directory is back"
    failed = Path(f"{engine.data}.noust-failed-recovery")
    assert failed.is_dir() and not (failed / "marker").exists()
    assert not list(engine.data.parent.glob("pgdata.noust-before-recovery-*"))


def test_a_server_that_does_not_stop_changes_nothing(
    enabled: PitrManager, engine: SimulatedPostgres, monkeypatch: pytest.MonkeyPatch
) -> None:
    (engine.data / "marker").write_text("before")
    monkeypatch.setattr(PitrManager, "_postmaster_running", staticmethod(lambda data: True))

    with pytest.raises(DatabaseBackupError, match="did not stop"):
        enabled.restore(None, confirmed=True)

    assert (engine.data / "marker").read_text() == "before"
    assert not list(engine.data.parent.glob("pgdata.noust-*"))


def test_a_recovery_that_never_ends_is_called_failed_after_its_time(
    enabled: PitrManager, engine: SimulatedPostgres
) -> None:
    engine.in_recovery_polls = 10**9

    with pytest.raises(DatabaseBackupError, match="the previous data is back") as caught:
        enabled.restore(None, confirmed=True, timeout=30)

    assert "did not finish in 30 seconds" in str(caught.value.__cause__)


# ------------------------------------------------------------------ turning it off


def test_turning_it_off_resets_the_engine_and_keeps_the_archive(
    enabled: PitrManager, engine: SimulatedPostgres, timer: FakeTimer
) -> None:
    result = enabled.disable(confirmed=True)

    assert result == {"disabled": True, "deleted": 0}
    assert "archive_mode" not in engine.settings and "archive_command" not in engine.settings
    assert engine.restarts == 2
    assert enabled.records.get(ENGINE) is None and not timer.installed
    assert enabled.archived_files(SYSTEM_ID) and enabled.list_bases(SYSTEM_ID)


def test_turning_it_off_must_be_confirmed(enabled: PitrManager, engine: SimulatedPostgres) -> None:
    with pytest.raises(ValidationError, match="restarts PostgreSQL"):
        enabled.disable(confirmed=False)

    assert engine.settings["archive_mode"] == "on"


def test_turning_it_off_can_delete_everything_here_and_on_the_destination(
    with_destination: PitrManager, engine: SimulatedPostgres
) -> None:
    with_destination.sync()

    result = with_destination.disable(confirmed=True, delete_archive=True)

    assert result["deleted"] >= 4
    assert not with_destination.archived_files(SYSTEM_ID)
    assert not with_destination.list_bases(SYSTEM_ID)
    assert not list(remote_dir(engine, WAL_FOLDER).iterdir())
    assert not list(remote_dir(engine, BASE_FOLDER).iterdir())


def test_a_wal_level_noust_raised_goes_back_with_it(
    pitr: PitrManager, engine: SimulatedPostgres
) -> None:
    engine.facts["wal_level"] = "minimal"
    pitr.enable(confirmed=True)
    assert engine.settings["wal_level"] == "replica"

    pitr.disable(confirmed=True)

    assert "wal_level" not in engine.settings


def test_turning_off_what_was_never_on_says_so(pitr: PitrManager) -> None:
    assert pitr.disable(confirmed=True)["disabled"] is False


def test_the_base_ids_name_the_instant_they_start(enabled: PitrManager, clock: Clock) -> None:
    base = enabled.list_bases(SYSTEM_ID)[0]

    assert base.id == base_id_at(START)
    assert (
        json.loads((enabled.base_directory(SYSTEM_ID) / f"{base.id}.json").read_text())["system_id"]
        == SYSTEM_ID
    )


# ------------------------------------------------------------------ one at a time


@pytest.fixture
def busy() -> Iterator[None]:
    """Another operation holds the lock of this area, from another thread."""
    held = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with app_lock(LOCK_KEY, "recovery"):
            held.set()
            release.wait(30)

    thread = threading.Thread(target=hold)
    thread.start()
    assert held.wait(10)
    try:
        yield
    finally:
        release.set()
        thread.join(10)


def test_a_change_is_refused_while_another_runs(
    enabled: PitrManager, engine: SimulatedPostgres, busy: None
) -> None:
    for call in (
        lambda: enabled.take_base(),
        lambda: enabled.restore(None, confirmed=True),
        lambda: enabled.sync(),
        lambda: enabled.disable(confirmed=True),
    ):
        with pytest.raises(DatabaseBackupError, match=r"recovery .* is still running") as caught:
            call()
        assert "Only one change" in str(caught.value.details)

    assert engine.settings["archive_mode"] == "on" and engine.data.is_dir()


def test_the_upkeep_that_finds_the_area_busy_skips_without_failing(
    with_destination: PitrManager, busy: None
) -> None:
    result = with_destination.run()

    assert result["base"] is None and "recovery" in result["skipped"]
    policy = with_destination.records.get(ENGINE)
    assert policy is not None and policy.last_status is None


def test_the_lock_is_free_again_after_an_operation(
    enabled: PitrManager, engine: SimulatedPostgres, clock: Clock
) -> None:
    clock.advance(hours=1)
    enabled.take_base()
    clock.advance(hours=1)

    enabled.take_base()

    assert len(enabled.list_bases(SYSTEM_ID)) == 3
