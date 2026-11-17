# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A night of bad network between a server and its backup destination (item 201), as a test can run it.

The real night had fifteen scheduled backups, nine of which could not be sent (the sends timed out and
the listings said ``Command timed out after 60s``), interleaved with the six that did go. Nothing here
touches a network or a clock: the destination is a directory the fake rclone answers out of, the bad
network is a switch the test flips between runs, and time is :class:`Night`'s own.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core import timeutil
from noust.core.runner import DEFAULT_TIMEOUT, CommandResult
from noust.core.store import BackupScheduleRecord, get_store
from noust.managers.backup_destination_files import DestinationFileManager
from noust.managers.backup_manager import BackupManager, BackupMetadata
from tests.support.database_backup_support import RcloneRunner

__all__ = [
    "APPS",
    "FAILING_RUNS",
    "FlakyRclone",
    "Night",
    "app_name",
    "make_night",
]

#: The fifteen applications of the night, one scheduled backup each.
APPS = [f"app{index:02d}.example.com" for index in range(15)]

#: Which of the fifteen runs the network was bad for: nine, interleaved with the six that went through.
FAILING_RUNS = frozenset({1, 2, 4, 5, 7, 9, 11, 12, 14})

#: What rclone said in the real night, word for word as far as the report kept it.
TIMED_OUT = (
    "Failed to copyto: sftp: write: i/o timeout: connection lost\n"
    "ERROR : Attempt 1/1 failed with 1 errors and: write tcp 192.0.2.10:51022->198.51.100.7:22: "
    "i/o timeout"
)
LISTING_TIMED_OUT = "Could not list the folder: Command timed out after 60s"


def app_name(domain: str) -> str:
    """
    Args:
        domain: An application's domain.

    Returns:
        Its name, the domain with dots as dashes.
    """
    return domain.replace(".", "-")


class FlakyRclone(RcloneRunner):
    """
    A fake rclone whose network can be down.

    While :attr:`down` is true every call that reaches a remote times out, the way the real ones did;
    ``obscure`` is local and never fails. :attr:`reached` counts the calls that were answered by the
    remote, which is what a retry is measured by.

    Attributes:
        down: Whether the network is bad now.
        failing: A function of the call (the verb and what it names) that may fail it even while the
            network is up, for a test that fails exactly the second attempt.
    """

    def __init__(self, root: Path, **kwargs: Any) -> None:
        super().__init__(root, **kwargs)
        self.down = False
        self.failing: Callable[[str, Sequence[str]], str | None] | None = None
        self.refusals: list[str] = []
        self.timeouts_seen: list[int] = []

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        input: str | None = None,
        stdin_path: Path | None = None,
        user: str | None = None,
        check: bool = False,
        secrets: Sequence[str] = (),
    ) -> CommandResult:
        if len(argv) >= 2 and argv[0] == "rclone" and argv[1] != "obscure":
            verb = argv[1]
            self.timeouts_seen.append(timeout)
            if self.down:
                text = LISTING_TIMED_OUT if verb in ("lsjson", "lsf", "cat") else TIMED_OUT
                self.calls.append(tuple(argv))
                self.envs.append(dict(env) if env is not None else None)
                return CommandResult(
                    argv=tuple(argv), exit_code=1, stderr=text, timed_out=True, duration=timeout
                )
            said = self.failing(verb, list(argv[2:])) if self.failing is not None else None
            if said is not None:
                self.calls.append(tuple(argv))
                self.envs.append(dict(env) if env is not None else None)
                return CommandResult(argv=tuple(argv), exit_code=1, stderr=said)
        return super().run(
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

    def uploads(self) -> list[tuple[str, ...]]:
        """
        Returns:
            The ``copyto`` calls that put a file on a remote.
        """
        return [call for call in self.calls if call[:2] == ("rclone", "copyto")]


class Night:
    """
    The night: a clock, a destination, fifteen schedules and what each run leaves behind.

    Attributes:
        runner: The fake rclone.
        start: When the night begins (UTC).
        now: The moment the test has moved to.
        backups: Every metadata a fake run took, by the application's domain.
    """

    def __init__(self, runner: FlakyRclone, monkeypatch: pytest.MonkeyPatch, tmp: Path) -> None:
        self.runner = runner
        self.start = datetime(2026, 11, 17, 1, 0, tzinfo=timezone.utc)
        self.now = self.start
        self.backups: dict[str, BackupMetadata] = {}
        self._tmp = tmp
        self._monkeypatch = monkeypatch
        # The one clock: every moment Noust reads in this test is the night's.
        monkeypatch.setattr(timeutil, "utc_now", lambda: self.now)
        # The directory a manager keeps its backups in is a class attribute, so a sandbox can move it.
        monkeypatch.setattr(BackupManager, "DEFAULT_BACKUP_DIR", tmp / "backups")
        # The archives of the night are a few bytes, not tarballs: nothing here is a verification's.
        monkeypatch.setattr("noust.managers.backup_scheduler.verify_days", lambda config: 0)

    # ------------------------------------------------------------------ the clock

    def move_to(self, moment: datetime) -> None:
        """
        Args:
            moment: The time every clock Noust reads should now say.
        """
        self.now = moment

    def advance(self, **delta: float) -> None:
        """
        Args:
            **delta: What ``timedelta`` takes.
        """
        self.now = self.now + timedelta(**delta)

    def epoch(self) -> float:
        """
        Returns:
            The night's time as epoch seconds, which is what the monitor's clock reads.
        """
        return self.now.timestamp()

    # ------------------------------------------------------------------ the machine

    def destination(self, name: str = "nas") -> None:
        """
        Make the destination an operator would: an SFTP server.

        Args:
            name: Its name.
        """
        self.runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")
        DestinationFileManager(runner=self.runner).add(
            name, "sftp", {"host": "nas.example.net", "user": "noust", "pass": "s3cr3t-value"}
        )

    def schedule(self, domain: str, *, destinations: Sequence[str] = ("nas",)) -> None:
        """
        Give an application a daily schedule that sends to the destinations.

        Args:
            domain: The application.
            destinations: Where its backups go.
        """
        get_store().save_backup_schedule(
            BackupScheduleRecord(
                app_domain=domain,
                schedule="daily",
                destinations=[
                    {"name": name, "retention_count": None, "retention_days": None}
                    for name in destinations
                ],
            )
        )

    def take_backups(self) -> None:
        """Make ``BackupManager.create`` write a small real backup instead of archiving anything."""
        night = self

        def create(manager: BackupManager, **kwargs: Any) -> BackupMetadata:
            domain = str(kwargs["domain"])
            name = app_name(domain)
            stamp = night.now.strftime("%Y%m%d_%H%M%S")
            backup_id = f"{name}_{stamp}"
            directory = manager._get_app_backup_dir(name)
            directory.mkdir(parents=True, exist_ok=True)
            content = f"backup of {domain} at {stamp}".encode()
            (directory / f"{backup_id}.tar.gz").write_bytes(content)
            metadata = BackupMetadata(
                id=backup_id,
                domain=domain,
                app_name=name,
                created_at=night.now.replace(tzinfo=None).isoformat(timespec="seconds"),
                size_bytes=len(content),
                app_type="static",
                version="4.3.0",
                description="",
                includes_env=True,
                includes_node_modules=False,
                tags=list(kwargs.get("tags") or []),
            )
            (directory / f"{backup_id}.json").write_text(json.dumps(metadata.to_dict()))
            night.backups[domain] = metadata
            return metadata

        self._monkeypatch.setattr(BackupManager, "create", create)

    def remote_has(self, domain: str, backup_id: str, destination: str = "nas") -> bool:
        """
        Args:
            domain: The application.
            backup_id: A backup of it.
            destination: The destination.

        Returns:
            Whether the destination holds that backup's archive.
        """
        folder = self.runner.root / destination / "noust-backups" / app_name(domain)
        return (folder / f"{backup_id}.tar.gz").is_file()


def make_night(
    runner_root: Path, monkeypatch: pytest.MonkeyPatch, tmp: Path
) -> Iterator[Night] | Night:
    """
    Build the night over a fresh fake rclone.

    Args:
        runner_root: Where the fake remotes live.
        monkeypatch: The test's patching helper.
        tmp: The test's temporary directory.

    Returns:
        The night, with its destination, schedules and backups in place.
    """
    runner = FlakyRclone(runner_root)
    night = Night(runner, monkeypatch, tmp)
    night.destination()
    for domain in APPS:
        night.schedule(domain)
    night.take_backups()
    return night
