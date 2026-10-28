# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_database_pitr.py`` that other tests build on."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import CommandResult, SubprocessRunner, set_runner
from noust.core.store import NoustStore, get_store
from noust.managers.backup_destination_files import DestinationFileManager
from noust.managers.database import flavours
from noust.managers.database.pitr import PitrManager
from noust.managers.database.pitr_records import PitrRecords
from noust.managers.database.postgres import PostgresManager
from noust.managers.server.host import HostPaths
from tests.support.database_backup_support import RcloneRunner

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
        #: What each extraction of a base backup was given on its standard input.
        self.extracted: list[bytes] = []
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
        if argv and argv[0] == "openssl":
            # Sealing is proved with the real program, by the tests that turn it on.
            return SubprocessRunner().run(
                argv, cwd=cwd, env=env, timeout=timeout, input=input, user=user, check=check
            )
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
        if words[:1] == ("tar",) and stdin_path is not None:
            self.extracted.append(stdin_path.read_bytes())
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
