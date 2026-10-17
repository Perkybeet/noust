# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Point-in-time recovery of PostgreSQL: the write-ahead log archived, base backups, and a
recovery to an instant (checklist H-32).

A dump says what a database held when it was taken. This says what it held at any instant
since: the engine copies every segment of its write-ahead log to an archive as it fills,
Noust takes a base backup now and then, and a recovery puts a base backup back and replays
the log over it until the instant asked for. Nothing here is a tool of its own: it is the
engine's documented mechanism (``archive_command``, ``pg_basebackup``, ``recovery.signal``,
``recovery_target_time``) driven through the one runner, so a server that loses Noust can
still recover by hand from the same files. That is also why no external archiver is used:
the projects that were the usual choice are in doubt, and a recovery that depends on a
program nobody maintains is the wrong place to take that risk.

**What it asks of the engine.** ``archive_mode = on`` (a restart, once), ``archive_command``
(Noust's, :func:`~noust.managers.database.pitr_archive.archive_command`), ``archive_timeout``
(so a quiet server still archives every minute, which is the granularity of a recovery) and a
``wal_level`` of at least ``replica``. They are set with ``ALTER SYSTEM`` so they work on any
layout and undo with ``RESET``. An ``archive_command`` that is not Noust's is never replaced.

**Where things are.** The log is archived to a directory of the ``postgres`` account's home
(``noust-wal/<system identifier>``), because the engine must write there and Noust's backup
directory is root's alone; the base backups are root's, in the backups directory, each with a
sidecar that says everything a recovery needs, so a server rebuilt from the files alone can
recover. Both are sent to the policy's destinations (``noust db pitr sync``).

**A recovery is in place and can be undone.** The data directory is renamed aside, never
deleted; the base backup is extracted, the engine started in recovery and waited for until it
promotes. When it does not, the failed attempt is kept for inspection and the previous
directory is put back and started.
"""

from __future__ import annotations

import functools
import json
import pwd
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeVar

from noust.core import audit, paths
from noust.core.applock import AppBusyError, app_lock
from noust.core.backup_files import open_backup, seal_in_place
from noust.core.exceptions import (
    BackupError,
    DatabaseBackupError,
    DatabaseEngineError,
    ValidationError,
)
from noust.core.fs import FileSystem, get_fs, is_rehearsal
from noust.core.store import get_store
from noust.managers.backup_destination_files import DestinationFileManager, file_sha256
from noust.managers.backup_scheduler import BackupScheduler
from noust.managers.database.pitr_archive import (
    BaseBackup,
    archive_command,
    base_id_at,
    bases_to_drop,
    choose_timeline,
    format_recovery_time,
    missing_segments,
    parse_backup_label,
    parse_history,
    prunable_wal,
    restore_command,
    select_base,
    wal_position,
)
from noust.managers.database.pitr_records import PitrPolicy, PitrRecords
from noust.managers.database.postgres import PostgresManager

__all__ = [
    "ENGINE",
    "PitrManager",
    "RestorePlan",
]

#: The engine a recovery is offered for.
ENGINE = "postgresql"

#: The account the engine runs as, which writes the archive and extracts a base backup.
ENGINE_ACCOUNT = "postgres"

#: A base backup is taken again when the last is older than this, unless the policy says.
DEFAULT_BASE_EVERY_HOURS = 168

#: How far back a recovery must reach unless the policy says.
DEFAULT_KEEP_DAYS = 7

#: The engine switches to a new segment at least this often, so a quiet server still archives
#: and an instant is never more than this far from the log.
ARCHIVE_TIMEOUT_SECONDS = 60

#: Seconds a base backup may take.
BASE_BACKUP_TIMEOUT = 6 * 3600

#: Seconds the engine is given to answer after a restart, and to prove the archive works.
READY_TIMEOUT = 180
ARCHIVE_PROOF_TIMEOUT = 120

#: Seconds a recovery may take before it is called failed.
RECOVERY_TIMEOUT = 3600

#: Seconds between two looks at the engine while waiting for it.
POLL_SECONDS = 2.0

#: Seconds a stopping engine is given to finish its shutdown checkpoint and exit.
STOP_TIMEOUT_SECONDS = 180

#: Seconds a started engine is given to write its process file before a missing one means it
#: died. ``systemctl start postgresql`` succeeds on Debian whatever the cluster does (the unit
#: only wants the clusters'), so the engine's own process is the thing to look at.
STARTUP_GRACE_SECONDS = 20

#: Journal lines an error shows.
JOURNAL_LINES = 40

#: The destinations' folders: ``databases/postgresql/pitr/<system identifier>/<kind>``.
BASE_FOLDER = "base"
WAL_FOLDER = "wal"

#: What the engine is given besides the command: the interval of the forced switch.
_SETTINGS_WE_SET = ("archive_mode", "archive_command", "archive_timeout")


#: The name the one-at-a-time lock of this area is kept under (``noust.core.applock``).
LOCK_KEY = "database-postgresql-pitr"

_Method = TypeVar("_Method", bound=Callable[..., Any])


def _exclusive(operation: str, *, skip_when_busy: bool = False) -> Callable[[_Method], _Method]:
    """
    Make a method run alone: one change to the archive, the base backups or the cluster at a time.

    A base backup taken while a recovery replaces the cluster, or two upkeep runs sending the same
    files, would each be fine alone and wrong together. The lock is the one every long operation
    of Noust takes (a kernel ``flock`` released when its process exits, so a crash leaves nothing
    to remove) and it is reentrant: the upkeep that takes a base backup holds it already.

    Args:
        operation: What the method does, as the refused caller will read it.
        skip_when_busy: Answer "skipped" instead of failing when something else holds the lock
            (the upkeep timer, which fires every few minutes whatever an operator is doing).

    Returns:
        The decorator.
    """

    def decorate(method: _Method) -> _Method:
        @functools.wraps(method)
        def guarded(self: PitrManager, *args: Any, **kwargs: Any) -> Any:
            try:
                with app_lock(LOCK_KEY, operation):
                    return method(self, *args, **kwargs)
            except AppBusyError as busy:
                if busy.domain != LOCK_KEY:
                    raise
                holder = busy.holder
                running = (
                    f"{holder.operation} (since {holder.started_at}, pid {holder.pid})"
                    if holder
                    else "another operation"
                )
                if skip_when_busy:
                    return {"base": None, "pruned": {}, "sync": {}, "skipped": running}
                raise DatabaseBackupError(
                    f"Cannot start the {operation}: {running} is still running",
                    details="Only one change to the archive, the base backups or the cluster "
                    "runs at a time. Wait for it to finish, then retry. The lock is released "
                    "the moment its process exits.",
                ) from busy

        return guarded  # type: ignore[return-value]

    return decorate


@dataclass
class RestorePlan:
    """
    What a recovery would do, worked out before anything is touched.

    Attributes:
        base: The base backup it starts from.
        target: The instant it stops at; None for the end of the log.
        data_directory: The engine's data directory, which is renamed aside.
        saved_as: Where the current data directory goes.
        segments: How many segments of the log sit in the archive.
        last_archived_at: When the newest was archived: the latest instant that can be reached.
        missing: Segments the log lacks between the base backup and the newest.
        warnings: Things the operator should know before going ahead.
        timeline: The timeline the recovery follows; None for the newest.
    """

    base: BaseBackup
    target: datetime | None
    data_directory: str
    saved_as: str
    segments: int
    last_archived_at: datetime | None
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    timeline: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The plan as plain data, JSON-ready.
        """
        return {
            "base": self.base.to_dict(),
            "target": self.target.isoformat(timespec="seconds") if self.target else None,
            "data_directory": self.data_directory,
            "saved_as": self.saved_as,
            "segments": self.segments,
            "last_archived_at": (
                self.last_archived_at.isoformat(timespec="seconds")
                if self.last_archived_at
                else None
            ),
            "missing": list(self.missing),
            "warnings": list(self.warnings),
            "timeline": self.timeline,
            "restarts_engine": True,
        }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sql_literal(value: str) -> str:
    """
    Quote a value for ``ALTER SYSTEM SET``.

    Args:
        value: Text Noust built; never input.

    Returns:
        The single-quoted literal.
    """
    return "'" + value.replace("'", "''") + "'"


class PitrManager:
    """
    Continuous archiving, base backups and recovery to an instant for the host's PostgreSQL.
    """

    def __init__(
        self,
        manager: PostgresManager | None = None,
        *,
        records: PitrRecords | None = None,
        destinations: DestinationFileManager | None = None,
        scheduler: BackupScheduler | None = None,
        fs: FileSystem | None = None,
        clock: Callable[[], datetime] = _utcnow,
        sleep: Callable[[float], None] = time.sleep,
        archive_root: Path | None = None,
        backup_root: Path | None = None,
    ) -> None:
        """
        Args:
            manager: The PostgreSQL manager. A default one when None.
            records: The store of policies. The process-wide store's when None.
            destinations: Sends and fetches files. A default one when None.
            scheduler: Installs the upkeep timer. A default one when None.
            fs: The filesystem seam. The process-wide one when None.
            clock: The current instant, injected so a test controls it.
            sleep: Waits between two looks at the engine, injected for the same reason.
            archive_root: The directory the archives live under. The ``postgres`` account's
                home plus ``noust-wal`` when None.
            backup_root: The directory the base backups live under. The backups directory's
                ``databases/pitr`` when None.
        """
        self.manager = manager or PostgresManager()
        self._records = records
        self._destinations = destinations
        self._scheduler = scheduler
        self._fs = fs
        self._clock = clock
        self._sleep = sleep
        self._archive_root = archive_root
        self._backup_root = backup_root

    def _audit(self, event: str, outcome: str = "ok", **details: Any) -> None:
        """
        Record a change in the audit trail.

        Args:
            event: A catalog name, such as ``db.pitr.enable``.
            outcome: ``ok`` or ``failure``.
            **details: Anything else worth keeping. Never a credential.
        """
        audit.record(event, target=f"database:{ENGINE}", outcome=outcome, details=details)

    # ==================== Collaborators ====================

    @property
    def records(self) -> PitrRecords:
        """The policies' store."""
        if self._records is None:
            self._records = PitrRecords(get_store())
        return self._records

    @property
    def destinations(self) -> DestinationFileManager:
        """The destinations, with the file verbs the archive is sent with."""
        if self._destinations is None:
            self._destinations = DestinationFileManager()
        return self._destinations

    @property
    def scheduler(self) -> BackupScheduler:
        """The scheduler that owns the upkeep timer."""
        if self._scheduler is None:
            self._scheduler = BackupScheduler()
        return self._scheduler

    @property
    def fs(self) -> FileSystem:
        """The filesystem seam every change goes through."""
        return self._fs or get_fs()

    # ==================== Where things are ====================

    def archive_directory(self, system_id: str) -> Path:
        """
        Name where a cluster's log is archived.

        Args:
            system_id: The cluster's system identifier.

        Returns:
            ``<postgres home>/noust-wal/<system identifier>``: a place the engine's account
            can write and the backups directory (root's alone) is not.

        Raises:
            DatabaseEngineError: When the engine's account has no home to put it in.
        """
        root = self._archive_root
        if root is None:
            try:
                root = Path(pwd.getpwnam(ENGINE_ACCOUNT).pw_dir) / "noust-wal"
            except KeyError as exc:
                raise DatabaseEngineError(
                    f"The account {ENGINE_ACCOUNT!r} does not exist",
                    details="PostgreSQL runs as it and writes the archive; install the engine "
                    "first.",
                ) from exc
        return root / system_id

    def base_directory(self, system_id: str) -> Path:
        """
        Name where a cluster's base backups are kept.

        Args:
            system_id: The cluster's system identifier.

        Returns:
            ``<backups>/databases/pitr/postgresql/<system identifier>``.
        """
        root = self._backup_root or (paths.backup_dir() / "databases" / "pitr")
        return root / ENGINE / system_id

    def folder(self, system_id: str, kind: str) -> str:
        """
        Name the folder of a destination that holds one kind of file of a cluster.

        Args:
            system_id: The cluster's system identifier.
            kind: :data:`BASE_FOLDER` or :data:`WAL_FOLDER`.

        Returns:
            ``databases/postgresql/pitr/<system identifier>/<kind>``.
        """
        return f"databases/{ENGINE}/pitr/{system_id}/{kind}"

    # ==================== Asking the engine ====================

    def _sql(self, sql: str, *, timeout: int = 120) -> str:
        """
        Run SQL on the cluster as its superuser.

        Args:
            sql: The statement, built from this module's constants.
            timeout: Deadline in seconds.

        Returns:
            psql's output.

        Raises:
            DatabaseEngineError: When the engine does not answer or refuses, with its words.
        """
        ok, output = self.manager._execute_sql(sql, timeout=timeout)
        if not ok:
            raise DatabaseEngineError(
                "PostgreSQL refused a statement of the point-in-time recovery setup",
                details=output.strip()[-2000:] or "psql returned no output.",
            )
        return output.strip()

    def _alive(self) -> bool:
        """
        Returns:
            Whether the engine answers a query.
        """
        ok, _output = self.manager._execute_sql("SELECT 1;", timeout=15)
        return ok

    def _wait_ready(self, timeout: int = READY_TIMEOUT) -> None:
        """
        Wait for the engine to answer after a restart.

        Args:
            timeout: Seconds to wait.

        Raises:
            DatabaseEngineError: When it does not, with the end of its journal.
        """
        deadline = self._clock() + timedelta(seconds=timeout)
        while True:
            if self._alive():
                return
            if self._clock() >= deadline:
                raise DatabaseEngineError(
                    "PostgreSQL did not answer after the restart",
                    details=self._journal(),
                )
            self._sleep(POLL_SECONDS)

    def _journal(self) -> str:
        """
        Returns:
            The end of the engine's journal, verbatim, for an error to carry: its unit, and the
            per-cluster units Debian's packages start (``postgresql@16-main``), which are where
            a cluster that did not come up says why.
        """
        result = self.manager._exec(
            [
                "journalctl",
                "-u",
                self.manager.service_unit(),
                "-u",
                "postgresql@*",
                "-n",
                str(JOURNAL_LINES),
                "--no-pager",
            ],
            timeout=60,
        )
        return (result.stdout if result.success else result.stderr).strip()

    @staticmethod
    def _postmaster_running(data: Path) -> bool:
        """
        Say whether the engine's server process is running, by the file it keeps.

        Args:
            data: The data directory.

        Returns:
            Whether ``postmaster.pid`` names a live process.
        """
        try:
            first = (data / "postmaster.pid").read_text(encoding="utf-8").splitlines()[0]
        except (OSError, IndexError):
            return False
        return first.strip().isdigit() and Path(f"/proc/{first.strip()}").exists()

    def _stop_engine(self, data: Path) -> None:
        """
        Stop the engine and wait until its server process is really gone.

        ``systemctl stop`` is not the end of it: on Debian the unit that is stopped only asks
        the cluster's own unit to stop, and a server that is still writing its shutdown
        checkpoint writes it to the data directory *by name*, so a directory renamed or
        replaced meanwhile gets that write, and the one that was set aside never records that
        it was shut down cleanly.

        Args:
            data: The data directory the engine runs on.

        Raises:
            DatabaseBackupError: When the server process is still there after the wait. Nothing
                was changed.
        """
        self.manager.stop()
        deadline = self._clock() + timedelta(seconds=STOP_TIMEOUT_SECONDS)
        while self._postmaster_running(data):
            if self._clock() >= deadline:
                raise DatabaseBackupError(
                    "PostgreSQL did not stop",
                    details=f"Its server process (see {data / 'postmaster.pid'}) was still "
                    f"running {STOP_TIMEOUT_SECONDS} seconds after the stop. Nothing was changed.",
                )
            self._sleep(POLL_SECONDS / 4)

    def facts(self) -> dict[str, Any]:
        """
        Read what the cluster says about itself that archiving depends on.

        Returns:
            ``data_directory``, ``system_id``, ``version`` (a number such as 160004),
            ``tablespaces`` (those beyond the two every cluster has), ``wal_level``,
            ``archive_mode`` and ``archive_command``.

        Raises:
            DatabaseEngineError: When the engine is not installed, does not run or answers
                something unexpected.
        """
        if not self.manager.is_installed():
            raise DatabaseEngineError(
                "PostgreSQL is not installed on this server",
                details="Install it first: noust db install postgresql",
            )
        if self.manager.instance is not None:
            raise DatabaseEngineError(
                "Point-in-time recovery is for the PostgreSQL of this server, not an instance",
                details="An instance keeps its data in a Docker volume; recovery to an instant "
                "is not offered for it yet. Dump it on a schedule (noust db backup-schedule).",
            )
        output = self._sql(
            "SELECT current_setting('data_directory'), "
            "(SELECT system_identifier FROM pg_control_system()), "
            "current_setting('server_version_num'), "
            "(SELECT count(*) FROM pg_tablespace WHERE spcname NOT IN ('pg_default', 'pg_global')), "
            "current_setting('wal_level'), current_setting('archive_mode'), "
            "current_setting('archive_command');"
        )
        fields = output.splitlines()[-1].split("|", 6) if output else []
        if len(fields) != 7 or not fields[1].isdigit() or not fields[2].isdigit():
            raise DatabaseEngineError(
                "PostgreSQL answered something the setup cannot read",
                details=output[-500:] or "No answer.",
            )
        return {
            "data_directory": fields[0],
            "system_id": fields[1],
            "version": int(fields[2]),
            "tablespaces": int(fields[3] or 0),
            "wal_level": fields[4],
            "archive_mode": fields[5],
            "archive_command": fields[6],
        }

    def archiver(self) -> dict[str, Any]:
        """
        Read the engine's own account of its archiver.

        Returns:
            ``archived``, ``failed`` (counts), ``last_archived_wal``, ``last_archived_at``,
            ``last_failed_wal`` and ``last_failed_at`` (None where there is none).
        """
        output = self._sql(
            "SELECT archived_count, failed_count, coalesce(last_archived_wal, ''), "
            "coalesce(to_char(last_archived_time AT TIME ZONE 'UTC', "
            "'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), ''), coalesce(last_failed_wal, ''), "
            "coalesce(to_char(last_failed_time AT TIME ZONE 'UTC', "
            "'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), '') FROM pg_stat_archiver;"
        )
        fields = (output.splitlines()[-1] if output else "").split("|")
        if len(fields) != 6:
            return {
                "archived": 0,
                "failed": 0,
                "last_archived_wal": None,
                "last_archived_at": None,
                "last_failed_wal": None,
                "last_failed_at": None,
            }
        return {
            "archived": int(fields[0] or 0),
            "failed": int(fields[1] or 0),
            "last_archived_wal": fields[2] or None,
            "last_archived_at": fields[3] or None,
            "last_failed_wal": fields[4] or None,
            "last_failed_at": fields[5] or None,
        }

    def _flush_log(self) -> str | None:
        """
        Make the engine finish the segment it is writing, so it is archived now.

        A statement that writes nothing leaves the segment open, so a restore point (a few
        bytes of log) is written first.

        Returns:
            The last segment archived before the flush, to compare with.
        """
        before = self.archiver()["last_archived_wal"]
        self._sql("SELECT pg_create_restore_point('noust-pitr'); SELECT pg_switch_wal();")
        return str(before) if before else None

    def _wait_archived(
        self, before: str | None, timeout: int = ARCHIVE_PROOF_TIMEOUT
    ) -> dict[str, Any]:
        """
        Wait for the archiver to archive a segment newer than one seen before.

        Args:
            before: The last segment archived when the wait began.
            timeout: Seconds to wait.

        Returns:
            The archiver's account when a newer segment landed.

        Raises:
            DatabaseEngineError: When the archiver fails or nothing arrives, with its words.
        """
        deadline = self._clock() + timedelta(seconds=timeout)
        failed_before = self.archiver()["failed"]
        while True:
            state = self.archiver()
            if state["last_archived_wal"] and state["last_archived_wal"] != before:
                return state
            if state["failed"] > failed_before or (
                state["last_failed_wal"] and state["last_failed_wal"] != before
            ):
                raise DatabaseEngineError(
                    "The engine's archive command failed",
                    details=(
                        f"It could not archive {state['last_failed_wal']} "
                        f"(last failure {state['last_failed_at']}). Look at the end of the "
                        f"journal:\n{self._journal()}"
                    ),
                )
            if self._clock() >= deadline:
                raise DatabaseEngineError(
                    "No segment of the log reached the archive",
                    details=f"The archiver reported nothing new in {timeout} seconds.\n"
                    f"{self._journal()}",
                )
            self._sleep(POLL_SECONDS)

    # ==================== The archive and the base backups on disk ====================

    def _prepare_archive(self, system_id: str) -> Path:
        """
        Create the directory the engine archives into, owned by its account.

        Args:
            system_id: The cluster's system identifier.

        Returns:
            The directory.

        Raises:
            DatabaseEngineError: When it cannot be made or handed over.
        """
        directory = self.archive_directory(system_id)
        try:
            self.fs.make_dir(directory.parent, mode=0o700)
            self.fs.set_owner(
                directory.parent, user=ENGINE_ACCOUNT, group=ENGINE_ACCOUNT, mode=0o700
            )
            self.fs.make_dir(directory, mode=0o700)
            self.fs.set_owner(directory, user=ENGINE_ACCOUNT, group=ENGINE_ACCOUNT, mode=0o700)
        except (OSError, KeyError) as exc:
            raise DatabaseEngineError(
                f"Could not prepare the archive directory {directory}",
                details=f"{exc}. The engine's account ({ENGINE_ACCOUNT}) must be able to write "
                "there.",
            ) from exc
        return directory

    def archived_files(self, system_id: str) -> list[Path]:
        """
        List the files the archive holds.

        Args:
            system_id: The cluster's system identifier.

        Returns:
            The files, by name; empty when there is no archive yet.
        """
        directory = self.archive_directory(system_id)
        try:
            return sorted(path for path in directory.iterdir() if path.is_file())
        except OSError:
            return []

    def list_bases(self, system_id: str) -> list[BaseBackup]:
        """
        List the base backups a cluster has, oldest first.

        Args:
            system_id: The cluster's system identifier.

        Returns:
            The base backups whose sidecar and archive are both there.
        """
        directory = self.base_directory(system_id)
        found: list[BaseBackup] = []
        try:
            sidecars = sorted(directory.glob("base-*.json"))
        except OSError:
            return []
        for sidecar in sidecars:
            archive = directory / f"{sidecar.stem}.tar.gz"
            if not archive.is_file():
                continue
            try:
                found.append(BaseBackup.from_dict(json.loads(sidecar.read_text(encoding="utf-8"))))
            except (OSError, ValueError) as exc:
                self.manager.logger.warning(f"Ignoring {sidecar}: {exc}")
        return sorted(found, key=lambda base: (base.finished_at, base.id))

    def base_path(self, system_id: str, base: BaseBackup) -> Path:
        """
        Name a base backup's archive.

        Args:
            system_id: The cluster's system identifier.
            base: The base backup.

        Returns:
            Its ``.tar.gz``.
        """
        return self.base_directory(system_id) / f"{base.id}.tar.gz"

    # ==================== Status ====================

    def status(self) -> dict[str, Any]:
        """
        Say how point-in-time recovery stands.

        Returns:
            ``enabled``, the policy, the engine's archiver, the archive (files and bytes), the
            base backups, the window that can be recovered (``earliest`` and ``latest``), the
            upkeep timer and ``warnings``: everything an operator needs to trust it.
        """
        policy = self.records.get(ENGINE)
        view: dict[str, Any] = {
            "engine": ENGINE,
            "enabled": bool(policy and policy.enabled),
            "policy": policy.to_dict() if policy else None,
            "warnings": [],
        }
        if policy is None:
            return view
        warnings: list[str] = view["warnings"]
        system_id = policy.system_id
        bases = self.list_bases(system_id)
        files = self.archived_files(system_id)
        view["bases"] = [base.to_dict() for base in bases]
        view["archive"] = {
            "path": policy.archive_path,
            "files": len(files),
            "bytes": sum(path.stat().st_size for path in files if path.exists()),
        }
        latest = self._latest_archived(files)
        view["window"] = {
            "earliest": bases[0].finished_at.isoformat(timespec="seconds") if bases else None,
            "latest": latest.isoformat(timespec="seconds") if latest else None,
        }
        try:
            view["timer"] = self.scheduler.pitr_timer_state(ENGINE)
        except BackupError as exc:
            view["timer"] = {"installed": False}
            warnings.append(f"The upkeep timer could not be read: {exc}")
        if self.manager.is_running():
            try:
                facts = self.facts()
                view["archiver"] = self.archiver()
                if facts["archive_mode"] not in ("on", "always"):
                    warnings.append(
                        "Archiving is recorded as on but the engine has archive_mode "
                        f"{facts['archive_mode']}: the log is not being archived. "
                        "Run: noust db pitr enable"
                    )
                elif facts["archive_command"] != archive_command(policy.archive_path):
                    warnings.append(
                        "The engine's archive_command is not the one Noust set: the log goes "
                        "wherever that command sends it."
                    )
                state = view["archiver"]
                if state["last_failed_at"] and (
                    not state["last_archived_at"]
                    or state["last_failed_at"] > state["last_archived_at"]
                ):
                    warnings.append(
                        f"The last attempt to archive {state['last_failed_wal']} failed, and the "
                        "engine keeps its log on disk until it succeeds: check the journal."
                    )
            except DatabaseEngineError as exc:
                warnings.append(f"The engine could not be asked: {exc.message}")
        else:
            warnings.append("PostgreSQL is not running: nothing is being archived.")
        if not bases:
            warnings.append(
                "There is no base backup yet: nothing can be recovered. Run: noust db pitr base"
            )
        elif self._clock() - bases[-1].finished_at > timedelta(hours=2 * policy.base_every_hours):
            warnings.append(
                "The newest base backup is more than twice as old as the interval says: "
                "the upkeep timer may not be running."
            )
        if policy.last_status == "failed":
            warnings.append(f"The last upkeep run failed: {policy.last_error}")
        return view

    def _latest_archived(self, files: Sequence[Path]) -> datetime | None:
        """
        Find when the newest segment was archived.

        Args:
            files: The archive's files.

        Returns:
            The instant, or None when no segment is there.
        """
        times = [
            datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            for path in files
            if wal_position(path.name) is not None and path.exists()
        ]
        return max(times) if times else None

    # ==================== Turning it on and off ====================

    def plan_enable(
        self,
        *,
        base_every_hours: int = DEFAULT_BASE_EVERY_HOURS,
        retention_count: int | None = None,
        retention_days: int | None = DEFAULT_KEEP_DAYS,
        destinations: Sequence[dict[str, Any]] = (),
    ) -> dict[str, Any]:
        """
        Work out what turning archiving on would do, touching nothing.

        Args:
            base_every_hours: How often a base backup is taken.
            retention_count: Base backups kept; None for no limit by number.
            retention_days: How far back a recovery must reach; None for no limit by age.
            destinations: ``{"name", "retention_count", "retention_days"}`` per destination.

        Returns:
            ``settings`` (what the engine is given), ``restart`` (whether it restarts),
            ``archive_path`` and the policy that would be stored.

        Raises:
            ValidationError: When a value is out of range or a destination does not exist.
            DatabaseEngineError: When the cluster cannot be archived (a tablespace beyond the
                default two, another archive command, a version below 12).
        """
        self._check_policy(base_every_hours, retention_count, retention_days, destinations)
        facts = self.facts()
        if facts["version"] < 120000:
            raise DatabaseEngineError(
                "Point-in-time recovery needs PostgreSQL 12 or newer",
                details="Recovery configuration moved into the server's own settings in 12; "
                "upgrade the engine first.",
            )
        if facts["tablespaces"]:
            raise DatabaseEngineError(
                "This cluster has tablespaces besides the default two",
                details="A base backup streamed to a single file cannot hold them. Point-in-time "
                "recovery is not offered for a cluster with tablespaces yet.",
            )
        archive = self.archive_directory(facts["system_id"])
        wanted = archive_command(archive)
        current = facts["archive_command"]
        if facts["archive_mode"] in ("on", "always") and current not in ("", "(disabled)", wanted):
            raise DatabaseEngineError(
                "PostgreSQL already archives its log with a command that is not Noust's",
                details=f"archive_command is {current!r}. Noust never replaces an archive "
                "command it did not write: two archives of one log would each be missing "
                "what the other holds. Turn that archiving off first, or recover with the "
                "tool that owns it.",
            )
        settings = {
            "archive_mode": "on",
            "archive_command": wanted,
            "archive_timeout": f"{ARCHIVE_TIMEOUT_SECONDS}s",
        }
        if facts["wal_level"] == "minimal":
            settings["wal_level"] = "replica"
        restart = facts["archive_mode"] not in ("on", "always") or "wal_level" in settings
        return {
            "settings": settings,
            "restart": restart,
            "archive_path": str(archive),
            "system_id": facts["system_id"],
            "data_directory": facts["data_directory"],
            "wal_level_was": facts["wal_level"] if "wal_level" in settings else None,
            "base_every_hours": base_every_hours,
            "retention_count": retention_count,
            "retention_days": retention_days,
            "destinations": list(destinations),
        }

    def _check_policy(
        self,
        base_every_hours: int,
        retention_count: int | None,
        retention_days: int | None,
        destinations: Sequence[dict[str, Any]],
    ) -> None:
        """
        Check the values of a policy.

        Args:
            base_every_hours: How often a base backup is taken.
            retention_count: Base backups kept.
            retention_days: How far back a recovery must reach.
            destinations: Where the archive is sent.

        Raises:
            ValidationError: When a value is out of range or a destination is unknown or
                named twice.
        """
        if not 1 <= base_every_hours <= 24 * 366:
            raise ValidationError(
                "A base backup is taken every 1 to 8784 hours",
                details="Weekly (168) is the default.",
                field="base_every_hours",
            )
        for name, value in (
            ("retention_count", retention_count),
            ("retention_days", retention_days),
        ):
            if value is not None and not 1 <= value <= 100_000:
                raise ValidationError(
                    f"{name} must be between 1 and 100000",
                    field=name,
                    details="Leave it empty for no limit.",
                )
        seen: set[str] = set()
        for entry in destinations:
            name = str(entry.get("name", ""))
            if not name or self.destinations.get(name) is None:
                raise ValidationError(
                    f"Backup destination {name!r} does not exist",
                    details="Create it first with: noust backup destination add",
                    field="destination",
                )
            if name in seen:
                raise ValidationError(f"Destination {name!r} is named twice", field="destination")
            seen.add(name)

    @_exclusive("start of archiving")
    def enable(
        self,
        *,
        confirmed: bool,
        base_every_hours: int = DEFAULT_BASE_EVERY_HOURS,
        retention_count: int | None = None,
        retention_days: int | None = DEFAULT_KEEP_DAYS,
        destinations: Sequence[dict[str, Any]] = (),
    ) -> dict[str, Any]:
        """
        Turn archiving on, prove it works, take the first base backup and start the upkeep.

        Args:
            confirmed: The operator accepted the engine's restart (the guard is here, not in
                the caller that asks).
            base_every_hours: How often a base backup is taken.
            retention_count: Base backups kept; None for no limit by number.
            retention_days: How far back a recovery must reach; None for no limit by age.
            destinations: ``{"name", "retention_count", "retention_days"}`` per destination.

        Returns:
            The status after turning it on.

        Raises:
            ValidationError: When the restart was not confirmed or a value is out of range.
            DatabaseEngineError: When the cluster cannot be archived, the engine does not come
                back, or the archive does not work. The engine's settings are put back first.
        """
        plan = self.plan_enable(
            base_every_hours=base_every_hours,
            retention_count=retention_count,
            retention_days=retention_days,
            destinations=destinations,
        )
        if plan["restart"] and not confirmed:
            raise ValidationError(
                "Turning on archiving restarts PostgreSQL",
                details="archive_mode only changes with a restart: every connection is "
                "dropped for a few seconds. Confirm to go ahead.",
                field="confirmed",
            )
        if is_rehearsal():
            self.manager.logger.info(
                f"Would archive the log of cluster {plan['system_id']} to {plan['archive_path']}"
            )
            return {"engine": ENGINE, "enabled": False, "plan": plan, "warnings": []}
        system_id = plan["system_id"]
        archive = self._prepare_archive(system_id)
        self.fs.make_dir(self.base_directory(system_id), mode=0o700)
        applied: list[str] = []
        try:
            for name, value in plan["settings"].items():
                self._sql(f"ALTER SYSTEM SET {name} = {_sql_literal(value)};")
                applied.append(name)
            if plan["restart"]:
                self.manager.restart()
                self._wait_ready()
            else:
                self._sql("SELECT pg_reload_conf();")
            before = self._flush_log()
            self._wait_archived(before)
        except DatabaseEngineError as exc:
            self._revert(applied, plan["restart"])
            exc.details = (
                f"{exc.details or ''}\nNoust put the engine's settings back "
                f"({', '.join(applied) or 'none were changed'})."
            ).strip()
            raise
        policy = self.records.save(
            PitrPolicy(
                engine=ENGINE,
                system_id=system_id,
                archive_path=str(archive),
                base_every_hours=base_every_hours,
                retention_count=retention_count,
                retention_days=retention_days,
                destinations=[dict(entry) for entry in destinations],
                wal_level_was=plan["wal_level_was"],
            )
        )
        self._audit(
            "db.pitr.enable",
            archive=policy.archive_path,
            every_hours=base_every_hours,
            keep=retention_count,
            keep_days=retention_days,
            destinations=[entry["name"] for entry in destinations],
        )
        self.take_base()
        # After the first base backup, not before: a timer installed with a calendar that has
        # already elapsed fires at once, finds no base backup and takes one of its own.
        self.scheduler.install_pitr_timer(ENGINE)
        self.manager.logger.success(f"Archiving the log of PostgreSQL to {policy.archive_path}")
        return self.status()

    def _revert(self, applied: Sequence[str], restarted: bool) -> None:
        """
        Put the engine's archiving settings back after a failed turn-on.

        Args:
            applied: The settings that were changed.
            restarted: Whether the engine was restarted for them, and so must be again.
        """
        for name in applied:
            try:
                self._sql(f"ALTER SYSTEM RESET {name};")
            except DatabaseEngineError as exc:
                self.manager.logger.warning(f"Could not reset {name}: {exc.message}")
        try:
            if restarted:
                self.manager.restart()
                self._wait_ready()
            else:
                self._sql("SELECT pg_reload_conf();")
        except DatabaseEngineError as exc:
            self.manager.logger.warning(
                f"Could not restart the engine after resetting: {exc.message}"
            )

    @_exclusive("end of archiving")
    def disable(self, *, confirmed: bool, delete_archive: bool = False) -> dict[str, Any]:
        """
        Turn archiving off. The archive and the base backups stay unless asked to go.

        Args:
            confirmed: The operator accepted the engine's restart.
            delete_archive: Also delete the archive, the base backups and what the
                destinations hold of them.

        Returns:
            What was done: ``disabled``, ``deleted`` (files removed).

        Raises:
            ValidationError: When the restart was not confirmed.
            DatabaseEngineError: When a setting cannot be reset or the engine does not come
                back.
        """
        policy = self.records.get(ENGINE)
        if policy is None:
            return {"disabled": False, "deleted": 0, "note": "Archiving was not on."}
        if not confirmed:
            raise ValidationError(
                "Turning off archiving restarts PostgreSQL",
                details="archive_mode only changes with a restart. Confirm to go ahead.",
                field="confirmed",
            )
        if is_rehearsal():
            return {"disabled": False, "deleted": 0, "note": "Rehearsal: nothing was changed."}
        names = list(_SETTINGS_WE_SET)
        if policy.wal_level_was:
            names.append("wal_level")
        if self.manager.is_running():
            for name in names:
                self._sql(f"ALTER SYSTEM RESET {name};")
            self.manager.restart()
            self._wait_ready()
        self.scheduler.remove_pitr_timer(ENGINE)
        deleted = 0
        if delete_archive:
            deleted = self._delete_everything(policy)
        self.records.delete(ENGINE)
        self._audit("db.pitr.disable", deleted_archive=delete_archive, files_deleted=deleted)
        return {"disabled": True, "deleted": deleted}

    def _delete_everything(self, policy: PitrPolicy) -> int:
        """
        Delete a cluster's archive, base backups and remote copies.

        Args:
            policy: The policy being removed.

        Returns:
            How many files were removed locally.
        """
        removed = 0
        for path in self.archived_files(policy.system_id):
            self.fs.remove(path)
            removed += 1
        for base in self.list_bases(policy.system_id):
            directory = self.base_directory(policy.system_id)
            for suffix in (".tar.gz", ".json"):
                self.fs.remove(directory / f"{base.id}{suffix}")
                removed += 1
        for entry in policy.destinations:
            name = str(entry["name"])
            for kind in (BASE_FOLDER, WAL_FOLDER):
                folder = self.folder(policy.system_id, kind)
                try:
                    self.destinations.delete_names(
                        name, folder, self.destinations.list_names(name, folder)
                    )
                except BackupError as exc:
                    self.manager.logger.warning(
                        f"Could not clear {folder} on {name}: {exc.message}"
                    )
        return removed

    # ==================== Base backups ====================

    @_exclusive("base backup")
    def take_base(self) -> BaseBackup:
        """
        Take a base backup now and apply retention.

        The cluster is streamed through ``pg_basebackup`` as the engine's account, as one
        compressed tar, with no log in it (the archive holds the log: the backup does not end
        until the segments it needs are archived). Its record is read from the ``.backup`` file
        the engine archives, so the first segment a recovery needs is the engine's word and not
        a guess.

        Returns:
            The base backup.

        Raises:
            DatabaseBackupError: When archiving is not on, ``pg_basebackup`` fails, or the engine
                archived no record of the backup.
        """
        policy = self.records.get(ENGINE)
        if policy is None or not policy.enabled:
            raise DatabaseBackupError(
                "Archiving is not on, so a base backup would be of no use",
                details="Turn it on first: noust db pitr enable",
            )
        started = self._clock().replace(microsecond=0)
        identifier = base_id_at(started)
        label = f"noust-{identifier}"
        directory = self.base_directory(policy.system_id)
        self.fs.make_dir(directory, mode=0o700)
        archive = directory / f"{identifier}.tar.gz"
        partial = directory / f"{identifier}.tar.gz.partial"
        result = self.manager.runner.capture_to_file(
            [
                "pg_basebackup",
                "-D",
                "-",
                "-Ft",
                "-X",
                "none",
                "-c",
                "fast",
                "--no-password",
                "--label",
                label,
            ],
            partial,
            compress=True,
            user=ENGINE_ACCOUNT,
            timeout=BASE_BACKUP_TIMEOUT,
        )
        if not result.success:
            self.fs.remove(partial)
            raise DatabaseBackupError(
                "pg_basebackup failed",
                details=(result.stderr or result.stdout).strip()[-2000:] or "No output.",
            )
        if is_rehearsal():
            return self._rehearsal_base(policy, started, identifier)
        record = self._wait_for_backup_record(policy, label)
        self.fs.rename(partial, archive)
        # Sealed before it is measured, when backups are sealed on this server: the size and
        # the digest recorded, and checked before every recovery, are those of the stored file.
        seal_in_place(archive, fs=self.fs)
        base = BaseBackup(
            id=identifier,
            started_at=record.started_at,
            finished_at=record.stopped_at,
            start_wal_file=record.start_wal_file,
            timeline=record.timeline,
            size=archive.stat().st_size,
            sha256=file_sha256(archive),
            server_version=str(self.facts()["version"]),
            system_id=policy.system_id,
        )
        self.fs.write_text(
            directory / f"{identifier}.json", json.dumps(base.to_dict(), indent=2), mode=0o600
        )
        self.manager.logger.success(f"Base backup {identifier} ({base.size} bytes)")
        self._audit("db.pitr.base", base=identifier, size=base.size, start=base.start_wal_file)
        self.prune()
        return base

    def _rehearsal_base(self, policy: PitrPolicy, started: datetime, identifier: str) -> BaseBackup:
        """
        Describe the base backup a rehearsal would have taken.

        Args:
            policy: The policy.
            started: When it would start.
            identifier: Its name.

        Returns:
            A record with nothing real in it.
        """
        return BaseBackup(
            id=identifier,
            started_at=started,
            finished_at=started,
            start_wal_file="0" * 24,
            timeline=1,
            size=0,
            sha256="0" * 64,
            server_version="",
            system_id=policy.system_id,
        )

    def _wait_for_backup_record(self, policy: PitrPolicy, label: str) -> Any:
        """
        Find the ``.backup`` file the engine archived for a base backup.

        Args:
            policy: The policy.
            label: The label the backup was started with.

        Returns:
            The parsed label.

        Raises:
            DatabaseBackupError: When none says it, even after the engine is asked to archive.
        """
        deadline = self._clock() + timedelta(seconds=ARCHIVE_PROOF_TIMEOUT)
        while True:
            for path in sorted(Path(policy.archive_path).glob("*.backup")):
                try:
                    parsed = parse_backup_label(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if parsed.label == label:
                    return parsed
            if self._clock() >= deadline:
                raise DatabaseBackupError(
                    "The engine archived no record of the base backup",
                    details="pg_basebackup finished but the .backup file for its label did not "
                    f"reach {policy.archive_path}. The backup was discarded.",
                )
            self._sleep(POLL_SECONDS)

    def prune(self) -> dict[str, Any]:
        """
        Apply retention to the base backups and the log kept here.

        Returns:
            ``bases`` and ``segments``: the names deleted.
        """
        policy = self.records.get(ENGINE)
        if policy is None:
            return {"bases": [], "segments": []}
        bases = self.list_bases(policy.system_id)
        dropped = bases_to_drop(
            bases, now=self._clock(), count=policy.retention_count, days=policy.retention_days
        )
        directory = self.base_directory(policy.system_id)
        for base in dropped:
            for suffix in (".tar.gz", ".json"):
                self.fs.remove(directory / f"{base.id}{suffix}")
        kept = [base for base in bases if base not in dropped]
        names = [path.name for path in self.archived_files(policy.system_id)]
        stale = prunable_wal(names, kept)
        for name in stale:
            self.fs.remove(Path(policy.archive_path) / name)
        return {"bases": [base.id for base in dropped], "segments": stale}

    # ==================== Sending the archive away ====================

    @_exclusive("sending of the archive")
    def sync(self) -> dict[str, Any]:
        """
        Send the base backups and the log to every destination of the policy, and prune there.

        Returns:
            Per destination: what was uploaded and deleted.

        Raises:
            BackupError: When a destination fails; the others were still sent to.
        """
        policy = self.records.get(ENGINE)
        if policy is None or not policy.destinations:
            return {}
        report: dict[str, Any] = {}
        failures: list[str] = []
        bases = self.list_bases(policy.system_id)
        files = self.archived_files(policy.system_id)
        for entry in policy.destinations:
            name = str(entry["name"])
            try:
                report[name] = self._sync_to(policy, name, entry, bases, files)
            except BackupError as exc:
                failures.append(f"{name}: {exc.message} {exc.details or ''}".strip())
        if failures:
            raise BackupError(
                "The archive could not be sent everywhere", details="\n".join(failures)
            )
        return report

    def _sync_to(
        self,
        policy: PitrPolicy,
        name: str,
        entry: dict[str, Any],
        bases: Sequence[BaseBackup],
        files: Sequence[Path],
    ) -> dict[str, Any]:
        """
        Send one destination what it lacks, then prune it by its own retention.

        Args:
            policy: The policy.
            name: The destination.
            entry: The policy's entry for it.
            bases: The base backups kept here.
            files: The archive's files.

        Returns:
            ``uploaded`` (base backups), ``segments`` (uploaded) and ``deleted``.

        Raises:
            BackupError: When an upload or a deletion fails.
        """
        base_folder = self.folder(policy.system_id, BASE_FOLDER)
        wal_folder = self.folder(policy.system_id, WAL_FOLDER)
        held = {remote.name for remote in self.destinations.list_files(name, base_folder)}
        uploaded_bases: list[str] = []
        directory = self.base_directory(policy.system_id)
        for base in bases:
            archive = directory / f"{base.id}.tar.gz"
            if archive.name in held:
                continue
            self.destinations.push_file(
                archive,
                name,
                base_folder,
                sidecar={**base.to_dict(), "scheduled": True},
            )
            uploaded_bases.append(base.id)
        shipped = self.destinations.push_batch(list(files), name, wal_folder)
        remote_bases = self._remote_bases(name, base_folder)
        drop = bases_to_drop(
            remote_bases,
            now=self._clock(),
            count=entry.get("retention_count") or policy.retention_count,
            days=entry.get("retention_days") or policy.retention_days,
        )
        deleted: list[str] = []
        if drop:
            self.destinations.delete_names(
                name, base_folder, [f"{base.id}.tar.gz" for base in drop]
            )
            deleted += [base.id for base in drop]
        kept = [base for base in remote_bases if base not in drop]
        stale = prunable_wal(self.destinations.list_names(name, wal_folder), kept)
        if stale:
            self.destinations.delete_names(name, wal_folder, stale)
            deleted += stale
        return {
            "uploaded": uploaded_bases,
            "segments": shipped["uploaded"],
            "deleted": deleted,
        }

    @_exclusive("fetching of the archive")
    def fetch(self, destination: str) -> dict[str, Any]:
        """
        Bring the base backups and the log back from a destination.

        For a server whose archive is gone (a new disk, a server rebuilt from a backup of
        Noust's own state, which holds the policy): what is already here whole is left alone,
        every base backup is checked against the digest it was sent with, and the log is put
        where the engine's account can read it. A recovery (:meth:`restore`) can follow.

        Args:
            destination: The destination, one of the policy's.

        Returns:
            ``bases`` and ``segments``: the names downloaded now.

        Raises:
            ValidationError: When archiving has no policy, or the destination is not one of its.
            BackupError: When a download fails or does not match.
        """
        policy = self.records.get(ENGINE)
        if policy is None:
            raise ValidationError(
                "There is no archive policy to fetch for",
                details="The policy names the cluster and where its archive lives. Restore "
                "Noust's own state first (noust restore-server), or turn archiving on.",
            )
        if destination not in {str(entry["name"]) for entry in policy.destinations}:
            raise ValidationError(
                f"{destination!r} is not a destination of the archive",
                details="Destinations: "
                + (", ".join(str(entry["name"]) for entry in policy.destinations) or "none"),
                field="destination",
            )
        archive = self._prepare_archive(policy.system_id)
        directory = self.base_directory(policy.system_id)
        self.fs.make_dir(directory, mode=0o700)
        base_folder = self.folder(policy.system_id, BASE_FOLDER)
        wal_folder = self.folder(policy.system_id, WAL_FOLDER)
        local = {base.id for base in self.list_bases(policy.system_id)}
        got_bases: list[str] = []
        for base in self._remote_bases(destination, base_folder):
            if base.id in local:
                continue
            name = f"{base.id}.tar.gz"
            self.destinations.download_file(destination, base_folder, name, directory, fs=self.fs)
            self.fs.chmod(directory / name, 0o600)
            self.fs.write_text(
                directory / f"{base.id}.json", json.dumps(base.to_dict(), indent=2), mode=0o600
            )
            got_bases.append(base.id)
        names = self.destinations.list_names(destination, wal_folder)
        got_segments = self.destinations.fetch_batch(
            destination, wal_folder, names, archive, fs=self.fs
        )
        for name in got_segments:
            self.fs.set_owner(archive / name, user=ENGINE_ACCOUNT, group=ENGINE_ACCOUNT, mode=0o600)
        self.manager.logger.success(
            f"Fetched {len(got_bases)} base backups and {len(got_segments)} files of the log"
        )
        return {"bases": got_bases, "segments": got_segments}

    def _remote_bases(self, destination: str, folder: str) -> list[BaseBackup]:
        """
        Read the base backups a destination holds from their sidecars.

        Args:
            destination: The destination.
            folder: Its folder of base backups.

        Returns:
            The base backups, oldest first; one whose sidecar is missing or unreadable is not
            listed (and so never counted when deciding what the log is still needed for).
        """
        found: list[BaseBackup] = []
        for remote in self.destinations.list_files(destination, folder):
            if remote.sidecar is None:
                continue
            try:
                found.append(BaseBackup.from_dict(remote.sidecar))
            except ValueError:
                continue
        return sorted(found, key=lambda base: (base.finished_at, base.id))

    # ==================== Upkeep ====================

    @_exclusive("upkeep", skip_when_busy=True)
    def run(self) -> dict[str, Any]:
        """
        What the timer runs: a base backup when one is due, retention, and sending the archive.

        Returns:
            ``base`` (the id taken, or None), ``pruned`` and ``sync``.

        Raises:
            BackupError: When a step failed (every step still ran; the policy records it).
            DatabaseBackupError: As above, for the base backup.
        """
        policy = self.records.get(ENGINE)
        if policy is None or not policy.enabled:
            return {"base": None, "pruned": {}, "sync": {}}
        problems: list[str] = []
        took: str | None = None
        pruned: dict[str, Any] = {}
        sync: dict[str, Any] = {}
        bases = self.list_bases(policy.system_id)
        due = not bases or (self._clock() - bases[-1].finished_at) >= timedelta(
            hours=policy.base_every_hours
        )
        if due and self.manager.is_running():
            try:
                took = self.take_base().id
            except (DatabaseBackupError, DatabaseEngineError) as exc:
                problems.append(f"base backup: {exc.message} {exc.details or ''}".strip())
        try:
            pruned = self.prune()
        except OSError as exc:
            problems.append(f"retention: {exc}")
        try:
            sync = self.sync()
        except BackupError as exc:
            problems.append(f"sending: {exc.message} {exc.details or ''}".strip())
        self.records.record_run(
            ENGINE,
            ok=not problems,
            error="\n".join(problems) or None,
            base=took is not None,
            sync=bool(policy.destinations) and not any(p.startswith("sending") for p in problems),
        )
        if problems:
            raise BackupError(
                "The point-in-time recovery upkeep had problems", details="\n".join(problems)
            )
        return {"base": took, "pruned": pruned, "sync": sync}

    # ==================== Recovery ====================

    def plan_restore(self, target: datetime | None, *, base_id: str | None = None) -> RestorePlan:
        """
        Work out a recovery without touching anything.

        Args:
            target: The instant to stop at; None for the end of the log.
            base_id: A base backup to start from instead of the best one.

        Returns:
            The plan.

        Raises:
            ValidationError: When there is no base backup for the instant, the instant is later
                than the archive reaches, or the base backup named does not exist.
            DatabaseBackupError: When the log is not whole, or a base backup does not match
                its digest.
        """
        policy = self.records.get(ENGINE)
        if policy is None:
            raise ValidationError(
                "Archiving is not on, so there is nothing to recover to an instant from",
                details="Turn it on first: noust db pitr enable",
            )
        facts = self.facts() if self.manager.is_running() else None
        bases = self.list_bases(policy.system_id)
        if base_id is not None:
            chosen = next((base for base in bases if base.id == base_id), None)
            if chosen is None:
                raise ValidationError(
                    f"No base backup {base_id}",
                    details="Available: " + (", ".join(base.id for base in bases) or "none"),
                    field="base",
                )
            if target is not None and target < chosen.finished_at:
                raise ValidationError(
                    "That base backup ends after the instant asked for",
                    details=f"It finished at {chosen.finished_at:%Y-%m-%d %H:%M:%S} UTC.",
                    field="time",
                )
        else:
            chosen = select_base(bases, target)
        if chosen.system_id != policy.system_id:
            raise ValidationError(
                "That base backup belongs to another cluster",
                details=f"It is of cluster {chosen.system_id}; this archive is {policy.system_id}'s.",
                field="base",
            )
        files = self.archived_files(policy.system_id)
        latest = self._latest_archived(files)
        warnings: list[str] = []
        if target is not None and latest is not None and target > latest:
            raise ValidationError(
                "That instant is later than the archive reaches",
                details=f"The newest segment was archived at {latest:%Y-%m-%d %H:%M:%S} UTC. "
                "Ask for an earlier instant, or for the end of the log (--latest).",
                field="time",
            )
        names = [path.name for path in files]
        timeline_segments = [
            name for name in names if (wal_position(name) or (0,))[0] == chosen.timeline
        ]
        last = max(timeline_segments, default=None)
        gaps = missing_segments(names, chosen, last)
        if gaps:
            raise DatabaseBackupError(
                "The log has a gap, so a recovery from this base backup would stop short",
                details=f"Missing from the archive: {', '.join(gaps[:8])}"
                + (" ..." if len(gaps) > 8 else "")
                + ". Choose an earlier or later base backup (--base), or send the missing "
                "segments back from a destination.",
            )
        archive = self.base_path(policy.system_id, chosen)
        if not archive.is_file() or file_sha256(archive) != chosen.sha256:
            raise DatabaseBackupError(
                f"The base backup {chosen.id} does not match its digest",
                details=f"{archive} is missing or changed since it was taken. Nothing was touched.",
            )
        data_directory = facts["data_directory"] if facts else self._known_data_directory(policy)
        stamp = self._clock().strftime("%Y%m%dT%H%M%SZ")
        saved = f"{data_directory}.noust-before-recovery-{stamp}"
        if target is None:
            warnings.append(
                "Recovery to the end of the log: everything archived is replayed, and what "
                "was written after the last archived segment is lost."
            )
        timeline = None
        if target is not None:
            forks = [
                fork
                for path in files
                if (
                    fork := parse_history(
                        path.name,
                        path.read_text(encoding="utf-8", errors="replace"),
                        datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
                    )
                )
                is not None
            ]
            timeline = choose_timeline(chosen.timeline, target, forks)
        return RestorePlan(
            base=chosen,
            timeline=timeline,
            target=target,
            data_directory=data_directory,
            saved_as=saved,
            segments=len(timeline_segments),
            last_archived_at=latest,
            missing=gaps,
            warnings=warnings,
        )

    def _known_data_directory(self, policy: PitrPolicy) -> str:
        """
        Find the data directory when the engine is down and cannot be asked.

        Args:
            policy: The policy.

        Returns:
            The data directory, from the engine's own cluster listing.

        Raises:
            DatabaseEngineError: When it cannot be found.
        """
        result = self.manager._exec(["pg_lsclusters", "--no-header"], timeout=60)
        for line in result.stdout.splitlines() if result.success else []:
            fields = line.split()
            if len(fields) >= 6 and fields[0].isdigit():
                return fields[5]
        raise DatabaseEngineError(
            "The engine is not running and its data directory cannot be found",
            details="Start PostgreSQL first, so Noust can ask it where its data is.",
        )

    @_exclusive("recovery")
    def restore(
        self,
        target: datetime | None,
        *,
        confirmed: bool,
        base_id: str | None = None,
        timeout: int = RECOVERY_TIMEOUT,
    ) -> dict[str, Any]:
        """
        Recover the cluster to an instant, in place.

        Args:
            target: The instant to stop at; None for the end of the log.
            confirmed: The operator accepted that the cluster is replaced (the guard is here,
                not in the caller that asks).
            base_id: A base backup to start from instead of the best one.
            timeout: Seconds the replay may take.

        Returns:
            ``recovered_to``, ``base``, ``saved_as`` (the data directory it replaced, kept) and
            ``seconds``.

        Raises:
            ValidationError: When it was not confirmed, or the plan is refused.
            DatabaseBackupError: When the recovery fails. The attempt is kept aside, the
                previous data directory is put back and started, and the error carries the
                engine's journal.
        """
        plan = self.plan_restore(target, base_id=base_id)
        if not confirmed:
            raise ValidationError(
                "A recovery replaces the whole cluster",
                details=f"{plan.data_directory} is renamed to {plan.saved_as} and the base backup "
                f"{plan.base.id} is replayed over a fresh one. Every database goes back to "
                "that instant, and every connection is dropped. Confirm to go ahead.",
                field="confirmed",
            )
        if is_rehearsal():
            return {"recovered_to": None, "rehearsal": True, "plan": plan.to_dict()}
        policy = self.records.get(ENGINE)
        assert policy is not None  # plan_restore refused otherwise  # noqa: S101
        started = self._clock()
        data = Path(plan.data_directory)
        saved = Path(plan.saved_as)
        if self.manager.is_running():
            try:
                before = self._flush_log()
                self._wait_archived(before, timeout=60)
            except DatabaseEngineError as exc:
                self.manager.logger.warning(
                    f"The last segment could not be archived before the recovery: {exc.message}"
                )
        self._stop_engine(data)
        try:
            self.fs.rename(data, saved)
        except OSError as exc:
            self.manager.start()
            raise DatabaseBackupError(
                "The data directory could not be set aside",
                details=f"{exc}. A directory that is a mount point cannot be renamed; nothing "
                "was changed.",
            ) from exc
        try:
            self._lay_down(plan, policy, data)
            self.manager.start()
            self._wait_recovered(data, timeout)
        except (DatabaseEngineError, DatabaseBackupError, OSError) as exc:
            self._put_back(data, saved)
            details = (
                exc.details
                if isinstance(exc, DatabaseEngineError | DatabaseBackupError) and exc.details
                else str(exc)
            )
            raise DatabaseBackupError(
                "The recovery failed, and the previous data is back",
                details=f"{details}\nThe failed attempt is kept at {data}.noust-failed-recovery "
                f"and the engine runs on what it held before.",
            ) from exc
        for name in (
            "restore_command",
            "recovery_target_time",
            "recovery_target_action",
            "recovery_target_timeline",
        ):
            self._sql(f"ALTER SYSTEM RESET {name};")
        self._sql("SELECT pg_reload_conf();")
        seconds = int((self._clock() - started).total_seconds())
        self._audit(
            "db.pitr.restore",
            base=plan.base.id,
            target=plan.target.isoformat(timespec="seconds") if plan.target else "latest",
            saved_as=plan.saved_as,
            seconds=seconds,
        )
        self.manager.logger.success(
            f"Recovered to {plan.target or 'the end of the log'} from {plan.base.id}"
        )
        return {
            "recovered_to": plan.target.isoformat(timespec="seconds") if plan.target else None,
            "base": plan.base.id,
            "saved_as": plan.saved_as,
            "seconds": seconds,
        }

    def _lay_down(self, plan: RestorePlan, policy: PitrPolicy, data: Path) -> None:
        """
        Make a fresh data directory from the base backup and set the engine to recover.

        Args:
            plan: The plan.
            policy: The policy.
            data: The data directory to create.

        Raises:
            DatabaseBackupError: When the base backup cannot be extracted.
            OSError: When a file cannot be written.
        """
        self.fs.make_dir(data, mode=0o700)
        self.fs.set_owner(data, user=ENGINE_ACCOUNT, group=ENGINE_ACCOUNT, mode=0o700)
        with open_backup(self.base_path(policy.system_id, plan.base)) as readable:
            result = self.manager._exec(
                ["tar", "-xzf", "-", "-C", str(data)],
                stdin_path=readable,
                timeout=BASE_BACKUP_TIMEOUT,
                user=ENGINE_ACCOUNT,
            )
        if not result.success:
            raise DatabaseBackupError(
                "The base backup could not be extracted",
                details=(result.stderr or result.stdout).strip()[-2000:]
                or "tar returned no output.",
            )
        wal = data / "pg_wal"
        if not wal.is_dir():
            self.fs.make_dir(wal, mode=0o700)
            self.fs.set_owner(wal, user=ENGINE_ACCOUNT, group=ENGINE_ACCOUNT, mode=0o700)
        lines = [
            "",
            "# Added by Noust for a point-in-time recovery.",
            f"restore_command = {_sql_literal(restore_command(policy.archive_path))}",
        ]
        if plan.target is not None:
            lines += [
                f"recovery_target_time = {_sql_literal(format_recovery_time(plan.target))}",
                "recovery_target_action = 'promote'",
            ]
            if plan.timeline is not None:
                lines.append(f"recovery_target_timeline = '{plan.timeline}'")
        auto = data / "postgresql.auto.conf"
        existing = auto.read_text(encoding="utf-8") if auto.is_file() else ""
        self.fs.write_text(auto, existing.rstrip("\n") + "\n" + "\n".join(lines) + "\n", mode=0o600)
        self.fs.set_owner(auto, user=ENGINE_ACCOUNT, group=ENGINE_ACCOUNT, mode=0o600)
        signal = data / "recovery.signal"
        self.fs.write_text(signal, "", mode=0o600)
        self.fs.set_owner(signal, user=ENGINE_ACCOUNT, group=ENGINE_ACCOUNT, mode=0o600)

    def _wait_recovered(self, data: Path, timeout: int) -> None:
        """
        Wait for the engine to finish replaying the log and accept writes.

        Args:
            data: The data directory the engine was started on.
            timeout: Seconds to wait.

        Raises:
            DatabaseBackupError: When the engine's process is gone or the time runs out, with
                its journal.
        """
        began = self._clock()
        deadline = began + timedelta(seconds=timeout)
        grace = began + timedelta(seconds=STARTUP_GRACE_SECONDS)
        while True:
            ok, output = self.manager._execute_sql("SELECT pg_is_in_recovery();", timeout=15)
            if ok and output.strip() == "f":
                return
            if not self._postmaster_running(data) and self._clock() >= grace:
                raise DatabaseBackupError(
                    "PostgreSQL stopped during the recovery", details=self._journal()
                )
            if self._clock() >= deadline:
                raise DatabaseBackupError(
                    f"The recovery did not finish in {timeout} seconds", details=self._journal()
                )
            self._sleep(POLL_SECONDS)

    def _put_back(self, data: Path, saved: Path) -> None:
        """
        Undo a failed recovery: keep the attempt, restore the previous directory, start it.

        Args:
            data: The data directory the attempt used.
            saved: Where the previous one was set aside.
        """
        failed = Path(f"{data}.noust-failed-recovery")
        try:
            self._stop_engine(data)
        except (DatabaseEngineError, DatabaseBackupError) as exc:
            self.manager.logger.warning(
                f"Could not stop the engine after the failure: {exc.message}"
            )
        try:
            if failed.exists():
                # An earlier failed attempt is replaced by this one: only the last is kept.
                self.fs.remove_tree(failed)
            self.fs.rename(data, failed)
            self.fs.rename(saved, data)
            self.manager.start()
        except (OSError, DatabaseEngineError) as exc:
            self.manager.logger.error(
                f"The previous data directory could not be put back: {exc}. It is at {saved}."
            )
