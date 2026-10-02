# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Running an update in a transient systemd unit, and following it from outside.

Why the update does not run inside whoever asked for it: the ``noust`` package
is in the repository being updated, and its post-install script restarts the
console. A job running the package manager inside the console's process dies with
that restart, in the middle of dpkg, and leaves the package database half
configured. A ``systemd-run`` unit is in its own cgroup: restarting
``noust-web`` does not touch it, an SSH session closing does not touch it, and it
finishes and writes its record whatever became of the process that started it.

What follows is the other half: the unit's output is in the journal, its exit is
in the record it writes, and :meth:`UpdateUnit.follow` turns both back into
lines and a result for whoever is watching, the console's job or a terminal.
:meth:`UpdateUnit.reconcile` does the same after the watcher itself went away, so
a console that restarted in the middle of an update finds it again.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from noust.core import paths
from noust.core.fs import FileSystem, get_fs, is_rehearsal
from noust.core.runner import CommandRunner, get_runner
from noust.managers.server.errors import ServerError
from noust.managers.server.host import HostPaths
from noust.managers.server.pkg.base import UPGRADE_TIMEOUT
from noust.managers.server.updates import (
    RECORDS_KEPT,
    RecordStore,
    UpdateRecord,
    UpdatesManager,
)
from noust.managers.transient_unit import (
    JobVerdict,
    UnitEnding,
    UnitJobKind,
    UnitState,
    decode_message,
    parse_journal,
    parse_show,
    read_journal,
    unit_state,
    with_output,
)

if TYPE_CHECKING:
    from noust.core.store import JobRecord

__all__ = [
    "UNIT_PREFIX",
    "Reconciliation",
    "UnitState",
    "UpdateUnit",
    "decode_message",
    "job_kind",
    "noust_command",
    "parse_journal",
    "parse_show",
    "start_and_follow",
]

#: Every transient unit of an update starts with this, followed by the run's id.
UNIT_PREFIX = "noust-os-update-"

#: How much longer than the update's own deadline its unit is allowed to live.
RUNTIME_MARGIN_SECONDS = 120

#: Seconds between looks at the journal and the unit while following.
FOLLOW_POLL_SECONDS = 2.0

#: How long a follower waits for a unit that never ends. Longer than the unit's
#: own ``RuntimeMaxSec``, so it is the unit that gives up first and says why.
FOLLOW_MAX_SECONDS = UPGRADE_TIMEOUT + 300

#: Lines of a lost unit's journal that go into its record.
LOST_TAIL_LINES = 40

#: Variables of the console's own environment the transient unit needs to find
#: the same data directory, which a unit does not inherit.
_PASSED_ENVIRONMENT = (paths.DATA_DIR_ENV,)


@dataclass(frozen=True)
class Reconciliation:
    """
    What was found when looking again at updates a restarted console had lost track of.

    Attributes:
        reattach: Runs whose unit is still going; someone should follow them.
        lost: Runs that said they were running and whose unit is gone, now
            marked failed. They wrote no result: the process was killed, or the
            machine restarted.
    """

    reattach: tuple[UpdateRecord, ...] = ()
    lost: tuple[UpdateRecord, ...] = ()


def noust_command() -> list[str]:
    """
    Name the command that starts Noust, as a transient unit can run it.

    Returns:
        This interpreter running the package, by its absolute path. Not the
        ``noust`` on the path: that may be another installation, an older one
        that has no ``server updates run``, and a unit that fails to start is an
        update that never began. The interpreter that is running this is the
        installation that has the command.
    """
    return [sys.executable, "-m", "noust"]


class UpdateUnit:
    """Starts, follows and reconciles updates that run in transient units."""

    def __init__(
        self,
        *,
        records: RecordStore | None = None,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        host: HostPaths | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """
        Args:
            records: Where runs are written down.
            runner: Command runner; the process-wide one when omitted.
            fs: Filesystem seam; the process-wide one when omitted.
            host: Where the system files are.
            sleep: Waits between looks; replaced in tests.
        """
        self.host = host or HostPaths()
        self._runner = runner
        self._fs = fs
        self.records = records or RecordStore(fs=fs)
        self._sleep = sleep

    @property
    def runner(self) -> CommandRunner:
        """The runner in force right now."""
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """The filesystem in force right now."""
        return self._fs or get_fs()

    def available(self) -> bool:
        """
        Report whether a transient unit can be used here.

        Returns:
            True when systemd is running and ``systemd-run`` exists. Where it
            is not (a container without systemd), the update runs in the
            process that asked, which is safe there because nothing restarts
            the console either.
        """
        return self.runner.exists("systemd-run") and self.host.systemd_marker.is_dir()

    @staticmethod
    def unit_name(update_id: str) -> str:
        """
        Name the unit of a run.

        Args:
            update_id: The run's identifier.

        Returns:
            The unit name, without ``.service``.
        """
        return f"{UNIT_PREFIX}{update_id}"

    def start(
        self,
        update_id: str,
        *,
        scope: str,
        full: bool = False,
        allow_removals: bool = False,
        job_id: str | None = None,
        actor: str | None = None,
    ) -> str:
        """
        Start an update in its own unit and return at once.

        Args:
            update_id: The run's identifier.
            scope: ``security`` or ``all``.
            full: A full upgrade.
            allow_removals: The removals were confirmed.
            job_id: The console job that asks, for the record.
            actor: Who asked, for the record.

        Returns:
            The unit's name.

        Raises:
            ServerError: ``systemd-run`` refused; the error carries its output.
        """
        unit = self.unit_name(update_id)
        argv = [
            "systemd-run",
            f"--unit={unit}",
            # A unit that ends is forgotten at once, failed or not: nothing is
            # left in ``systemctl --failed`` for the operator to clean up. The
            # result lives in the record the run writes, and the words in the
            # journal.
            "--collect",
            "--description=Noust operating system update",
            "--property=StandardOutput=journal",
            "--property=StandardError=journal",
            # Later than the package manager's own deadline, so it is the command that gives
            # up and says why, and the unit only backs it up.
            f"--property=RuntimeMaxSec={UPGRADE_TIMEOUT + RUNTIME_MARGIN_SECONDS}",
        ]
        for name in _PASSED_ENVIRONMENT:
            if name in os.environ:
                argv.append(f"--setenv={name}={os.environ[name]}")
        argv += [
            "--",
            *noust_command(),
            "server",
            "updates",
            "run",
            "--id",
            update_id,
            "--scope",
            scope,
            "--unit",
            unit,
        ]
        if full:
            argv.append("--full")
        if allow_removals:
            argv.append("--allow-removals")
        if job_id:
            argv += ["--job-id", job_id]
        if actor:
            argv += ["--actor", actor]

        result = self.runner.run(argv, timeout=60)
        if not result.success:
            raise ServerError(
                "Could not start the update in its own unit",
                "systemd-run refused. The update was not started.",
                output="\n".join(p for p in (result.stdout.strip(), result.stderr.strip()) if p),
            )
        return unit

    def state(self, update_id: str) -> UnitState:
        """
        Ask systemd about a run's unit.

        Args:
            update_id: The run's identifier.

        Returns:
            Its state; a unit that ended and was collected is not loaded, and
            one systemd did not answer about is not ``known``.
        """
        return unit_state(self.runner, self.unit_name(update_id))

    def _drain(self, unit: str, cursor: str | None, on_line: Callable[[str], None]) -> str | None:
        """
        Deliver the journal lines the unit wrote since the last look.

        Args:
            unit: The unit's name.
            cursor: The journal position after the last line delivered.
            on_line: Receives each line.

        Returns:
            The position after the last line delivered.
        """
        for entry in read_journal(self.runner, unit, after_cursor=cursor):
            on_line(entry.message)
            cursor = entry.cursor
        return cursor

    def follow(
        self,
        update_id: str,
        on_line: Callable[[str], None],
        *,
        poll_seconds: float = FOLLOW_POLL_SECONDS,
        max_seconds: float = FOLLOW_MAX_SECONDS,
    ) -> UpdateRecord:
        """
        Deliver a run's output as it appears, and return what it recorded.

        Args:
            update_id: The run's identifier.
            on_line: Receives each line the unit wrote to the journal.
            poll_seconds: Seconds between looks.
            max_seconds: When to stop waiting for a unit that never ends.

        Returns:
            The finished record.

        Raises:
            ServerError: The unit ended without recording a result, or did not
                end in time; the error carries the last lines it wrote.
        """
        unit = self.unit_name(update_id)
        cursor: str | None = None
        tail: list[str] = []

        def relay(line: str) -> None:
            tail.append(line)
            del tail[:-LOST_TAIL_LINES]
            on_line(line)

        deadline = time.monotonic() + max_seconds
        while True:
            cursor = self._drain(unit, cursor, relay)
            state = self.state(update_id)
            if state.known and not state.active:
                # The last lines are written before the state flips; one more
                # look, or the end of the output is what gets lost.
                self._drain(unit, cursor, relay)
                break
            if time.monotonic() > deadline:
                raise ServerError(
                    "Gave up waiting for the update to finish",
                    f"The unit {unit} is still running. It keeps going on its own; "
                    f"'journalctl -fu {unit}' follows it.",
                    output="\n".join(tail),
                )
            self._sleep(poll_seconds)

        record = self.records.read(update_id)
        if record is None or record.status == "running":
            raise ServerError(
                "The update ended without recording a result",
                "Its process was killed, or the machine restarted. Check 'dpkg --audit' "
                "(or 'rpm -Va') before trying again.",
                output="\n".join(tail),
            )
        return record

    def reconcile(self) -> Reconciliation:
        """
        Find again the updates a restarted console had lost track of.

        Returns:
            The runs still going, which the caller should follow again, and the
            runs whose unit is gone, which are marked failed here.
        """
        reattach: list[UpdateRecord] = []
        lost: list[UpdateRecord] = []
        for record in self.records.running():
            if record.unit is None:
                # Started in a terminal, without a unit: nothing to ask. It is
                # given the deadline a unit would have had.
                if not self._older_than_deadline(record):
                    continue
            else:
                state = self.state(record.id)
                # Unanswered is not ended: followed again, the follower keeps
                # asking until systemd answers or the deadline passes.
                if state.active or not state.known:
                    reattach.append(record)
                    continue
            # The unit may have written its result while systemd was asked:
            # the record read before is stale, and writing it would replace a
            # success with "failed".
            fresh = self.records.read(record.id)
            if fresh is None or fresh.status != "running":
                continue
            record = fresh
            record.status = "failed"
            record.finished_at = datetime.now(timezone.utc).isoformat()
            record.error = (
                "The update ended without recording a result: its process was killed "
                "or the machine restarted"
            )
            if record.unit:
                record.tail = self._journal_tail(record.unit)
            self.records.write(record)
            lost.append(record)
        return Reconciliation(reattach=tuple(reattach), lost=tuple(lost))

    def _older_than_deadline(self, record: UpdateRecord) -> bool:
        """
        Tell whether a run started long enough ago that it cannot still be going.

        Args:
            record: The run.

        Returns:
            True when it started more than its longest allowed duration ago.
        """
        try:
            started = datetime.fromisoformat(record.started_at)
        except ValueError:
            return True
        return (datetime.now(timezone.utc) - started).total_seconds() > FOLLOW_MAX_SECONDS

    def _journal_tail(self, unit: str) -> list[str]:
        """
        Read the last lines a unit wrote.

        Args:
            unit: The unit's name.

        Returns:
            Up to :data:`LOST_TAIL_LINES` lines, verbatim.
        """
        result = self.runner.run(
            [
                "journalctl",
                f"--unit={unit}.service",
                f"--lines={LOST_TAIL_LINES}",
                "--output=cat",
                "--no-pager",
            ],
            timeout=30,
        )
        return result.stdout.splitlines()


def start_and_follow(
    manager: UpdatesManager,
    unit: UpdateUnit,
    update_id: str,
    on_line: Callable[[str], None],
    *,
    scope: str,
    full: bool,
    allow_removals: bool,
    job_id: str | None,
    actor: str | None,
) -> UpdateRecord:
    """
    Run an update the safe way and return its record.

    In a transient unit where systemd is there to provide one, and in this
    process where it is not. The console's job and the terminal's ``apply`` both
    come through here, which is why they cannot behave differently.

    Args:
        manager: The updates manager, for the in-process path.
        unit: The transient unit runner.
        update_id: The run's identifier.
        on_line: Receives every line of output.
        scope: ``security`` or ``all``.
        full: A full upgrade.
        allow_removals: The removals were confirmed.
        job_id: The console job that asks.
        actor: Who asked.

    Returns:
        The finished record; under ``--dry-run`` a stand-in whose status is
        ``dry-run``.
    """
    from noust.managers.server.pkg.base import UpdateScope

    if unit.available():
        unit.start(
            update_id,
            scope=scope,
            full=full,
            allow_removals=allow_removals,
            job_id=job_id,
            actor=actor,
        )
        if is_rehearsal():
            # Nothing was started, so there is nothing to follow: the rehearsal
            # has said what it would run, which is all it can say.
            return UpdateRecord(id=update_id, scope=scope, full=full, status="dry-run")
        return unit.follow(update_id, on_line)
    return manager.apply(
        UpdateScope(scope),
        full=full,
        allow_removals=allow_removals,
        on_line=on_line,
        update_id=update_id,
        job_id=job_id,
        actor=actor,
    )


def job_kind(records: RecordStore | None = None) -> UnitJobKind:
    """
    Say how a console job that applied updates is reconciled after a restart.

    The run's own record is the verdict, not only systemd's: it holds what the
    job returns (the packages, whether a reboot is due), and it is what the
    update wrote when it ended, whatever became of the console.

    Args:
        records: Where runs are written down; the default store when omitted.

    Returns:
        The kind, for :mod:`noust.web.job_reconcile`.
    """

    def store() -> RecordStore:
        return records or RecordStore()

    def find_unit(job: JobRecord) -> str | None:
        for record in store().recent(RECORDS_KEPT):
            if record.job_id == job.id and record.unit:
                return record.unit
        return None

    def verdict(job: JobRecord, unit: str, ending: UnitEnding, lines: list[str]) -> JobVerdict:
        record = store().read(unit.removeprefix(UNIT_PREFIX))
        if record is None or record.status == "running":
            return JobVerdict(
                False,
                error=with_output(
                    "The update ended without recording a result: its process was killed or "
                    "the machine restarted. Check 'dpkg --audit' (or 'rpm -Va') before trying "
                    "again",
                    lines,
                ),
            )
        if record.status == "failed":
            return JobVerdict(
                False, error=with_output(record.error or "The update failed", record.tail or lines)
            )
        return JobVerdict(True, result=record.to_dict())

    return UnitJobKind(
        find_unit=find_unit,
        verdict=verdict,
        max_seconds=FOLLOW_MAX_SECONDS,
        # As os_update_job audits it when it sees the end itself.
        audit_event="server.update",
        audit_target="packages",
        audit_details=lambda job, unit: {
            "action": "apply",
            "update": unit.removeprefix(UNIT_PREFIX),
        },
    )
