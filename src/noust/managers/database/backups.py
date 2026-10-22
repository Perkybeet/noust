# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Database backups: policies, verification, offsite copies and safe restores.

:class:`~noust.managers.database.service.DatabaseService` takes a dump and
restores one. What makes a dump a *backup* is here, once, for the CLI, the
console's jobs and the timer that ``noust db backup-schedule set`` installs:

- a **policy** per database (schedule, retention, destinations, format),
  whether or not an application uses the database. The timer is the same
  systemd timer an application's schedule uses
  (:class:`~noust.managers.backup_scheduler.BackupScheduler`) and the
  destinations are the same rclone destinations
  (:class:`~noust.managers.backup_destination_files.DestinationFileManager`),
  encrypted or not as they were created;
- **verification** after every dump: size, SHA-256, and the engine's own
  reading of the file (:mod:`~noust.managers.database.backup_verify`), and
  optionally a restore into a temporary database that is dropped afterwards.
  The evidence is kept with the dump, in the tool's own words;
- **retention** that only ever deletes what a schedule made, by count and by
  age, locally and on each destination separately;
- **restore** as a new database or over the existing one, from a local dump or
  a remote one (downloaded, checked against its digest and verified before
  anything is touched), always through the service's safety copy. Safety
  copies are kept per database up to ``databases.safety_copies_kept`` (the
  newest always), so restoring every day does not fill the disk.

The order inside a policy run is what keeps it safe: nothing is sent and
nothing is deleted until the new dump has been verified, so a dump that does
not restore can neither replace a good remote copy nor push an old one out.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noust.core.exceptions import (
    BackupError,
    ConfirmationRequired,
    DatabaseBackupError,
    DatabaseError,
    DatabaseNotFoundError,
    NoustError,
    ValidationError,
)
from noust.core.fs import SECRET_DIR_MODE, SECRET_MODE, FileSystem, get_fs
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import Notification
from noust.core.store import NoustStore
from noust.managers.backup_destination_files import DestinationFileManager, file_sha256
from noust.managers.backup_failures import record_failure
from noust.managers.backup_scheduler import BackupScheduler, validate_calendar
from noust.managers.backup_timer_options import validate_options
from noust.managers.database.backup_notifications import (
    compose_database_backup_completed,
    compose_database_backup_failed,
    compose_database_backup_upload_failed,
    compose_database_restore,
    database_subject,
    deliver,
)
from noust.managers.database.backup_records import (
    STATUS_FAILED,
    STATUS_OK,
    BackupPolicy,
    BackupRecords,
    DumpRecord,
    bound_evidence,
    now,
)
from noust.managers.database.backup_verify import check_dump, restore_test
from noust.managers.database.base import (
    OWN_DUMP_OPTIONS,
    BackupInfo,
    BaseDatabaseManager,
    RestoreOutcome,
    backup_format,
    format_size,
)
from noust.managers.database.instances import engine_of
from noust.managers.database.scope import (
    ALL_DATABASES,
    SCOPE_ALL,
    SCOPE_DATABASE,
    Exclusions,
)
from noust.managers.database.service import DatabaseService
from noust.managers.retention import (
    check_gigabytes,
    gigabytes_to_bytes,
    select_expired,
    with_gigabytes,
    without_newest_good,
)

__all__ = ["DatabaseBackups", "DumpView", "PolicyView"]

#: Folder a destination keeps database dumps under, then ``<engine>/<database>``.
REMOTE_ROOT = "databases"

#: The folder a Redis instance's snapshots share: a snapshot covers every slot,
#: so no slot names it.
REDIS_FOLDER = "instance"

#: Safety copies kept per database when the setting says nothing usable.
DEFAULT_SAFETY_COPIES_KEPT = 5

#: Where a remote restore downloads to, under the engine's backup directory:
#: the dumps' own filesystem, sized for them, hidden from every listing.
_STAGING_DIR = ".remote-staging"

#: Limits a policy's retention accepts, the same as an application's schedule.
MAX_RETENTION_COUNT = 365
MAX_RETENTION_DAYS = 3650

#: What an operator accepts by loading a dump as the server's superuser.
SUPERUSER_WARNING = (
    "The dump will run with the rights of PostgreSQL's superuser. A dump can run programs on "
    "this server (COPY ... FROM PROGRAM), read its files and change any database; only load one "
    "you made or have read."
)

#: Dump formats a PostgreSQL policy may ask for.
_POLICY_FORMATS = ("custom", "plain", "tar")


def remote_folder(engine: str, database: str) -> str:
    """
    Name the folder a database's dumps live in on a destination.

    Args:
        engine: Canonical engine name.
        database: The database.

    Returns:
        ``databases/<engine>/<database>``; a Redis instance has one folder.
    """
    folder = REDIS_FOLDER if engine_of(engine) == "redis" else database
    return f"{REMOTE_ROOT}/{engine}/{folder}"


class DumpFailedItsCheck(DatabaseBackupError):
    """
    A dump was taken and kept, and it did not pass its check (or its restore test).

    Its own class because it changes what a failed run does afterwards: the corrupt dump is the
    newest one and is kept as evidence, so nothing is pruned on its account - a good copy must not
    be pushed out to make room for a bad one.
    """


@dataclass
class DumpView:
    """
    A dump on disk with what Noust knows about it.

    Attributes:
        info: The file, as the engine's manager lists it.
        record: What the store recorded, or None for a dump Noust did not take.
    """

    info: BackupInfo
    record: DumpRecord | None = None

    @property
    def name(self) -> str:
        """The file name."""
        return self.info.path.name

    def to_dict(self) -> dict[str, Any]:
        """
        Render the dump as plain data.

        Returns:
            The manager's own description of the file plus ``kind`` (who made
            it: ``manual``, ``scheduled``, ``safety`` or ``unknown``),
            ``sha256``, the verification (``verify_status``: ``ok``,
            ``failed`` or ``unverified``), the restore test, ``destinations``
            (where a copy was sent) and ``age_seconds``.
        """
        record = self.record
        data = self.info.to_dict()
        data.update(
            {
                "kind": record.origin if record else "unknown",
                "sha256": record.sha256 if record else None,
                "verified_at": record.verified_at if record else None,
                "verify_status": (record.verify_status if record else None) or "unverified",
                "verify_method": record.verify_method if record else None,
                "verify_detail": record.verify_detail if record else None,
                "restore_tested_at": record.restore_tested_at if record else None,
                "restore_test_status": record.restore_test_status if record else None,
                "restore_test_detail": record.restore_test_detail if record else None,
                "destinations": list(record.remote_copies) if record else [],
                "age_seconds": max(0, int((datetime.now() - self.info.created).total_seconds())),
            }
        )
        return data


@dataclass
class PolicyView:
    """
    A database's backup policy with the state of its timer.

    Attributes:
        engine: Canonical engine name.
        database: The database.
        policy: The stored policy, or None when the database has none.
        timer: What systemd says of the timer: ``installed``, ``next_run``,
            ``last_run``.
        destinations: What each destination the policy names is now:
            ``{"exists", "encrypted"}``.
    """

    engine: str
    database: str
    policy: BackupPolicy | None = None
    timer: dict[str, Any] = field(default_factory=dict)
    destinations: dict[str, dict[str, bool]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Render the policy as plain data.

        Returns:
            ``configured`` and, when it is, every field of the policy, plus
            ``timer``. Each destination also says whether it still ``exists``
            and whether it is ``encrypted``.
        """
        data: dict[str, Any] = {
            "engine": self.engine,
            "database": self.database,
            "configured": self.policy is not None,
            "timer": dict(self.timer),
        }
        if self.policy is not None:
            stored = self.policy.to_dict()
            stored.pop("db_name", None)
            stored.pop("id", None)
            with_gigabytes(stored)
            stored["destinations"] = [
                {
                    **entry,
                    **self.destinations.get(entry["name"], {"exists": False, "encrypted": False}),
                }
                for entry in stored["destinations"]
            ]
            data.update(stored)
        return data


#: The directory, beside the store, holding one entry per restore in progress.
_JOURNAL_DIR = "restores-in-flight"

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class InterruptedRestore:
    """
    A restore whose process stopped before it said how it ended.

    Attributes:
        engine: Canonical engine name.
        target: The database restored into.
        source: The dump's file name.
        safety_copy: The copy of what the database held, when one was taken.
        message: What happened and the exact command that puts it back.
    """

    engine: str
    target: str
    source: str
    safety_copy: str | None
    message: str


class _RestoreJournal:
    """
    One file per restore in progress, written before anything is touched.

    A restore runs inside the console; a package upgrade restarting it
    mid-restore used to leave a dropped database and a safety copy nothing
    recorded. The entry names the safety copy as soon as it exists, and
    :func:`reconcile_interrupted_restores` reads what is left at the next
    start.
    """

    def __init__(self, store: NoustStore, fs: FileSystem) -> None:
        """
        Args:
            store: The store; the journal lives beside it.
            fs: The filesystem the entries are written through.
        """
        self.directory = store.db_path.parent / _JOURNAL_DIR
        self.fs = fs

    def begin(self, engine: str, target: str, source: Path, *, replace: bool) -> Path:
        """
        Record that a restore starts.

        Args:
            engine: Canonical engine name.
            target: The database restored into.
            source: The dump.
            replace: Whether the database is dropped first.

        Returns:
            The entry, for :meth:`note_safety_copy` and :meth:`end`.
        """
        self.fs.make_dir(self.directory, mode=SECRET_DIR_MODE)
        entry = self.directory / f"{engine}-{target}-{secrets.token_hex(4)}.json"
        self._write(
            entry,
            {
                "engine": engine,
                "target": target,
                "source": str(source),
                "replace": replace,
                "started_at": now(),
                "safety_copy": None,
            },
        )
        return entry

    def note_safety_copy(self, entry: Path, copy: Path) -> None:
        """
        Name the safety copy in an entry, before anything is dropped.

        Args:
            entry: The entry :meth:`begin` returned.
            copy: The safety copy.
        """
        data = _read_entry(entry) or {}
        self._write(entry, {**data, "safety_copy": str(copy)})

    def end(self, entry: Path) -> None:
        """
        Forget a restore that ended, well or badly, and said so.

        Args:
            entry: The entry.
        """
        self.fs.remove(entry, missing_ok=True)

    def _write(self, entry: Path, data: dict[str, Any]) -> None:
        self.fs.write_text(entry, json.dumps(data, sort_keys=True), mode=SECRET_MODE)


def _read_entry(entry: Path) -> dict[str, Any] | None:
    """
    Args:
        entry: A journal entry.

    Returns:
        Its content, or None when it cannot be read as one.
    """
    try:
        data = json.loads(entry.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _restore_interrupted(
    engine: str, target: str, source: str, message: str
) -> Callable[[NotificationContext], Notification]:
    """
    Args:
        engine: Canonical engine name.
        target: The database restored into.
        source: The dump's file name.
        message: What happened and how to recover.

    Returns:
        What composes the failed-restore notification for it.
    """

    def build(ctx: NotificationContext) -> Notification:
        return compose_database_restore(False, engine, target, source, message, ctx)

    return build


def reconcile_interrupted_restores(
    store: NoustStore, *, fs: FileSystem | None = None
) -> list[InterruptedRestore]:
    """
    Report the restores a stopped console left unfinished, once each.

    Called when the console starts. Each one is logged, announced as a failed
    restore with the command that puts the safety copy back, and removed
    from the journal; the caller puts the same words on the interrupted job.

    Args:
        store: The store the journal lives beside.
        fs: The filesystem; the process-wide one by default.

    Returns:
        What was found.
    """
    from noust.core.notifier import notify_composed

    fs = fs or get_fs()
    directory = store.db_path.parent / _JOURNAL_DIR
    if not directory.is_dir():
        return []
    found: list[InterruptedRestore] = []
    for entry in sorted(directory.glob("*.json")):
        data = _read_entry(entry) or {}
        engine = str(data.get("engine") or "unknown")
        target = str(data.get("target") or entry.stem)
        source = Path(str(data.get("source") or "")).name
        safety = data.get("safety_copy")
        said = (
            f"The restore of {engine}/{target} from {source} was interrupted: the console "
            "stopped before it finished."
        )
        if safety:
            message = (
                f"{said} What {target} held before is in the safety copy {safety}. Put it "
                f"back with: noust db restore {target} {safety} --engine {engine} --drop"
            )
        else:
            message = (
                f"{said} No safety copy had been taken, so nothing had been dropped; "
                f"{target} may hold part of the dump. Check it before relying on it."
            )
        _log.warning(message)
        found.append(
            InterruptedRestore(
                engine=engine,
                target=target,
                source=source,
                safety_copy=str(safety) if safety else None,
                message=message,
            )
        )
        notify_composed(_restore_interrupted(engine, target, source, message))
        fs.remove(entry, missing_ok=True)
    return found


class DatabaseBackups:
    """
    Every backup operation on a database, for every front end.

    Construct one per request or command; it holds no state beyond its
    collaborators.
    """

    def __init__(
        self,
        service: DatabaseService,
        *,
        scheduler: BackupScheduler | None = None,
        destinations: DestinationFileManager | None = None,
        fs: FileSystem | None = None,
    ) -> None:
        """
        Args:
            service: The database service: engines, the store, the audit
                trail and the logger all come through it.
            scheduler: Installs the timers. A default one when None.
            destinations: Sends and fetches files. A default one when None.
            fs: The filesystem deletions go through, so ``--dry-run`` reaches
                them. The process-wide one when None.
        """
        self.service = service
        self._scheduler = scheduler
        self._destinations = destinations
        self._fs = fs

    # ------------------------------------------------------------ plumbing

    @property
    def records(self) -> BackupRecords:
        """The policies and dumps tables."""
        return BackupRecords(self.service.store)

    @property
    def scheduler(self) -> BackupScheduler:
        """The scheduler that owns the timer units."""
        if self._scheduler is None:
            self._scheduler = BackupScheduler(verbose=self.service.logger.verbose)
            # One voice: what the scheduler reports goes where the service's does,
            # a job's log included.
            self._scheduler.logger = self.service.logger
        return self._scheduler

    @property
    def destinations(self) -> DestinationFileManager:
        """The destinations, with the file verbs dumps need."""
        if self._destinations is None:
            self._destinations = DestinationFileManager()
        return self._destinations

    @property
    def fs(self) -> FileSystem:
        """The filesystem deletions go through."""
        return self._fs or get_fs()

    def _audit(self, event: str, target: str, outcome: str = "ok", **details: Any) -> None:
        """
        Record a change in the audit trail.

        Args:
            event: A catalog name, such as ``db.backup.policy``.
            target: What it happened to, such as ``postgresql/shop``.
            outcome: ``ok``, ``failure`` or ``denied``.
            **details: Anything else worth keeping. Never a credential.
        """
        self.service.audit(event, target, outcome, **details)

    # ------------------------------------------------------------ policies

    def _timer(self, engine: str, database: str) -> dict[str, Any]:
        """
        Ask systemd about a database's timer.

        Args:
            engine: Canonical engine name.
            database: The database.

        Returns:
            The timer's state; empty when its unit cannot be named.
        """
        try:
            return self.scheduler.database_timer_state(engine, database)
        except BackupError as exc:
            self.service.logger.warning(f"Could not read the backup timer of {database}: {exc}")
            return {}

    def _view_of(self, policy: BackupPolicy | None, engine: str, database: str) -> PolicyView:
        """
        Build a policy's view: its timer and what its destinations are now.

        Args:
            policy: The stored policy, or None.
            engine: Canonical engine name.
            database: The database.

        Returns:
            The view.
        """
        timer: dict[str, Any] = {}
        states: dict[str, dict[str, bool]] = {}
        if policy is not None:
            if policy.enabled:
                timer = self._timer(engine, database)
            for entry in policy.destinations:
                record = self.destinations.get(entry["name"])
                states[entry["name"]] = {
                    "exists": record is not None,
                    "encrypted": bool(record and record.encrypted),
                }
        return PolicyView(engine, database, policy, timer, states)

    def get_policy(self, engine: str, database: str) -> PolicyView:
        """
        Read one database's policy and the state of its timer.

        Args:
            engine: The engine.
            database: The database.

        Returns:
            The view; ``policy`` is None when the database has none.
        """
        manager = self.service.manager(engine)
        name = self.policy_name(manager, database)
        return self._view_of(
            self.records.policy(manager.ENGINE_NAME, name), manager.ENGINE_NAME, name
        )

    @staticmethod
    def policy_name(manager: BaseDatabaseManager, database: str) -> str:
        """
        Name what a policy is stored under.

        Args:
            manager: The engine.
            database: A database, or :data:`ALL_DATABASES` for the policy that
                covers every one of them.

        Returns:
            The database's validated name, or :data:`ALL_DATABASES`.

        Raises:
            ValidationError: When the name is not one the engine accepts.
        """
        if database == ALL_DATABASES:
            return ALL_DATABASES
        return manager.validate_database_name(database)

    def list_policies(self, engine: str | None = None) -> list[PolicyView]:
        """
        List every policy with the state of its timer.

        Args:
            engine: Only this engine's.

        Returns:
            The views, by engine then database.
        """
        canonical = self.service.manager(engine).ENGINE_NAME if engine else None
        return [
            self._view_of(policy, policy.engine, policy.db_name)
            for policy in self.records.policies(canonical)
        ]

    def unprotected(self, engine: str | None = None) -> list[dict[str, str]]:
        """
        List the databases no policy covers.

        A Redis snapshot covers every slot, so Redis is one entry: unprotected
        when no policy names any of its slots.

        Args:
            engine: Only this engine's.

        Returns:
            ``{"engine", "database"}`` per database without an enabled policy.
        """
        policies = [p for p in self.records.policies() if p.enabled]
        covered = {(p.engine, p.db_name) for p in policies}
        # A policy for all of an engine's databases covers each one it does not
        # leave out, the ones created after it too.
        everything = {
            p.engine: Exclusions.from_json(p.exclusions).databases
            for p in policies
            if p.scope == SCOPE_ALL
        }
        # One snapshot per Redis instance: the host's, and each container's.
        redis_covered = {key for key, _ in covered if engine_of(key) == "redis"}
        missing: list[dict[str, str]] = []
        redis_listed: set[str] = set()
        for view in self.service.list_databases(engine):
            if view.missing or (view.engine, view.name) in covered:
                continue
            if view.engine in everything and view.name not in everything[view.engine]:
                continue
            if engine_of(view.engine) == "redis":
                if view.engine in redis_covered or view.engine in redis_listed:
                    continue
                redis_listed.add(view.engine)
            missing.append({"engine": view.engine, "database": view.name})
        return missing

    def _require_destination(self, name: str) -> str:
        """
        Check that a destination exists, so a typo is a 400 and not an rclone error.

        Args:
            name: The destination's name.

        Returns:
            The name.

        Raises:
            ValidationError: When there is no such destination.
        """
        if not name or self.destinations.get(name) is None:
            raise ValidationError(
                f"Backup destination {name!r} does not exist",
                details="Create it first with: noust backup destination add",
                field="destination",
            )
        return name

    def _validated_destinations(self, destinations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Check that every destination a policy names exists, and its retention.

        Args:
            destinations: ``{"name", "retention_count", "retention_days",
                "retention_gb"}`` each.

        Returns:
            The same, normalised.

        Raises:
            ValidationError: When a destination does not exist or is named twice,
                or a retention is out of range.
        """
        checked: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in destinations:
            name = str(entry.get("name") or "")
            if not name or self.destinations.get(name) is None:
                raise ValidationError(
                    f"Backup destination {name!r} does not exist",
                    details="Create it first with: noust backup destination add",
                    field="destinations",
                )
            if name in seen:
                raise ValidationError(f"Destination {name!r} is listed twice", field="destinations")
            seen.add(name)
            checked.append(
                {
                    "name": name,
                    "retention_count": _limit(
                        entry.get("retention_count"), MAX_RETENTION_COUNT, "retention_count"
                    ),
                    "retention_days": _limit(
                        entry.get("retention_days"), MAX_RETENTION_DAYS, "retention_days"
                    ),
                    "retention_gb": check_gigabytes(entry.get("retention_gb"), "retention_gb"),
                }
            )
        return checked

    def set_policy(
        self,
        engine: str,
        database: str,
        *,
        schedule: str = "daily",
        retention_count: int | None = 7,
        retention_days: int | None = 30,
        destinations: list[dict[str, Any]] | None = None,
        dump_format: str | None = None,
        verify_restore: bool = False,
        enabled: bool = True,
        exclusions: Exclusions | None = None,
        retention_gb: float | None = None,
        remote_only: bool = False,
        max_runtime: int | str | None = None,
        zone: str | None = None,
    ) -> PolicyView:
        """
        Create or replace a database's backup policy, and its timer.

        The timer is installed first and the row written last, the way an
        application's schedule is: a policy in the store always has the timer
        it describes.

        Args:
            engine: The engine.
            database: The database, which must exist; or
                :data:`~noust.managers.database.scope.ALL_DATABASES` for a
                policy that covers every database of the engine, the ones
                created later included.
            schedule: An alias (``hourly``, ``daily``, ``weekly``,
                ``monthly``) or a systemd calendar expression.
            retention_count: Scheduled dumps to keep locally; None for no limit
                by count.
            retention_days: Days to keep a scheduled dump locally; None for no
                limit by age.
            destinations: Remote copies, ``{"name", "retention_count",
                "retention_days"}`` each.
            dump_format: PostgreSQL's ``custom``, ``plain`` or ``tar``; the
                engine's default when None.
            verify_restore: Load each dump into a temporary database as proof
                it restores. Not available for Redis.
            enabled: Whether the timer runs. A disabled policy keeps its
                settings and has no timer.
            exclusions: What the dumps leave out: databases (all-databases
                policies only), PostgreSQL schemas, tables or collections.
            retention_gb: The most the scheduled dumps kept here may weigh
                together, in gigabytes (the newest always stays); None for no
                limit by size.
            remote_only: Delete each scheduled dump from this server once every
                destination holds it, for a server whose disk is small. Needs a
                destination; a dump that could not be sent everywhere stays.
            max_runtime: The longest a run may take (seconds, or ``90m``/``2h``); systemd stops
                a run past it. None for no limit.
            zone: The time zone the schedule is read in (``Europe/Madrid``); None for the
                server's clock.

        Returns:
            The policy as stored, with its timer's state.

        Raises:
            ValidationError: When a value is unusable.
            BackupError: When the timer cannot be made.
            DatabaseNotFoundError: When the database does not exist.
            DatabaseEngineError: When the engine is not installed and running.
        """
        manager = self.service.running(engine)
        engine_name = manager.ENGINE_NAME
        name = self.policy_name(manager, database)
        scope = SCOPE_ALL if name == ALL_DATABASES else SCOPE_DATABASE
        if scope == SCOPE_ALL and manager.engine_type == "redis":
            raise ValidationError(
                "A Redis policy already covers the whole instance",
                details="A snapshot is every slot at once: give the policy any database of the "
                "instance, such as 0.",
                field="database",
            )
        if scope == SCOPE_DATABASE and not manager.database_exists(name):
            raise DatabaseNotFoundError(
                f"Database '{name}' does not exist",
                details=f"List them with: noust db list --engine {engine_name}",
            )
        left_out = exclusions or Exclusions()
        left_out.check_for(manager.engine_type, scope=scope)
        calendar = validate_calendar(schedule)
        timer_options = validate_options(max_runtime, zone)
        count = _limit(retention_count, MAX_RETENTION_COUNT, "retention_count")
        days = _limit(retention_days, MAX_RETENTION_DAYS, "retention_days")
        budget = check_gigabytes(retention_gb, "retention_gb")
        if dump_format is not None and (
            manager.engine_type != "postgresql" or dump_format not in _POLICY_FORMATS
        ):
            raise ValidationError(
                f"Dump format {dump_format!r} is not available for {manager.DISPLAY_NAME}",
                details="PostgreSQL takes custom, plain or tar; every other engine has one format.",
                field="dump_format",
            )
        if verify_restore and manager.engine_type == "redis":
            raise ValidationError(
                "A Redis snapshot cannot be test-restored",
                details="Restoring one replaces every key of the instance, so it cannot be "
                "tried on the side. Noust checks it with redis-check-rdb instead.",
                field="verify_restore",
            )
        checked = self._validated_destinations(destinations or [])
        if remote_only and not checked:
            raise ValidationError(
                "A policy that keeps nothing on this server needs a destination",
                details="Name at least one destination to send the dumps to (--to NAME), or "
                "keep the dumps here.",
                field="remote_only",
            )

        if enabled:
            self.scheduler.install_database_timer(
                engine_name, name, calendar, options=timer_options
            )
        else:
            self.scheduler.remove_database_timer(engine_name, name)
        self.records.save_policy(
            BackupPolicy(
                engine=engine_name,
                db_name=name,
                schedule=calendar,
                retention_count=count,
                retention_days=days,
                retention_bytes=gigabytes_to_bytes(budget),
                remote_only=remote_only,
                destinations=checked,
                dump_format=dump_format,
                verify_restore=verify_restore,
                scope=scope,
                exclusions=left_out.to_json(),
                enabled=enabled,
            )
        )
        self._audit(
            "db.backup.policy",
            f"{engine_name}/{name}",
            schedule=calendar,
            retention_count=count,
            retention_days=days,
            retention_gb=budget,
            remote_only=remote_only,
            max_runtime_seconds=timer_options.max_runtime_seconds,
            timezone=timer_options.timezone,
            destinations=[entry["name"] for entry in checked],
            verify_restore=verify_restore,
            enabled=enabled,
            scope=scope,
            exclusions=left_out.to_json(),
        )
        return self.get_policy(engine_name, name)

    def remove_policy(self, engine: str, database: str) -> bool:
        """
        Remove a database's policy and its timer. Its dumps stay.

        Args:
            engine: The engine.
            database: The database.

        Returns:
            Whether there was a policy.

        Raises:
            BackupError: When the timer's unit cannot be named.
        """
        manager = self.service.manager(engine)
        name = self.policy_name(manager, database)
        self.scheduler.remove_database_timer(manager.ENGINE_NAME, name)
        removed = self.records.delete_policy(manager.ENGINE_NAME, name)
        if removed:
            self._audit("db.backup.policy.remove", f"{manager.ENGINE_NAME}/{name}")
        return removed

    # ---------------------------------------------------------------- dumps

    def list_dumps(self, engine: str | None = None, database: str | None = None) -> list[DumpView]:
        """
        List the dumps on disk with what Noust knows about each.

        Args:
            engine: Only this engine's.
            database: Only this database's.

        Returns:
            The dumps, newest first within each engine.
        """
        infos = self.service.list_dumps(engine, database)
        canonical = self.service.manager(engine).ENGINE_NAME if engine else None
        known = {(r.engine, r.file_name): r for r in self.records.dumps(canonical)}
        return [DumpView(info, known.get((info.engine, info.path.name))) for info in infos]

    def record_resealed(self, view: DumpView, *, size: int, sha256: str) -> None:
        """
        Record a dump's stored size and digest after the file was sealed in place.

        Without it the next check would say the file changed on disk since the dump was taken. A
        dump nobody recorded (one imported by hand) has nothing to update.

        Args:
            view: The dump, as :meth:`list_dumps` returned it.
            size: The stored file's size now.
            sha256: Its SHA-256 now.
        """
        if view.record is None:
            return
        view.record.size = size
        view.record.sha256 = sha256
        self.records.save_dump(view.record)

    def _view(self, engine: str, file_name: str) -> DumpView:
        """
        Find one dump on disk.

        Args:
            engine: The engine.
            file_name: The dump's file name.

        Returns:
            The dump.

        Raises:
            DatabaseNotFoundError: When there is no such dump.
        """
        path = self.service.dump_path(engine, file_name)
        canonical = self.service.manager(engine).ENGINE_NAME
        for view in self.list_dumps(canonical):
            if view.info.path == path:
                return view
        raise DatabaseNotFoundError(
            f"Backup not found: {file_name}",
            details="List the dumps with: noust db backups",
        )

    def dump(
        self,
        engine: str,
        database: str,
        *,
        origin: str = "manual",
        compress: bool = True,
        dump_format: str | None = None,
        verify: bool = True,
        exclusions: Exclusions | None = None,
    ) -> DumpView:
        """
        Take a dump, record it and check it.

        The dump is kept even when the check fails: it is evidence, and the
        operator decides. The failure carries the check's own words.

        Args:
            engine: The engine.
            database: The database.
            origin: ``manual`` or ``scheduled``; only a scheduled dump is ever
                deleted by retention.
            compress: gzip the dump where the engine does not compress it.
            dump_format: PostgreSQL's dump format.
            verify: Check it after taking it.
            exclusions: Schemas, tables or collections to leave out.

        Returns:
            The dump, with its record.

        Raises:
            DatabaseBackupError: When the dump fails or does not verify.
        """
        manager = self.service.manager(engine)
        info = self.service.dump(
            engine, database, compress=compress, dump_format=dump_format, exclusions=exclusions
        )
        try:
            digest = file_sha256(info.path)
        except OSError as exc:
            raise DatabaseBackupError(
                f"The dump {info.path.name} was reported taken but cannot be read",
                details=f"{exc}. Check the permissions and the space of {info.path.parent}.",
            ) from exc
        record = self.records.save_dump(
            DumpRecord(
                engine=manager.ENGINE_NAME,
                file_name=info.path.name,
                db_name=database,
                origin=origin,
                size=info.size,
                sha256=digest,
            )
        )
        view = DumpView(info, record)
        if verify:
            view = self._check(view)
            if view.record and view.record.verify_status == STATUS_FAILED:
                raise DumpFailedItsCheck(
                    f"The dump {info.path.name} was taken but failed its check",
                    details=(
                        f"{view.record.verify_detail}\n\nThe file is kept at {info.path}. "
                        "Take another dump, and delete this one with: noust db backup-delete"
                    ),
                )
        return view

    def _check(self, view: DumpView) -> DumpView:
        """
        Check a dump's file and record what was found.

        Args:
            view: The dump.

        Returns:
            The dump with its record updated.
        """
        manager = self.service.manager(view.info.engine)
        record = view.record or DumpRecord(
            engine=manager.ENGINE_NAME, file_name=view.name, db_name=view.info.database
        )
        outcome = check_dump(manager, view.info.path)
        try:
            digest = file_sha256(view.info.path) if view.info.path.is_file() else None
        except OSError:
            # check_dump already said so, in its own words, when the file is unreadable.
            digest = None
        detail = outcome.detail
        ok = outcome.ok
        if ok and record.sha256 and digest and digest != record.sha256:
            ok = False
            detail = (
                f"The file's SHA-256 is {digest}; it was {record.sha256} when the dump was "
                "taken. The file changed on disk since."
            )
        if record.sha256 is None:
            record.sha256 = digest
        record.size = view.info.size
        record.verified_at = now()
        record.verify_status = STATUS_OK if ok else STATUS_FAILED
        record.verify_method = outcome.method
        record.verify_detail = bound_evidence(detail)
        saved = self.records.save_dump(record)
        self._audit(
            "db.backup.verify",
            f"{manager.ENGINE_NAME}/{record.db_name or view.info.database}",
            "ok" if ok else "failure",
            file=view.name,
            method=outcome.method,
        )
        return DumpView(view.info, saved)

    def verify(self, engine: str, file_name: str, *, restore: bool = False) -> DumpView:
        """
        Check a dump again, optionally by loading it into a temporary database.

        Args:
            engine: The engine.
            file_name: The dump's file name.
            restore: Also load it into a temporary database and drop it.

        Returns:
            The dump with its evidence updated.

        Raises:
            DatabaseNotFoundError: When there is no such dump.
            DatabaseBackupError: When a restore test is asked of a Redis
                snapshot.
            DatabaseEngineError: When the engine is not running for a
                restore test.
        """
        view = self._check(self._view(engine, file_name))
        if not restore or (view.record and view.record.verify_status != STATUS_OK):
            return view
        return self._restore_test(view)

    def _restore_test(self, view: DumpView) -> DumpView:
        """
        Load a dump that passed its check into a temporary database.

        Args:
            view: The dump, already checked.

        Returns:
            The dump with the test's evidence recorded.

        Raises:
            DatabaseBackupError: For a Redis snapshot.
            DatabaseEngineError: When the engine is not running.
        """
        manager = self.service.running(view.info.engine)
        if manager.engine_type == "redis":
            raise DatabaseBackupError(
                "A Redis snapshot cannot be test-restored",
                details="It would replace every key of the instance.",
            )
        result = restore_test(manager, view.info.path)
        record = view.record or DumpRecord(engine=manager.ENGINE_NAME, file_name=view.name)
        record.restore_tested_at = now()
        record.restore_test_status = STATUS_OK if result.ok else STATUS_FAILED
        record.restore_test_detail = bound_evidence(result.detail)
        saved = self.records.save_dump(record)
        self._audit(
            "db.backup.verify",
            f"{manager.ENGINE_NAME}/{record.db_name or view.info.database}",
            "ok" if result.ok else "failure",
            file=view.name,
            method="restore_test",
            temporary=result.database,
        )
        return DumpView(view.info, saved)

    def delete_dump(self, engine: str, file_name: str) -> None:
        """
        Delete a dump from this server. Copies on destinations stay.

        Args:
            engine: The engine.
            file_name: The dump's file name.

        Raises:
            DatabaseNotFoundError: When there is no such dump.
        """
        view = self._view(engine, file_name)
        self._remove_local(view, reason="operator")

    def _remove_local(self, view: DumpView, *, reason: str) -> None:
        """
        Delete a dump's file and its record.

        Args:
            view: The dump.
            reason: Why, for the audit trail: ``operator`` or ``retention``.
        """
        self.fs.remove(view.info.path)
        self.records.delete_dump(view.info.engine, view.name)
        self._audit(
            "db.backup.delete",
            f"{view.info.engine}/{view.info.database}",
            file=view.name,
            reason=reason,
        )

    # -------------------------------------------------------------- offsite

    def _database_of(self, view: DumpView) -> str:
        """
        Args:
            view: A dump.

        Returns:
            The database it belongs to: the one it was taken for, else the
            one its file name says.
        """
        return (view.record.db_name if view.record and view.record.db_name else None) or (
            view.info.database
        )

    def push(
        self,
        engine: str,
        file_name: str,
        destination: str,
        *,
        scheduled: bool = False,
        retention_count: int | None = None,
        retention_days: int | None = None,
        retention_gb: float | None = None,
    ) -> dict[str, Any]:
        """
        Send a dump to a destination, after making sure it is a good one.

        A dump that has not passed its check is checked first, and one that
        fails it is not sent: a bad copy on a destination is worse than none,
        because it is trusted.

        Args:
            engine: The engine.
            file_name: The dump's file name.
            destination: A destination created with ``noust backup destination add``.
            scheduled: Whether a schedule sends it, which makes it eligible
                for the destination's retention.
            retention_count: This server's scheduled dumps of the database to
                keep there; None for no limit.
            retention_days: Maximum age in days of those; None for no limit.
            retention_gb: The most those may weigh together there, in
                gigabytes; None for no limit.

        Returns:
            What the destination manager reports: ``verified_by``,
            ``retention_deleted``...

        Raises:
            DatabaseNotFoundError: When there is no such dump.
            DatabaseBackupError: When the dump fails its check.
            ValidationError: When the destination does not exist.
            BackupError: When the destination refuses it.
        """
        self._require_destination(destination)
        view = self._view(engine, file_name)
        if not view.record or view.record.verify_status != STATUS_OK:
            view = self._check(view)
        record = view.record
        if record is None or record.verify_status != STATUS_OK:
            raise DatabaseBackupError(
                f"{file_name} failed its check and was not sent to {destination}",
                details=(record.verify_detail if record else "") or "It could not be checked.",
            )
        database = self._database_of(view)
        folder = remote_folder(view.info.engine, database)
        target = f"{view.info.engine}/{database}"
        try:
            summary = self.destinations.push_file(
                view.info.path,
                destination,
                folder,
                sidecar={
                    "version": 1,
                    "engine": view.info.engine,
                    "database": database,
                    "format": backup_format(view.info.path),
                    "sha256": record.sha256,
                    "created": view.info.created.isoformat(),
                    "scheduled": scheduled,
                    "verified_at": record.verified_at,
                },
                retention_count=retention_count,
                retention_days=retention_days,
                retention_bytes=gigabytes_to_bytes(check_gigabytes(retention_gb, "retention_gb")),
            )
        except NoustError:
            self._audit(
                "db.backup.push", target, "failure", file=file_name, destination=destination
            )
            raise
        copies = [c for c in record.remote_copies if c.get("destination") != destination]
        copies.append(
            {
                "destination": destination,
                "folder": folder,
                "pushed_at": now(),
                "verified_by": summary.get("verified_by"),
            }
        )
        record.remote_copies = copies
        self.records.save_dump(record)
        self._audit(
            "db.backup.push",
            target,
            file=file_name,
            destination=destination,
            verified_by=summary.get("verified_by"),
        )
        return summary

    def remote_databases(self, engine: str, destination: str) -> list[str]:
        """
        List the databases a destination holds dumps of, for this engine.

        Args:
            engine: The engine.
            destination: The destination.

        Returns:
            The database names (``instance`` for Redis), sorted.

        Raises:
            BackupError: When the destination cannot be listed.
        """
        canonical = self.service.manager(engine).ENGINE_NAME
        self._require_destination(destination)
        return self.destinations.list_folders(destination, f"{REMOTE_ROOT}/{canonical}")

    def remote_dumps(self, engine: str, database: str, destination: str) -> list[dict[str, Any]]:
        """
        List a database's dumps on a destination, newest first.

        Args:
            engine: The engine.
            database: The database.
            destination: The destination.

        Returns:
            One object per dump: ``name``, ``size``, ``modified``, ``own``
            (this server sent it), ``scheduled``, ``sha256``, ``format``,
            ``created``, ``local`` (the file is also on this server).

        Raises:
            BackupError: When the destination cannot be listed.
        """
        manager = self.service.manager(engine)
        name = manager.validate_database_name(database)
        self._require_destination(destination)
        folder = remote_folder(manager.ENGINE_NAME, name)
        here = {view.name for view in self.list_dumps(manager.ENGINE_NAME)}
        dumps = []
        for entry in self.destinations.list_files(destination, folder):
            sidecar = entry.sidecar or {}
            dumps.append(
                {
                    "name": entry.name,
                    "size": entry.size,
                    "size_human": format_size(entry.size) if entry.size is not None else None,
                    "modified": entry.modified,
                    "own": entry.own,
                    "scheduled": sidecar.get("scheduled") is True,
                    "sha256": sidecar.get("sha256"),
                    "format": sidecar.get("format") or backup_format(Path(entry.name)),
                    "created": sidecar.get("created"),
                    "database": sidecar.get("database") or name,
                    "local": entry.name in here,
                    "destination": destination,
                }
            )
        return dumps

    def delete_remote(self, engine: str, database: str, file_name: str, destination: str) -> None:
        """
        Delete a dump, and its sidecar, from a destination.

        Args:
            engine: The engine.
            database: The database the dump is of.
            file_name: The dump's file name.
            destination: The destination.

        Raises:
            BackupError: When the destination refuses.
        """
        manager = self.service.manager(engine)
        name = manager.validate_database_name(database)
        self._require_destination(destination)
        self.destinations.delete_file(
            destination, remote_folder(manager.ENGINE_NAME, name), file_name
        )
        record = self.records.dump(manager.ENGINE_NAME, file_name)
        if record is not None:
            record.remote_copies = [
                c for c in record.remote_copies if c.get("destination") != destination
            ]
            self.records.save_dump(record)
        self._audit(
            "db.backup.delete",
            f"{manager.ENGINE_NAME}/{name}",
            file=file_name,
            destination=destination,
            reason="operator",
        )

    # -------------------------------------------------------------- restore

    def restore(
        self,
        engine: str,
        database: str,
        file_name: str,
        *,
        drop_existing: bool = False,
        safety_backup: bool = True,
        new_name: str | None = None,
    ) -> RestoreOutcome:
        """
        Restore a local dump, and keep track of the safety copy it took.

        Args:
            engine: The engine.
            database: The database the dump is of, and the target unless
                ``new_name`` is given.
            file_name: The dump's file name.
            drop_existing: Drop and recreate the target before loading.
            safety_backup: Dump the target first when nothing is dropped.
            new_name: Restore into a new database of this name instead.

        Returns:
            What the service did.

        Raises:
            DatabaseNotFoundError: When there is no such dump.
            DatabaseBackupError: When the restore fails.
        """
        source = self.service.dump_path(engine, file_name)
        return self.restore_file(
            engine,
            database,
            source,
            drop_existing,
            safety_backup,
            new_name,
            options=OWN_DUMP_OPTIONS,
        )

    def restore_remote(
        self,
        engine: str,
        database: str,
        destination: str,
        file_name: str,
        *,
        drop_existing: bool = False,
        safety_backup: bool = True,
        new_name: str | None = None,
    ) -> RestoreOutcome:
        """
        Download a dump from a destination, verify it, and restore it.

        Nothing is touched until the download matches its recorded digest and
        the file passes its check; the download is staged beside the dumps and
        removed afterwards whatever happens.

        Args:
            engine: The engine.
            database: The database the dump is of (which names its folder),
                and the target unless ``new_name`` is given.
            destination: The destination.
            file_name: The dump's file name there.
            drop_existing: Drop and recreate the target before loading.
            safety_backup: Dump the target first when nothing is dropped.
            new_name: Restore into a new database of this name instead.

        Returns:
            What the service did.

        Raises:
            BackupError: When the download fails or does not match its digest.
            DatabaseBackupError: When the file fails its check, or the
                restore fails.
        """
        manager = self.service.running(engine)
        name = manager.validate_database_name(database)
        self._require_destination(destination)
        staging = manager.BACKUP_DIR / _STAGING_DIR / f"{secrets.token_hex(4)}"
        try:
            path, _ = self.destinations.download_file(
                destination,
                remote_folder(manager.ENGINE_NAME, name),
                file_name,
                staging,
                fs=self.fs,
            )
            outcome = check_dump(manager, path)
            if not outcome.ok:
                raise DatabaseBackupError(
                    f"{file_name} from {destination} failed its check; nothing was restored",
                    details=outcome.detail,
                )
            # Matched to the checksum recorded when Noust sent it, and read by the engine.
            return self.restore_file(
                manager.ENGINE_NAME,
                name,
                path,
                drop_existing,
                safety_backup,
                new_name,
                options=OWN_DUMP_OPTIONS,
            )
        finally:
            try:
                self.fs.remove_tree(staging)
            except OSError as exc:
                self.service.logger.warning(f"Could not remove the download at {staging}: {exc}")

    def suggest_name(self, engine: str, database: str, file_name: str | None = None) -> str:
        """
        Suggest a name for restoring a dump as a new database.

        The name says what it is (``shop_restored_20260928``), fits the
        engine's limit, and is one nothing uses yet.

        Args:
            engine: The engine.
            database: The database the dump is of.
            file_name: The dump, whose timestamp dates the name; today when None.

        Returns:
            A free database name.

        Raises:
            ValidationError: For Redis, which cannot restore beside itself.
            DatabaseEngineError: When the engine is not running.
        """
        manager = self.service.running(engine)
        if manager.engine_type == "redis":
            raise ValidationError(
                "Redis cannot restore as a new database",
                details="A snapshot replaces every key of the instance.",
            )
        base = manager.validate_database_name(database)
        match = re.search(r"(\d{8})_\d{6}", file_name or "")
        stamp = match.group(1) if match else datetime.now().strftime("%Y%m%d")
        limit = manager.MAX_DATABASE_NAME_LENGTH
        for attempt in range(1, 100):
            suffix = f"_restored_{stamp}" + (f"_{attempt}" if attempt > 1 else "")
            candidate = f"{base[: limit - len(suffix)]}{suffix}"
            if not manager.database_exists(candidate):
                return candidate
        suffix = f"_restored_{secrets.token_hex(3)}"
        return f"{base[: limit - len(suffix)]}{suffix}"

    def restore_file(
        self,
        engine: str,
        database: str,
        source: Path,
        drop_existing: bool,
        safety_backup: bool,
        new_name: str | None,
        *,
        options: Mapping[str, Any] | None = None,
        as_superuser: bool = False,
        confirm_superuser: bool = False,
    ) -> RestoreOutcome:
        """
        Restore a file through the service and record the safety copy it took.

        The file is whatever the caller already vetted: a dump named in the
        engine's directory (:meth:`restore`), a download that passed its
        check (:meth:`restore_remote`), or a path the operator gave the CLI.
        Whatever it is, PostgreSQL loads it as the database's owner unless
        ``as_superuser`` says otherwise (checklist H-19).

        Args:
            engine: The engine.
            database: The database the dump is of.
            source: The dump.
            drop_existing: Drop and recreate the target before loading.
            safety_backup: Dump the target first when nothing is dropped.
            new_name: Restore into a new database of this name instead.
            options: What the engine's loader is asked beyond that (an import's
                ``keep_owners``); the engine ignores what it has no use for.
            as_superuser: PostgreSQL: load the dump as the cluster's superuser
                instead of the database's owner, which lets it run programs
                on this server. The operator's explicit decision, audited.
            confirm_superuser: The operator accepted :data:`SUPERUSER_WARNING`.

        Returns:
            What the service did.

        Raises:
            ValidationError: For a new name on Redis, which cannot restore
                beside itself, or ``as_superuser`` on an engine that has no
                such account to ask for.
            ConfirmationRequired: ``as_superuser`` without ``confirm_superuser``.
        """
        manager = self.service.manager(engine)
        if new_name is not None and manager.engine_type == "redis":
            raise ValidationError(
                "Redis cannot restore as a new database",
                details="A snapshot replaces every key of the instance.",
                field="new_name",
            )
        canonical = manager.ENGINE_NAME
        if as_superuser:
            options = self.superuser_options(manager, options, confirm_superuser)
            # Before it runs: a load that kills the server still leaves the decision on record.
            self._audit(
                "db.restore.superuser", f"{canonical}/{new_name or database}", source=source.name
            )
        journal = _RestoreJournal(self.service.store, self.fs)
        entry = journal.begin(canonical, new_name or database, source, replace=drop_existing)
        noted: set[Path] = set()

        def taken(copy: Path) -> None:
            # Before anything is dropped: a console restarted mid-restore
            # leaves the copy on record, and the journal says where it is.
            self._record_safety_copy(canonical, database, copy)
            noted.add(copy)
            journal.note_safety_copy(entry, copy)

        try:
            outcome = self.service.restore(
                engine,
                database,
                source,
                drop_existing=drop_existing,
                safety_backup=safety_backup,
                new_name=new_name,
                on_safety_copy=taken,
                options=options,
            )
        except Exception:
            # A failure the restore handled (and said so): not interrupted.
            # Only a process that stopped (killed, restarted) leaves the entry.
            journal.end(entry)
            raise
        journal.end(entry)
        if outcome.safety_copy is not None and outcome.safety_copy not in noted:
            self._note_safety_copy(engine, database, outcome)
        if outcome.safety_copy is not None:
            self._prune_safety_copies(engine, database)
        return outcome

    def superuser_options(
        self, manager: BaseDatabaseManager, options: Mapping[str, Any] | None, confirmed: bool
    ) -> dict[str, Any]:
        """
        Turn the operator's request for a superuser load into the engine's option.

        Args:
            manager: The engine the dump is loaded into.
            options: The options so far.
            confirmed: The operator accepted :data:`SUPERUSER_WARNING`.

        Returns:
            The options with ``as_superuser`` set.

        Raises:
            ValidationError: For an engine that is not PostgreSQL.
            ConfirmationRequired: When the warning was not accepted.
        """
        if manager.engine_type != "postgresql":
            raise ValidationError(
                f"{manager.DISPLAY_NAME} has no superuser load to ask for",
                details="Only PostgreSQL loads a dump as the database's owner by default.",
                field="as_superuser",
            )
        if not confirmed:
            raise ConfirmationRequired([SUPERUSER_WARNING], field="confirm_superuser")
        return {**(options or {}), **OWN_DUMP_OPTIONS}

    def safety_copies_kept(self) -> int:
        """
        Say how many safety copies of a database are kept.

        Returns:
            ``databases.safety_copies_kept``, at least 1: the newest safety
            copy is the way back from the restore that took it, and is never
            deleted.
        """
        from noust.core.config import Config

        value = Config().get("databases.safety_copies_kept", DEFAULT_SAFETY_COPIES_KEPT)
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return DEFAULT_SAFETY_COPIES_KEPT

    def _prune_safety_copies(self, engine: str, database: str) -> list[str]:
        """
        Delete a database's oldest safety copies beyond the number kept.

        Only safety copies are considered, and the newest always stays
        (:func:`~noust.managers.retention.select_expired` never picks it).

        Args:
            engine: The engine.
            database: The database restored over.

        Returns:
            The file names deleted.
        """
        canonical = self.service.manager(engine).ENGINE_NAME
        by_name = {view.name: view for view in self.list_dumps(canonical)}
        candidates = [
            (record.file_name, by_name[record.file_name].info.created.astimezone(timezone.utc))
            for record in self.records.dumps(canonical, database)
            if record.origin == "safety" and record.file_name in by_name
        ]
        expired = select_expired(
            candidates,
            count=max(1, self.safety_copies_kept()),
            days=None,
            now=datetime.now(timezone.utc),
        )
        for file_name in expired:
            self._remove_local(by_name[file_name], reason="safety copies kept")
        return expired

    def _note_safety_copy(self, engine: str, database: str, outcome: RestoreOutcome) -> None:
        """
        Record the copy a restore took of what it was about to overwrite.

        It is kept as ``safety``: never deleted by retention, shown as what it
        is, and named in the restore's own result.

        Args:
            engine: The engine.
            database: The database restored over.
            outcome: What the restore did.
        """
        copy = outcome.safety_copy
        if copy is None:
            return
        self._record_safety_copy(self.service.manager(engine).ENGINE_NAME, database, copy)

    def _record_safety_copy(self, canonical: str, database: str, copy: Path) -> None:
        """
        Record one safety copy as ``safety``.

        Args:
            canonical: Canonical engine name.
            database: The database it is a copy of.
            copy: The file.
        """
        if not copy.is_file():
            return
        self.records.save_dump(
            DumpRecord(
                engine=canonical,
                file_name=copy.name,
                db_name=database,
                origin="safety",
                size=copy.stat().st_size,
                sha256=file_sha256(copy),
            )
        )

    # ----------------------------------------------------------- policy run

    def run_policy(
        self,
        engine: str,
        database: str,
        *,
        notify: bool = False,
        on_step: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """
        Do everything a database's policy promises: dump, check, send, prune.

        The order is the safety: the new dump is checked (and test-restored,
        when the policy asks) before anything is sent or deleted, so a dump
        that does not restore can neither replace a good remote copy nor push
        an old one out. A destination that fails does not stop the others.
        With no policy row - a store that moved while the timer kept firing -
        the dump is still taken, with no retention and no destinations:
        skipping every backup because a row is missing is the worse outcome.

        The policy that covers all of an engine's databases
        (:data:`~noust.managers.database.scope.ALL_DATABASES`) does this for
        each database the engine holds now, one after the other, leaving out
        the ones its exclusions name and the ones that have a policy of their
        own. One database failing does not stop the next: every one is dumped,
        and the run fails at the end, naming each that did not go through.

        Args:
            engine: The engine.
            database: The database, or ``*`` for the all-databases policy.
            notify: Announce a failure (and, when the operator switched it on,
                the success) through the notifier. A console job leaves this
                off: the job manager announces its failure.
            on_step: Told what is happening, for a job's progress.

        Returns:
            ``dump`` (the dump as :meth:`DumpView.to_dict` describes it),
            ``restore_test``, ``destinations`` (``{"ok": ...}`` each),
            ``local_deleted`` and ``policy_missing``. For the all-databases
            policy: ``databases`` (each database's own result, or its
            ``{"ok": False, "error": ...}``) and ``skipped``.

        Raises:
            DatabaseBackupError: When the dump fails or does not verify.
            BackupError: When at least one destination could not be sent it,
                or any database of an all-databases run failed; what was
                dumped is kept either way.
        """
        # Not ``running()``: an engine that is down is a failed run to announce, and the
        # dump below is what finds it out, inside the block that announces.
        manager = self.service.manager(engine)
        engine_name = manager.ENGINE_NAME
        if database == ALL_DATABASES:
            return self._run_all(manager, notify=notify, on_step=on_step)
        name = manager.validate_database_name(database)
        policy = self.records.policy(engine_name, name)
        if policy is None:
            self.service.logger.warning(
                f"No backup policy is stored for {engine_name}/{name}; taking a dump with no "
                "retention and no destinations. Save the policy again with: "
                "noust db backup-schedule set"
            )
        return self._run_one(engine_name, name, policy, notify=notify, on_step=on_step)

    def _run_all(
        self,
        manager: BaseDatabaseManager,
        *,
        notify: bool,
        on_step: Callable[[str], None] | None,
    ) -> dict[str, Any]:
        """
        Run the all-databases policy: every database the engine holds, one by one.

        Args:
            manager: The engine.
            notify: See :meth:`run_policy`.
            on_step: See :meth:`run_policy`.

        Returns:
            See :meth:`run_policy`.

        Raises:
            BackupError: When any database could not be dumped or sent.
        """
        engine_name = manager.ENGINE_NAME
        policy = self.records.policy(engine_name, ALL_DATABASES)
        log = self.service.logger
        if policy is None:
            log.warning(
                f"No all-databases policy is stored for {engine_name}; dumping every database "
                "with no retention and no destinations. Save the policy again with: "
                "noust db backup-schedule set --all"
            )
        left_out = Exclusions.from_json(policy.exclusions if policy else None)
        own = {p.db_name for p in self.records.policies(engine_name) if p.scope == SCOPE_DATABASE}
        result: dict[str, Any] = {
            "engine": engine_name,
            "database": ALL_DATABASES,
            "policy_missing": policy is None,
            "databases": {},
            "skipped": [],
        }
        try:
            held = [info.name for info in manager.list_databases()]
        except DatabaseError as exc:
            self._finish(engine_name, ALL_DATABASES, ok=False, error=str(exc), dump=None)
            raise
        failures: list[tuple[str, str]] = []
        dumped: list[str] = []
        for name in held:
            if name in left_out.databases:
                result["skipped"].append({"database": name, "reason": "excluded"})
                continue
            if name in own:
                result["skipped"].append({"database": name, "reason": "has its own policy"})
                continue
            try:
                result["databases"][name] = {
                    "ok": True,
                    **self._run_one(
                        engine_name, name, policy, notify=notify, on_step=on_step, record=False
                    ),
                }
                dumped.append(name)
            except NoustError as exc:
                result["databases"][name] = {"ok": False, "error": str(exc)}
                failures.append((name, str(exc)))
        if not dumped and not failures:
            log.warning(
                f"There is no database of {engine_name} for the all-databases policy to dump"
            )
        if failures:
            error = BackupError(
                f"{len(failures)} of {len(dumped) + len(failures)} {engine_name} databases "
                "were not backed up: " + ", ".join(name for name, _ in failures),
                details="What was dumped was kept.\n"
                + "\n".join(f"{name}: {text}" for name, text in failures),
            )
            self._finish(engine_name, ALL_DATABASES, ok=False, error=str(error), dump=None)
            raise error
        self._finish(
            engine_name,
            ALL_DATABASES,
            ok=True,
            error=None,
            dump=f"{len(dumped)} databases" if dumped else None,
        )
        return result

    def _run_one(
        self,
        engine_name: str,
        name: str,
        policy: BackupPolicy | None,
        *,
        notify: bool,
        on_step: Callable[[str], None] | None,
        record: bool = True,
    ) -> dict[str, Any]:
        """
        Dump, check, send and prune one database under a policy.

        Args:
            engine_name: Canonical engine name.
            name: The database.
            policy: The policy that governs it: its own, or the all-databases
                one; None to dump with no retention and no destinations.
            notify: See :meth:`run_policy`.
            on_step: See :meth:`run_policy`.
            record: Note how the run ended on the policy row of this database.
                An all-databases run notes it once, on its own row.

        Returns:
            See :meth:`run_policy`.

        Raises:
            DatabaseBackupError: When the dump fails or does not verify.
            BackupError: When a destination could not be sent it.
        """

        def step(text: str) -> None:
            self.service.logger.info(text)
            if on_step:
                on_step(text)

        subject = database_subject(engine_name, name)

        def announce(build: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
            if notify:
                deliver(lambda ctx: build(engine_name, name, *args, ctx, **kwargs))

        def finish(*, ok: bool, error: str | None, dump: Any) -> None:
            if record:
                self._finish(engine_name, name, ok=ok, error=error, dump=dump)

        result: dict[str, Any] = {
            "engine": engine_name,
            "database": name,
            "policy_missing": policy is None,
            "destinations": {},
            "local_deleted": [],
            "restore_test": None,
        }
        step(f"Dumping {name}")
        try:
            view = self.dump(
                engine_name,
                name,
                origin="scheduled",
                dump_format=policy.dump_format if policy else None,
                exclusions=Exclusions.from_json(policy.exclusions if policy else None).per_dump(),
            )
            result["dump"] = view.to_dict()
            if policy and policy.verify_restore:
                step("Testing the restore into a temporary database")
                view = self._restore_test(view)
                result["dump"] = view.to_dict()
                if view.record and view.record.restore_test_status == STATUS_FAILED:
                    raise DumpFailedItsCheck(
                        f"The dump {view.name} did not restore into a temporary database",
                        details=view.record.restore_test_detail or "",
                    )
                result["restore_test"] = view.record.restore_test_status if view.record else None
        except NoustError as exc:
            finish(ok=False, error=str(exc), dump=result.get("dump"))
            announce(
                compose_database_backup_failed,
                str(exc),
                failure_id=record_failure("backup", subject, str(exc)),
            )
            if policy is not None and not isinstance(exc, DumpFailedItsCheck):
                # A run that could not take a dump (the disk is full) is the one that most needs
                # its old dumps pruned by the policy; a corrupt dump is kept as evidence instead.
                self._prune_after_failure(policy, name)
            raise

        failures: list[tuple[str, str]] = []
        if policy:
            for entry in policy.destinations:
                target = entry["name"]
                step(f"Sending {view.name} to {target}")
                try:
                    summary = self.push(
                        engine_name,
                        view.name,
                        target,
                        scheduled=True,
                        retention_count=entry.get("retention_count"),
                        retention_days=entry.get("retention_days"),
                        retention_gb=entry.get("retention_gb"),
                    )
                    result["destinations"][target] = {"ok": True, **summary}
                except NoustError as exc:
                    result["destinations"][target] = {"ok": False, "error": str(exc)}
                    failures.append((target, str(exc)))
                    announce(
                        compose_database_backup_upload_failed,
                        target,
                        str(exc),
                        failure_id=record_failure("upload", subject, str(exc), destination=target),
                    )
            if policy.remote_only and policy.destinations and not failures:
                # Every destination holds it and verified it on arrival: the local copy is the
                # one the small disk cannot afford. A dump that did not go everywhere stays.
                step(f"Removing {view.name} from this server: every destination holds it")
                self._remove_local(view, reason="remote_only")
                result["local_deleted"] = [view.name]
                result["kept_locally"] = False
            else:
                step("Applying local retention")
                result["local_deleted"] = self._apply_local_retention(policy, name)

        if failures:
            error = BackupError(
                f"The dump of {name} was taken but could not be sent to: "
                + ", ".join(target for target, _ in failures),
                details="The local dump was kept.\n"
                + "\n".join(f"{target}: {text}" for target, text in failures),
            )
            finish(ok=False, error=str(error), dump=view.name)
            raise error
        finish(ok=True, error=None, dump=view.name)
        if notify:
            sent = tuple(result["destinations"])
            deliver(
                lambda ctx: compose_database_backup_completed(
                    engine_name, name, view.name, ctx, size_bytes=view.info.size, destinations=sent
                )
            )
        return result

    def _finish(
        self, engine: str, database: str, *, ok: bool, error: str | None, dump: Any
    ) -> None:
        """
        Note how a run ended on its policy, when it has one.

        Args:
            engine: Canonical engine name.
            database: The database.
            ok: Whether the run went everywhere it was meant to.
            error: What it said when it did not.
            dump: The dump's name, or a dump's description with a ``name``.
        """
        name = dump.get("name") if isinstance(dump, dict) else dump
        self.records.record_run(engine, database, ok=ok, error=error, dump=name)

    def _apply_local_retention(
        self, policy: BackupPolicy, database: str | None = None
    ) -> list[str]:
        """
        Delete a database's scheduled dumps beyond a policy's limits.

        Only dumps a schedule made are ever considered: one taken by hand and
        the safety copy of a restore are not in the list, whatever their age.

        Args:
            policy: The policy.
            database: The database whose dumps are pruned. The policy's own
                when None; an all-databases policy prunes each in turn.

        Returns:
            The file names deleted.
        """
        if not policy.retention_count and not policy.retention_days and not policy.retention_bytes:
            return []
        by_name = {view.name: view for view in self.list_dumps(policy.engine)}
        candidates: list[tuple[str, datetime]] = []
        good: dict[str, bool] = {}
        for record in self.records.dumps(policy.engine, database or policy.db_name):
            view = by_name.get(record.file_name)
            if view is None:
                # The file is gone: forget the row so the table does not grow.
                self.records.delete_dump(policy.engine, record.file_name)
            elif record.origin == "scheduled":
                candidates.append((record.file_name, view.info.created.astimezone(timezone.utc)))
                good[record.file_name] = (
                    record.verify_status != STATUS_FAILED
                    and record.restore_test_status != STATUS_FAILED
                )
        expired = select_expired(
            candidates,
            count=policy.retention_count,
            days=policy.retention_days,
            now=datetime.now(timezone.utc),
            max_bytes=policy.retention_bytes,
            sizes={name: by_name[name].info.size for name, _moment in candidates},
        )
        # The newest dump is kept; when it is a bad one, so is the newest that is not.
        expired = without_newest_good(
            expired, [(name, moment, good[name]) for name, moment in candidates]
        )
        for file_name in expired:
            self._remove_local(by_name[file_name], reason="retention")
        return expired

    def _prune_after_failure(self, policy: BackupPolicy, database: str) -> None:
        """
        Apply a policy's retention after a run that failed, without hiding why it failed.

        Args:
            policy: The policy.
            database: The database whose dumps are pruned.
        """
        try:
            self._apply_local_retention(policy, database)
        except (NoustError, OSError) as exc:
            self.service.logger.warning(
                f"Could not apply the retention of {policy.engine}/{database} after a failed "
                f"run: {exc}"
            )

    def sweep_retention(self) -> dict[str, list[str]]:
        """
        Apply every policy's local retention now, whether or not its last run went well.

        A policy used to prune only after a run that went well, so a dump that failed pruned
        nothing and a timer that stopped pruned nothing either. This is the separate sweep: it
        reads the policies and the dumps, never the engine, so it works with the engine down.
        What a policy for all of an engine's databases covers is every database that has dumps
        and neither a policy of its own nor a place in its exclusions.

        Returns:
            For each ``engine/database`` that lost dumps, their file names.
        """
        policies = self.records.policies()
        own = {(p.engine, p.db_name) for p in policies if p.scope == SCOPE_DATABASE}
        deleted: dict[str, list[str]] = {}
        for policy in policies:
            if policy.scope == SCOPE_DATABASE:
                names = [policy.db_name]
            else:
                left_out = Exclusions.from_json(policy.exclusions).databases
                names = sorted(
                    {
                        record.db_name
                        for record in self.records.dumps(policy.engine)
                        if record.db_name
                        and (policy.engine, record.db_name) not in own
                        and record.db_name not in left_out
                    }
                )
            for name in names:
                removed = self._apply_local_retention(policy, name)
                if removed:
                    deleted[f"{policy.engine}/{name}"] = removed
        return deleted


def _limit(value: Any, ceiling: int, field_name: str) -> int | None:
    """
    Check a retention value.

    Args:
        value: What the caller gave.
        ceiling: The most a policy accepts.
        field_name: The field, for the error.

    Returns:
        The value as an int, or None when the caller gave none.

    Raises:
        ValidationError: When it is not a whole number from 1 to ``ceiling``.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= ceiling:
        raise ValidationError(
            f"{field_name} must be a whole number from 1 to {ceiling}",
            details="Leave it empty for no limit.",
            field=field_name,
        )
    return int(value)
