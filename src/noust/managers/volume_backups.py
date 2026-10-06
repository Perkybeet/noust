# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Backups of an application's data: its persistent directories and Docker volumes.

An application backup (:mod:`noust.managers.backup_manager`) is one archive of
the whole application, taken with the application, and is the right unit to
move an application. What it is the wrong unit for is the data that changes
every minute and is worth more than the code: the uploads of a shop, a
SQLite file, the named volume of a Compose stack. Those want their own
schedule, their own retention and their own destinations, without archiving a
release every time. This module is that: **one archive per data target**,
scheduled, kept, sent away, verified and restored on its own.

**Targets.** What an application declares as its data, found rather than
configured:

- the persistent paths of a release application (``shared/<path>``, which the
  recipes fill in too), and any data directory the policy adds
  (``--path``), relative to where the data lives;
- the named volumes of a Docker Compose stack, as Docker knows them.

Each is a key (``path:uploads``, ``volume:shop_pgdata``), which a policy lists
or leaves empty for all of them.

**Consistency.** A directory copied while an application writes to it can be
torn. Three modes, per policy, say how still the data is kept
(:data:`~noust.managers.volume_backup_records.CONSISTENCY_MODES`): ``live``
(nothing is stopped; SQLite databases are still snapshotted with SQLite's own
backup API, so they are whole), ``pause`` (the application is frozen in place,
``systemctl freeze`` or ``docker pause``, and thawed after) and ``stop`` (the
service is stopped for the duration). Whatever the mode, the application is
started again when the archive is done, whether or not it succeeded.

**Files.** ``<backups>/volumes/<application>/<id>.tar.gz`` with a signed
``<id>.json`` beside it, 0600, like an application backup: the sidecar says
what it is, its SHA-256 and which server took it, and carries a MAC under the
key in the secret store, so a copy on a destination is checked against more
than itself. A manual backup and the safety copy a restore takes are never
deleted by retention; a schedule only ever deletes its own.

**Restore** puts the data back as it was, not merged over what is there: the
current data is archived first (the safety copy), the application is stopped,
the data is replaced and the application is started and passes its health
gate; when it does not, what was there is put back and the application
started on it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import stat
import tarfile
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from noust.central import require_server_role
from noust.core import audit
from noust.core.applock import AppBusyError, app_lock
from noust.core.config import Config, resolve_backup_directory
from noust.core.exceptions import (
    BackupError,
    NoustError,
    SourceError,
    ValidationError,
)
from noust.core.fs import SECRET_DIR_MODE, SECRET_MODE, DryRunFileSystem, FileSystem, get_fs
from noust.core.logger import Logger
from noust.core.notifications.composers import (
    compose_backup_completed,
    compose_backup_failed,
    compose_backup_upload_failed,
)
from noust.core.notifications.context import NotificationContext
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import App, NoustStore, get_store
from noust.core.utils import domain_to_app_name
from noust.deployers.helpers.layout import RELEASES, app_root
from noust.deployers.releases import SHARED_DIR, persistent_path
from noust.managers.backup_destination_files import DestinationFileManager, file_sha256
from noust.managers.backup_manager import (
    SCHEDULED_TAG,
    BackupManager,
    server_id,
    sidecar_mac_state,
    sign_sidecar,
)
from noust.managers.backup_scheduler import BackupScheduler, validate_calendar, verify_days
from noust.managers.docker_volumes import (
    DOCKER_VOLUME_NAME,
    docker_archive_argv,
    docker_restore_argv,
)
from noust.managers.retention import select_expired
from noust.managers.service_manager import ServiceManager
from noust.managers.source_manager import extract_archive
from noust.managers.volume_backup_records import (
    CONSISTENCY_MODES,
    VolumePolicy,
    VolumeRecords,
)
from noust.validators.domain import validate_domain
from noust.validators.names import validate_app_name, validate_filename

__all__ = [
    "SAFETY_TAG",
    "VOLUMES_DIR",
    "VolumeBackupManager",
    "VolumeBackupMetadata",
    "VolumeBackupNotFound",
    "VolumePolicyView",
    "VolumeTarget",
]

_log = logging.getLogger(__name__)

#: Where volume backups live under the backup directory, then ``<application>``.
VOLUMES_DIR = "volumes"

#: The archive's suffix.
ARCHIVE_SUFFIX = ".tar.gz"

#: Tag of the copy a restore takes of what it replaces: never retention's to delete.
SAFETY_TAG = "safety"

#: Where a destination keeps them: ``volumes/<application>/<target>``.
REMOTE_ROOT = "volumes"

#: gzip level: nine costs several times the CPU of six for a few percent.
_COMPRESS_LEVEL = 6

#: Deadline for ``docker run`` of the helper that tars a volume (the first run pulls alpine).
_DOCKER_TIMEOUT = 3600

#: Deadline for a systemctl or docker verb that only signals.
_SIGNAL_TIMEOUT = 60

#: Retention limits a policy accepts, the same as an application's schedule.
MAX_RETENTION_COUNT = 365
MAX_RETENTION_DAYS = 3650

#: Members and bytes a restore extracts at most.
MAX_ENTRIES = 5_000_000
MAX_BYTES = 1024**4

#: How long a scheduled run waits for an operation holding the application's lock.
LOCK_WAIT_SECONDS = 1800
LOCK_RETRY_SECONDS = 30

_SQLITE_MAGIC = b"SQLite format 3\x00"
_SQLITE_COMPANIONS = ("-wal", "-shm", "-journal")

#: What a volume backup's id looks like: the application, a dot, the target, then the moment.
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*\.[A-Za-z0-9_][A-Za-z0-9_$.-]*_\d{8}_\d{6}$")

#: A target's slug, which names its archive, its remote folder and its id.
_SLUG_JUNK = re.compile(r"[^A-Za-z0-9_$-]+")


# ------------------------------------------------------------------ the targets


@dataclass(frozen=True)
class VolumeTarget:
    """
    One piece of an application's data that is backed up on its own.

    Attributes:
        key: What a policy names it by: ``path:<relative path>`` or ``volume:<name>``.
        kind: ``path`` (a directory or a file on the host) or ``docker`` (a named volume).
        source: The relative path under the data root, or the volume's name.
        host_path: Where a path target is on the host; None for a volume.
        exists: Whether there is something to back up now.
        declared_by: Where it comes from, in a few words.
    """

    key: str
    kind: str
    source: str
    host_path: Path | None
    exists: bool
    declared_by: str

    @property
    def slug(self) -> str:
        """
        Returns:
            The target as a name that is safe in a file name and a remote folder.
        """
        text = _SLUG_JUNK.sub("-", f"{'vol' if self.kind == 'docker' else 'path'}-{self.source}")
        return text.strip("-")[:100] or "data"

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The target as plain data, JSON-ready.
        """
        return {
            "key": self.key,
            "kind": self.kind,
            "source": self.source,
            "path": str(self.host_path) if self.host_path else None,
            "exists": self.exists,
            "declared_by": self.declared_by,
            "slug": self.slug,
        }


@dataclass
class VolumeBackupMetadata:
    """
    What a volume backup's sidecar says.

    Attributes:
        id: ``<application>.<target slug>_<YYYYMMDD>_<HHMMSS>``.
        domain: The application.
        app_name: Its name, which names the directory.
        target: The target's key.
        kind: ``path`` or ``docker``.
        source: The relative path or the volume's name.
        slug: The target as a file name.
        created_at: When, UTC, ISO 8601.
        size_bytes: The archive's size.
        checksum: Its SHA-256.
        consistency: The mode it was taken under.
        origin: :func:`~noust.managers.backup_manager.server_id` of the server.
        tags: ``scheduled`` and ``auto`` for a schedule's, ``safety`` for a restore's.
        description: A note.
        is_file: The target is one file, not a directory.
        sqlite_files: SQLite databases inside it that were snapshotted whole.
        skipped: What was left out and why (a link out of the data, a socket).
        owner: ``{"uid", "gid"}`` of the data root, put back by a restore.
        remote_copies: Where it was sent: ``{"destination", "folder", "pushed_at"}``.
        last_verified_at: When it was last checked.
        verified_ok: Whether that check passed; None before one.
        mac: HMAC of the sidecar under this server's key.
        mac_key_id: Which key.
    """

    id: str
    domain: str
    app_name: str
    target: str
    kind: str
    source: str
    slug: str
    created_at: str
    size_bytes: int
    checksum: str | None
    consistency: str = "live"
    origin: str | None = None
    tags: list[str] = field(default_factory=list)
    description: str = ""
    is_file: bool = False
    sqlite_files: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    owner: dict[str, int] | None = None
    remote_copies: list[dict[str, Any]] = field(default_factory=list)
    last_verified_at: str | None = None
    verified_ok: bool | None = None
    mac: str | None = None
    mac_key_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The metadata as plain data, JSON-ready.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VolumeBackupMetadata:
        """
        Read a sidecar.

        Args:
            data: The parsed JSON.

        Returns:
            The metadata; fields this version does not know are ignored.

        Raises:
            KeyError: A field every sidecar has is missing.
        """
        known = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in data.items() if key in known})

    @property
    def scheduled(self) -> bool:
        """Whether a schedule took it: the only kind a schedule's retention deletes."""
        return SCHEDULED_TAG in self.tags


@dataclass
class VolumePolicyView:
    """
    An application's policy with the state of its timer.

    Attributes:
        domain: The application.
        policy: The stored policy, or None.
        timer: What systemd says: ``installed``, ``next_run``, ``last_run``.
        destinations: What each destination named now is: ``{"exists", "encrypted"}``.
    """

    domain: str
    policy: VolumePolicy | None = None
    timer: dict[str, Any] = field(default_factory=dict)
    destinations: dict[str, dict[str, bool]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            ``configured`` and, when it is, every field of the policy, plus ``timer``.
        """
        data: dict[str, Any] = {
            "domain": self.domain,
            "configured": self.policy is not None,
            "timer": dict(self.timer),
        }
        if self.policy is not None:
            stored = self.policy.to_dict()
            stored.pop("id", None)
            stored.pop("app_domain", None)
            stored["destinations"] = [
                {
                    **entry,
                    **self.destinations.get(entry["name"], {"exists": False, "encrypted": False}),
                }
                for entry in stored["destinations"]
            ]
            data.update(stored)
        return data


class VolumeBackupNotFound(BackupError):
    """There is no such application or no such volume backup (a 404 to the API)."""


def _limit(value: Any, ceiling: int, name: str) -> int | None:
    """
    Check a retention value.

    Args:
        value: What the caller gave.
        ceiling: The most a policy accepts.
        name: The field, for the error.

    Returns:
        The value, or None when the caller gave none.

    Raises:
        ValidationError: When it is not a whole number from 1 to ``ceiling``.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= ceiling:
        raise ValidationError(
            f"{name} must be a whole number from 1 to {ceiling}",
            details="Leave it empty for no limit.",
            field=name,
        )
    return int(value)


class VolumeBackupManager:
    """
    Take, keep, send, check and restore the backups of an application's data.

    Construct one per command or request; it holds no state beyond its
    collaborators.
    """

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        logger: Logger | None = None,
        config: Config | None = None,
        store: NoustStore | None = None,
        scheduler: BackupScheduler | None = None,
        destinations: DestinationFileManager | None = None,
        services: ServiceManager | None = None,
        actor: audit.Actor | None = None,
    ) -> None:
        """
        Args:
            runner: Runs docker, systemctl and chown. The process-wide one when None.
            fs: Every change to the persistent tree goes through it. The process-wide one when None.
            logger: Where progress goes.
            config: The configuration.
            store: The store the policies and the applications are in.
            scheduler: Installs the timers.
            destinations: Sends and fetches files.
            services: Stops, starts and asks about the application's units.
            actor: Who acts, for the audit trail.
        """
        self._runner = runner
        self._fs = fs
        self.logger = logger or Logger(verbose=False)
        self.config = config or Config()
        self._store = store
        self._scheduler = scheduler
        self._destinations = destinations
        self._services = services
        self.actor = actor

    # ------------------------------------------------------------ plumbing

    @property
    def runner(self) -> CommandRunner:
        """The runner every process goes through."""
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """The filesystem every change goes through."""
        return self._fs or get_fs()

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    @property
    def records(self) -> VolumeRecords:
        """The policies table."""
        return VolumeRecords(self.store)

    @property
    def scheduler(self) -> BackupScheduler:
        """The scheduler that owns the timer units."""
        if self._scheduler is None:
            self._scheduler = BackupScheduler(runner=self._runner, fs=self._fs)
            self._scheduler.logger = self.logger
        return self._scheduler

    @property
    def destinations(self) -> DestinationFileManager:
        """The destinations, with the file verbs an archive needs."""
        if self._destinations is None:
            self._destinations = DestinationFileManager(runner=self._runner)
        return self._destinations

    @property
    def services(self) -> ServiceManager:
        """The units of the applications."""
        if self._services is None:
            self._services = ServiceManager(runner=self._runner)
        return self._services

    @property
    def root(self) -> Path:
        """``volumes`` under the backup directory."""
        return resolve_backup_directory(self.config.get("backup.directory")) / VOLUMES_DIR

    def _audit(self, event: str, domain: str, outcome: str = "ok", **details: Any) -> None:
        """
        Record a change in the audit trail.

        Args:
            event: A catalog name, such as ``volume_backup.create``.
            domain: The application it happened to.
            outcome: ``ok``, ``failure`` or ``denied``.
            **details: Anything else worth keeping. Never a credential.
        """
        audit.record(
            event,
            actor=self.actor,
            target=f"app:{domain}",
            outcome=outcome,
            details={key: value for key, value in details.items() if value is not None},
        )

    def _app(self, domain: str) -> App:
        """
        Find an application.

        Args:
            domain: Its domain.

        Returns:
            Its record.

        Raises:
            BackupError: When there is no such application.
        """
        app = self.store.get_app(validate_domain(domain))
        if app is None:
            raise VolumeBackupNotFound(
                f"Application not found: {domain}",
                details="Run 'noust list' to see what is deployed.",
            )
        return app

    def require_app(self, domain: str) -> App:
        """
        Find an application.

        Args:
            domain: Its domain.

        Returns:
            Its record.

        Raises:
            VolumeBackupNotFound: When there is no such application.
        """
        return self._app(domain)

    def _directory(self, app_name: str) -> Path:
        """
        Name an application's backup directory.

        Args:
            app_name: The application's name.

        Returns:
            ``volumes/<app name>`` under the backup directory.
        """
        return self.root / validate_app_name(app_name)

    # ------------------------------------------------------------- targets

    def _data_root(self, app: App) -> Path:
        """
        Where an application's persistent data lives on the host.

        Args:
            app: The application.

        Returns:
            ``shared/`` of a release application, the application directory in place.
        """
        root = app_root(app)
        return root / SHARED_DIR if app.layout == RELEASES else root

    def targets(self, domain: str, *, extra_paths: Sequence[str] = ()) -> list[VolumeTarget]:
        """
        List the data an application declares, and what a policy adds.

        Args:
            domain: The application.
            extra_paths: Data directories to add, relative to the data root.

        Returns:
            One target per persistent path, per added path and per named
            volume of a Compose stack.

        Raises:
            BackupError: When there is no such application.
            ValidationError: When an added path is not a path inside the data.
        """
        app = self._app(domain)
        root = self._data_root(app)
        found: dict[str, VolumeTarget] = {}

        def add_path(raw: str, declared_by: str) -> None:
            try:
                relative = persistent_path(raw)
            except NoustError as exc:
                raise ValidationError(
                    f"{raw!r} is not a path inside the application's data",
                    details="Give a path relative to where the data lives, such as "
                    "'uploads' or 'storage/app', without '..'.",
                    field="paths",
                ) from exc
            host = root / relative
            key = f"path:{relative.as_posix()}"
            found.setdefault(
                key,
                VolumeTarget(
                    key=key,
                    kind="path",
                    source=relative.as_posix(),
                    host_path=host,
                    exists=host.exists() and not host.is_symlink(),
                    declared_by=declared_by,
                ),
            )

        if app.layout == RELEASES:
            for raw in app.persistent_paths:
                add_path(raw, "persistent path")
        for raw in extra_paths:
            add_path(raw, "added to the policy")
        if app.app_type == "docker-compose":
            for name in BackupManager(runner=self._runner, fs=self._fs).docker_volumes(domain):
                if not DOCKER_VOLUME_NAME.fullmatch(name):
                    self.logger.warning(f"Skipping Docker volume with an unusable name: {name!r}")
                    continue
                key = f"volume:{name}"
                found.setdefault(
                    key, VolumeTarget(key, "docker", name, None, True, "Compose volume")
                )
        return list(found.values())

    def _select(self, available: list[VolumeTarget], wanted: Sequence[str]) -> list[VolumeTarget]:
        """
        Pick the targets a policy or a command asks for.

        Args:
            available: What the application declares.
            wanted: Keys; empty is all of them.

        Returns:
            The chosen targets.

        Raises:
            ValidationError: When a key names nothing the application has.
        """
        if not wanted:
            return available
        by_key = {target.key: target for target in available}
        unknown = [key for key in wanted if key not in by_key]
        if unknown:
            raise ValidationError(
                f"This application has no data target {unknown[0]!r}",
                details="Its targets are: "
                + (", ".join(by_key) or "none declared")
                + ". Add a directory with --path.",
                field="targets",
            )
        return [by_key[key] for key in dict.fromkeys(wanted)]

    # ------------------------------------------------------------ policies

    def get_policy(self, domain: str) -> VolumePolicyView:
        """
        Read an application's policy and the state of its timer.

        Args:
            domain: The application.

        Returns:
            The view; ``policy`` is None when it has none.
        """
        domain = validate_domain(domain)
        policy = self.records.policy(domain)
        timer: dict[str, Any] = {}
        states: dict[str, dict[str, bool]] = {}
        if policy is not None:
            if policy.enabled:
                timer = self.scheduler.volume_timer_state(domain)
            for entry in policy.destinations:
                record = self.destinations.get(entry["name"])
                states[entry["name"]] = {
                    "exists": record is not None,
                    "encrypted": bool(record and record.encrypted),
                }
        return VolumePolicyView(domain, policy, timer, states)

    def list_policies(self) -> list[VolumePolicyView]:
        """
        Returns:
            Every application's policy with its timer.
        """
        return [self.get_policy(policy.app_domain) for policy in self.records.policies()]

    def set_policy(
        self,
        domain: str,
        *,
        schedule: str = "daily",
        targets: Sequence[str] = (),
        extra_paths: Sequence[str] = (),
        retention_count: int | None = 7,
        retention_days: int | None = 30,
        destinations: Sequence[dict[str, Any]] = (),
        consistency: str = "live",
        enabled: bool = True,
    ) -> VolumePolicyView:
        """
        Create or replace an application's data backup policy, and its timer.

        The timer is installed first and the row written last: a policy in the
        store always has the timer it describes.

        Args:
            domain: The application.
            schedule: An alias (``hourly``, ``daily``, ``weekly``, ``monthly``)
                or a systemd calendar expression.
            targets: Target keys to back up; empty is every one the
                application declares.
            extra_paths: Data directories to add, relative to the data root.
            retention_count: Scheduled backups kept locally per target.
            retention_days: Days a scheduled backup is kept locally.
            destinations: Remote copies, ``{"name", "retention_count",
                "retention_days"}`` each.
            consistency: ``live``, ``pause`` or ``stop``.
            enabled: Whether the timer runs.

        Returns:
            The policy as stored, with its timer.

        Raises:
            ValidationError: When a value is unusable.
            BackupError: When the timer cannot be made or the application is unknown.
        """
        require_server_role("Volume backups")
        domain = self._app(domain).domain
        calendar = validate_calendar(schedule)
        if consistency not in CONSISTENCY_MODES:
            raise ValidationError(
                f"{consistency!r} is not a consistency mode",
                details=f"Use one of: {', '.join(CONSISTENCY_MODES)}.",
                field="consistency",
            )
        count = _limit(retention_count, MAX_RETENTION_COUNT, "retention_count")
        days = _limit(retention_days, MAX_RETENTION_DAYS, "retention_days")
        available = self.targets(domain, extra_paths=extra_paths)
        chosen = self._select(available, targets)
        if not available and not targets:
            raise ValidationError(
                f"{domain} declares no data to back up",
                details="Declare a persistent path when deploying (--persist PATH), or add a "
                "directory to this policy with --path.",
                field="targets",
            )
        checked = self._validated_destinations(destinations)
        if enabled:
            self.scheduler.install_volume_timer(domain, calendar)
        else:
            self.scheduler.remove_volume_timer(domain)
        self.records.save_policy(
            VolumePolicy(
                app_domain=domain,
                schedule=calendar,
                targets=[target.key for target in chosen] if targets else [],
                extra_paths=[str(persistent_path(raw)) for raw in extra_paths],
                retention_count=count,
                retention_days=days,
                destinations=checked,
                consistency=consistency,
                enabled=enabled,
            )
        )
        self._audit(
            "volume_backup.policy",
            domain,
            schedule=calendar,
            targets=list(targets) or "all",
            consistency=consistency,
            destinations=[entry["name"] for entry in checked],
            retention_count=count,
            retention_days=days,
            enabled=enabled,
        )
        return self.get_policy(domain)

    def remove_policy(self, domain: str) -> bool:
        """
        Remove an application's policy and its timer. Its backups stay.

        Args:
            domain: The application.

        Returns:
            Whether there was a policy.
        """
        domain = validate_domain(domain)
        self.scheduler.remove_volume_timer(domain)
        removed = self.records.delete_policy(domain)
        if removed:
            self._audit("volume_backup.policy.remove", domain)
        return removed

    def _validated_destinations(
        self, destinations: Sequence[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Check that every destination a policy names exists, and its retention.

        Args:
            destinations: ``{"name", "retention_count", "retention_days"}`` each.

        Returns:
            The same, normalised.

        Raises:
            ValidationError: When one does not exist, is named twice, or has a
                retention out of range.
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
                }
            )
        return checked

    # --------------------------------------------------------- consistency

    def _running_units(self, app: App) -> list[str]:
        """
        Name the units serving an application that are running now.

        Args:
            app: The application.

        Returns:
            Their names; empty when it is stopped or has no unit.
        """
        name = domain_to_app_name(app.domain)
        candidates = self.services.serving_units(app) or [name]
        running = []
        for unit in candidates:
            status = self.services.get_status(unit, require_managed=False)
            if bool(status.get("active", False)):
                running.append(unit)
        return running

    def _compose_containers(self, app: App) -> list[str]:
        """
        List the running containers of an application's Compose stack.

        Args:
            app: The application.

        Returns:
            Container ids; empty when none runs.
        """
        label = (
            f"com.docker.compose.project={app.compose_project}"
            if app.compose_project
            else f"com.docker.compose.project.working_dir={app.app_path}"
        )
        result = self.runner.run(
            ["docker", "ps", "-q", "--filter", f"label={label}", "--filter", "status=running"],
            timeout=_SIGNAL_TIMEOUT,
        )
        if not result.success:
            raise BackupError(
                f"Could not list the containers of {app.domain}",
                details=result.stderr.strip() or "Is Docker running?",
            )
        return result.stdout.split()

    @contextmanager
    def _quiesced(self, app: App, mode: str) -> Iterator[None]:
        """
        Keep an application still while its data is copied, and always let it go.

        Args:
            app: The application.
            mode: ``live``, ``pause`` or ``stop``.

        Yields:
            Nothing; the application is still for the duration of the block.

        Raises:
            BackupError: When it cannot be stilled (nothing is then copied), or
                cannot be started or thawed again afterwards.
        """
        if mode == "live":
            yield
            return
        compose = app.app_type == "docker-compose"
        if mode == "pause" and compose:
            containers = self._compose_containers(app)
            if containers:
                self._signal(["docker", "pause", *containers], f"pause {app.domain}")
            try:
                yield
            finally:
                if containers:
                    self._signal(["docker", "unpause", *containers], f"unpause {app.domain}")
            return
        units = self._running_units(app)
        if mode == "pause":
            for unit in units:
                self._signal(["systemctl", "freeze", f"{unit}.service"], f"freeze {unit}")
            try:
                yield
            finally:
                for unit in units:
                    self._signal(["systemctl", "thaw", f"{unit}.service"], f"thaw {unit}")
            return
        stopped: list[str] = []
        try:
            for unit in units:
                self.logger.info(f"Stopping {unit} for the copy")
                self.services.stop(unit)
                stopped.append(unit)
            yield
        finally:
            failed: list[str] = []
            for unit in stopped:
                try:
                    self.services.start(unit)
                except NoustError as exc:
                    failed.append(f"{unit}: {exc}")
            if failed:
                raise BackupError(
                    f"{app.domain} could not be started again after its data was copied",
                    details="\n".join(failed)
                    + f"\nStart it with: noust start {app.domain}. The backup itself is "
                    "unaffected.",
                )

    def _signal(self, argv: list[str], what: str) -> None:
        """
        Run a command that stills or releases an application.

        Args:
            argv: The command.
            what: What it does, for the error.

        Raises:
            BackupError: When it fails; its own words are the details.
        """
        result = self.runner.run(argv, timeout=_SIGNAL_TIMEOUT)
        if not result.success:
            raise BackupError(
                f"Could not {what}",
                details=(result.stderr or result.stdout).strip()
                or f"{argv[0]} exited with code {result.exit_code}.",
            )

    # ------------------------------------------------------------- archives

    @staticmethod
    def _is_sqlite(path: Path) -> bool:
        """
        Args:
            path: Anything found in a data directory.

        Returns:
            Whether it is a regular file that starts with SQLite's header. Only a
            regular file is ever opened: opening a FIFO waits for a writer for ever.
        """
        try:
            if not stat.S_ISREG(path.lstat().st_mode):
                return False
            with path.open("rb") as handle:
                return handle.read(len(_SQLITE_MAGIC)) == _SQLITE_MAGIC
        except OSError:
            return False

    @staticmethod
    def _snapshot(source: Path, destination: Path) -> None:
        """
        Copy a live SQLite database consistently, with SQLite's backup API.

        Args:
            source: The database, possibly in use in WAL mode.
            destination: Where the snapshot goes.

        Raises:
            sqlite3.Error: When the database cannot be read as one.
        """
        reader = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
        writer = sqlite3.connect(destination)
        try:
            reader.backup(writer)
        finally:
            writer.close()
            reader.close()

    @staticmethod
    def _link_stays_inside(link: Path, root: Path) -> bool:
        """
        Decide whether a symbolic link may go into an archive.

        A restore never writes a link that points out of the data (that is
        how an archive reaches a file it should not), so a backup does not
        carry one it could not give back.

        Args:
            link: The link.
            root: The data root.

        Returns:
            True when the link is relative and, followed lexically, stays in ``root``.
        """
        target = os.readlink(link)
        if os.path.isabs(target):
            return False
        resolved = os.path.normpath(os.path.join(link.parent, target))
        return Path(resolved).is_relative_to(root)

    def _write_path_archive(
        self, source: Path, archive: Path, scratch: Path
    ) -> tuple[list[str], list[dict[str, str]]]:
        """
        Tar a directory or a file into a gzipped archive, in process.

        SQLite databases are snapshotted with their own backup API and the
        snapshot goes in under the file's name; sockets, devices and links
        that leave the data are left out and said so.

        Args:
            source: The directory or the file.
            archive: The ``.tar.gz`` to write.
            scratch: Where snapshots are made.

        Returns:
            The SQLite databases snapshotted, and what was skipped with why.

        Raises:
            BackupError: When something cannot be read or written.
        """
        sqlite_files: list[str] = []
        skipped: list[dict[str, str]] = []
        counter = 0

        def add(
            tar: tarfile.TarFile, path: Path, arcname: str, root: Path, companions: set[str]
        ) -> None:
            nonlocal counter
            if path.name in companions:
                return
            info = tar.gettarinfo(str(path), arcname)
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                if not self._link_stays_inside(path, root):
                    skipped.append({"path": arcname, "reason": "a link that leaves the data"})
                    return
                tar.addfile(info)
                return
            if stat.S_ISDIR(mode):
                tar.addfile(info)
                return
            if not stat.S_ISREG(mode):
                skipped.append({"path": arcname, "reason": "not a regular file"})
                return
            if self._is_sqlite(path):
                counter += 1
                snapshot = scratch / f"{counter}.sqlite"
                try:
                    self._snapshot(path, snapshot)
                except sqlite3.Error as exc:
                    skipped.append(
                        {"path": arcname, "reason": f"not snapshotted, copied as it is: {exc}"}
                    )
                else:
                    info.size = snapshot.stat().st_size
                    with snapshot.open("rb") as handle:
                        tar.addfile(info, handle)
                    self.fs.remove(snapshot)
                    sqlite_files.append(arcname)
                    return
            with path.open("rb") as handle:
                tar.addfile(info, handle)

        try:
            with tarfile.open(archive, "w:gz", compresslevel=_COMPRESS_LEVEL) as tar:
                if source.is_file():
                    add(tar, source, source.name, source.parent, set())
                else:
                    for directory, dirnames, filenames in os.walk(source, followlinks=False):
                        current = Path(directory)
                        relative = current.relative_to(source)
                        databases = {name for name in filenames if self._is_sqlite(current / name)}
                        companions = {
                            f"{name}{suffix}" for name in databases for suffix in _SQLITE_COMPANIONS
                        }
                        dirnames.sort()
                        for name in dirnames:
                            full = current / name
                            if full.is_symlink():
                                add(tar, full, (relative / name).as_posix(), source, set())
                        if relative != Path("."):
                            add(tar, current, relative.as_posix(), source, set())
                        for name in sorted(filenames):
                            add(
                                tar,
                                current / name,
                                (relative / name).as_posix(),
                                source,
                                companions,
                            )
                        # A link to a directory is a member, not something to walk into.
                        dirnames[:] = [
                            name for name in dirnames if not (current / name).is_symlink()
                        ]
        except (OSError, tarfile.TarError) as exc:
            raise BackupError(
                f"Could not archive {source}",
                details=f"{exc}. Check that the data is readable and the disk has room.",
            ) from exc
        return sqlite_files, skipped

    def _write_docker_archive(self, volume: str, directory: Path, filename: str) -> None:
        """
        Tar a named volume into a file of ``directory``.

        Args:
            volume: The volume.
            directory: Where the archive is written.
            filename: Its name.

        Raises:
            BackupError: When Docker fails or writes nothing.
        """
        result = self.runner.run(
            docker_archive_argv(volume, directory, filename), timeout=_DOCKER_TIMEOUT
        )
        if not result.success:
            raise BackupError(
                f"Failed to archive the volume {volume}",
                details=(result.stderr or result.stdout).strip() or "Is Docker running?",
            )
        if not (directory / filename).is_file():
            raise BackupError(
                f"Docker reported success but produced no archive for the volume {volume}",
                details=f"Look for {directory / filename}.",
            )

    # -------------------------------------------------------------- create

    def _new_id(self, app_name: str, slug: str) -> str:
        """
        Name a backup after its application, its target and the moment.

        Args:
            app_name: The application's name.
            slug: The target's slug.

        Returns:
            The id, unused in the application's directory.
        """
        directory = self._directory(app_name)
        stamp = datetime.now(timezone.utc)
        while True:
            candidate = f"{app_name}.{slug}_{stamp:%Y%m%d_%H%M%S}"
            if not (directory / f"{candidate}{ARCHIVE_SUFFIX}").exists():
                return candidate
            stamp += timedelta(seconds=1)

    def _sidecar_text(self, metadata: VolumeBackupMetadata) -> str:
        """
        Sign a backup's metadata and render its sidecar.

        Args:
            metadata: The metadata; its ``mac`` fields are updated.

        Returns:
            The sidecar's JSON.
        """
        data = sign_sidecar(metadata.to_dict())
        metadata.mac, metadata.mac_key_id = data["mac"], data["mac_key_id"]
        return json.dumps(data, indent=2)

    def _write_sidecar(self, metadata: VolumeBackupMetadata) -> None:
        """
        Write a backup's sidecar, 0600.

        Args:
            metadata: What to write.
        """
        path = self._directory(metadata.app_name) / f"{metadata.id}.json"
        self.fs.write_text(path, self._sidecar_text(metadata), mode=SECRET_MODE)

    def plan(
        self,
        domain: str,
        *,
        targets: Sequence[str] = (),
        extra_paths: Sequence[str] = (),
        consistency: str = "live",
    ) -> list[VolumeTarget]:
        """
        Check what a backup of an application's data would take, changing nothing.

        What :meth:`create` asks first, so an API can refuse a mistake as an
        answer instead of a failed job.

        Args:
            domain: The application.
            targets: Keys to back up; empty is every declared target.
            extra_paths: Data directories to add.
            consistency: ``live``, ``pause`` or ``stop``.

        Returns:
            The targets that would be backed up.

        Raises:
            VolumeBackupNotFound: When there is no such application.
            ValidationError: When the mode or a target is not usable.
        """
        app = self._app(domain)
        if consistency not in CONSISTENCY_MODES:
            raise ValidationError(
                f"{consistency!r} is not a consistency mode",
                details=f"Use one of: {', '.join(CONSISTENCY_MODES)}.",
                field="consistency",
            )
        return self._select(self.targets(app.domain, extra_paths=extra_paths), targets)

    def create(
        self,
        domain: str,
        *,
        targets: Sequence[str] = (),
        extra_paths: Sequence[str] = (),
        consistency: str = "live",
        description: str = "",
        tags: Sequence[str] = (),
    ) -> list[VolumeBackupMetadata]:
        """
        Back an application's data up, one archive per target.

        The whole operation holds the application's lock and keeps it still
        once for every target. A target that does not exist yet is skipped
        and said so; when none can be backed up the operation fails.

        Args:
            domain: The application.
            targets: Keys to back up; empty is every declared target.
            extra_paths: Data directories to add, relative to the data root.
            consistency: ``live``, ``pause`` or ``stop``.
            description: A note kept with each backup.
            tags: Tags kept with each backup (``scheduled`` makes it a
                schedule's, which its retention may delete).

        Returns:
            The backups taken, in order. Empty in a rehearsal.

        Raises:
            ValidationError: When the mode or a target is not usable.
            BackupError: When nothing can be backed up, an archive cannot be
                written, or the application cannot be stilled or released.
            AppBusyError: Another operation holds the application's lock.
        """
        require_server_role("Volume backups")
        app = self._app(domain)
        chosen = self.plan(
            domain, targets=targets, extra_paths=extra_paths, consistency=consistency
        )
        present = [target for target in chosen if target.exists]
        for missing in (target for target in chosen if not target.exists):
            self.logger.warning(
                f"Nothing to back up at {missing.host_path}: {missing.key} does not exist yet"
            )
        if not present:
            raise BackupError(
                f"{app.domain} has no data to back up now",
                details="Its declared data directories do not exist yet. "
                + (
                    f"Chosen: {', '.join(t.key for t in chosen)}."
                    if chosen
                    else "None are declared."
                ),
            )
        if isinstance(self.fs, DryRunFileSystem):
            for target in present:
                self.logger.info(f"would back up {target.key} of {app.domain} ({consistency})")
            return []
        app_name = validate_app_name(domain_to_app_name(app.domain))
        directory = self._directory(app_name)
        self.fs.make_dir(self.root, mode=SECRET_DIR_MODE, parents=True)
        self.fs.make_dir(directory, mode=SECRET_DIR_MODE)
        taken: list[VolumeBackupMetadata] = []
        with app_lock(app.domain, "backup"), self._quiesced(app, consistency):
            for target in present:
                taken.append(
                    self._archive_target(app, app_name, target, consistency, description, tags)
                )
        return taken

    def _archive_target(
        self,
        app: App,
        app_name: str,
        target: VolumeTarget,
        consistency: str,
        description: str,
        tags: Sequence[str],
    ) -> VolumeBackupMetadata:
        """
        Archive one target and write its sidecar.

        Args:
            app: The application.
            app_name: Its name.
            target: What to archive.
            consistency: The mode it is taken under, for the record.
            description: A note.
            tags: Tags to keep.

        Returns:
            The backup.

        Raises:
            BackupError: When the archive cannot be written.
        """
        directory = self._directory(app_name)
        backup_id = self._new_id(app_name, target.slug)
        archive = directory / f"{backup_id}{ARCHIVE_SUFFIX}"
        sqlite_files: list[str] = []
        skipped: list[dict[str, str]] = []
        owner: dict[str, int] | None = None
        is_file = False
        self.logger.info(f"Backing up {target.key}")
        try:
            if target.kind == "docker":
                self._write_docker_archive(target.source, directory, archive.name)
            else:
                if target.host_path is None:  # a path target always has one
                    raise BackupError(f"{target.key} has no path on this server")
                is_file = target.host_path.is_file()
                held = target.host_path.stat()
                owner = {"uid": held.st_uid, "gid": held.st_gid}
                with tempfile.TemporaryDirectory(prefix="noust-volume-", dir=directory) as scratch:
                    sqlite_files, skipped = self._write_path_archive(
                        target.host_path, archive, Path(scratch)
                    )
            self.fs.chmod(archive, SECRET_MODE)
            size = archive.stat().st_size
            checksum = file_sha256(archive)
        except (BackupError, OSError):
            self.fs.remove(archive)
            raise
        metadata = VolumeBackupMetadata(
            id=backup_id,
            domain=app.domain,
            app_name=app_name,
            target=target.key,
            kind=target.kind,
            source=target.source,
            slug=target.slug,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            size_bytes=size,
            checksum=checksum,
            consistency=consistency,
            origin=server_id(),
            tags=list(tags),
            description=description,
            is_file=is_file,
            sqlite_files=sqlite_files,
            skipped=skipped,
            owner=owner,
        )
        try:
            self._write_sidecar(metadata)
        except OSError as exc:
            self.fs.remove(archive)
            raise BackupError(
                f"Failed to write the metadata of {backup_id}", details=str(exc)
            ) from exc
        for entry in skipped:
            self.logger.warning(f"Left out {entry['path']}: {entry['reason']}")
        self._audit(
            "volume_backup.create",
            app.domain,
            id=backup_id,
            target=target.key,
            size=size,
            sha256=checksum,
            consistency=consistency,
            sqlite=len(sqlite_files) or None,
        )
        return metadata

    # --------------------------------------------------------------- reading

    def list_backups(
        self, domain: str | None = None, target: str | None = None
    ) -> list[VolumeBackupMetadata]:
        """
        List volume backups whose archive is present, newest first.

        Args:
            domain: Only this application's.
            target: Only this target's key.

        Returns:
            The backups.
        """
        directories: list[Path] = []
        if domain:
            directories = [self._directory(domain_to_app_name(validate_domain(domain)))]
        elif self.root.is_dir():
            directories = sorted(entry for entry in self.root.iterdir() if entry.is_dir())
        found: list[VolumeBackupMetadata] = []
        for directory in directories:
            if not directory.is_dir():
                continue
            for sidecar in sorted(directory.glob("*.json")):
                metadata = self._read(sidecar)
                if metadata is None or not (directory / f"{metadata.id}{ARCHIVE_SUFFIX}").is_file():
                    continue
                if target is not None and metadata.target != target:
                    continue
                found.append(metadata)
        return sorted(found, key=lambda entry: entry.created_at, reverse=True)

    def _read(self, sidecar: Path) -> VolumeBackupMetadata | None:
        """
        Read one sidecar.

        Args:
            sidecar: The ``.json`` file.

        Returns:
            The metadata, or None when it is unreadable or not one of ours.
        """
        try:
            metadata = VolumeBackupMetadata.from_dict(json.loads(sidecar.read_text()))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            _log.debug("Error reading %s: %s", sidecar, exc)
            return None
        return metadata if _ID.match(metadata.id) and sidecar.stem == metadata.id else None

    def get_backup(self, backup_id: str) -> VolumeBackupMetadata | None:
        """
        Find a backup by its id.

        Args:
            backup_id: The id.

        Returns:
            The metadata, or None.
        """
        if not _ID.match(backup_id):
            return None
        app_name = backup_id.split(".", 1)[0]
        sidecar = self.root / app_name / f"{backup_id}.json"
        metadata = self._read(sidecar) if sidecar.is_file() else None
        archive = self.root / app_name / f"{backup_id}{ARCHIVE_SUFFIX}"
        return metadata if metadata is not None and archive.is_file() else None

    def archive_path(self, metadata: VolumeBackupMetadata) -> Path:
        """
        Args:
            metadata: A backup.

        Returns:
            Where its archive is.
        """
        return self._directory(metadata.app_name) / f"{metadata.id}{ARCHIVE_SUFFIX}"

    def require(self, backup_id: str) -> VolumeBackupMetadata:
        """
        Find a backup or say how to list them.

        Args:
            backup_id: The id.

        Returns:
            The metadata.

        Raises:
            VolumeBackupNotFound: When there is no such backup.
        """
        metadata = self.get_backup(backup_id)
        if metadata is None:
            raise VolumeBackupNotFound(
                f"Volume backup not found: {backup_id}",
                details="List them with: noust backup volumes list",
            )
        return metadata

    # -------------------------------------------------------------- verify

    def verify(self, backup_id: str, *, deep: bool = False) -> dict[str, Any]:
        """
        Prove a backup intact: its digest, its MAC and that its archive reads through.

        Args:
            backup_id: The id.
            deep: Also extract it into a temporary directory with the hardened
                extractor a restore uses, which is the proof it can come back.

        Returns:
            ``valid``, ``errors``, ``warnings``, ``mac`` (the sidecar's MAC
            state) and ``deep``. The verdict is kept on the sidecar.

        Raises:
            BackupError: When there is no such backup.
        """
        metadata = self.require(backup_id)
        archive = self.archive_path(metadata)
        errors: list[str] = []
        warnings: list[str] = []
        sidecar = self._directory(metadata.app_name) / f"{metadata.id}.json"
        try:
            state = sidecar_mac_state(json.loads(sidecar.read_text()))
        except (OSError, ValueError):
            state = "invalid"
        if state == "invalid":
            errors.append(
                "The metadata does not match its MAC: the sidecar, or the checksum it records, "
                "was changed after the backup was taken"
            )
        elif state != "valid":
            warnings.append(f"The metadata's MAC could not be checked here ({state})")
        try:
            digest = file_sha256(archive)
        except OSError as exc:
            errors.append(f"The archive cannot be read: {exc}")
            digest = None
        if digest is not None and digest != metadata.checksum:
            errors.append(f"The archive's SHA-256 is {digest}; it was {metadata.checksum}")
        if not errors:
            try:
                with tarfile.open(archive, "r:gz") as tar:
                    for _ in tar:
                        pass
            except (tarfile.TarError, EOFError, OSError) as exc:
                errors.append(f"The archive does not read through: {exc}")
        if deep and not errors:
            with tempfile.TemporaryDirectory(
                prefix="noust-volume-verify-", dir=self._directory(metadata.app_name)
            ) as scratch:
                try:
                    extract_archive(
                        archive,
                        Path(scratch) / "extracted",
                        archive_format="tar.gz",
                        max_entries=MAX_ENTRIES,
                        max_total_bytes=MAX_BYTES,
                    )
                except SourceError as exc:
                    errors.append(f"It would not be extracted by a restore: {exc.message}")
        metadata.last_verified_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        metadata.verified_ok = not errors
        if state != "invalid":
            self._write_sidecar(metadata)
        self._audit(
            "volume_backup.verify",
            metadata.domain,
            "ok" if not errors else "failure",
            id=metadata.id,
            deep=deep,
        )
        return {
            "id": metadata.id,
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "mac": state,
            "deep": deep,
        }

    # -------------------------------------------------------------- delete

    def delete(self, backup_id: str) -> bool:
        """
        Delete a backup from this server. Copies on destinations stay.

        Args:
            backup_id: The id.

        Returns:
            True once it is gone.

        Raises:
            BackupError: When there is no such backup.
        """
        metadata = self.require(backup_id)
        self._remove(metadata, reason="operator")
        return True

    def _remove(self, metadata: VolumeBackupMetadata, *, reason: str) -> None:
        """
        Delete a backup's archive and sidecar.

        Args:
            metadata: The backup.
            reason: Why, for the audit trail: ``operator`` or ``retention``.
        """
        self.fs.remove(self.archive_path(metadata))
        self.fs.remove(self._directory(metadata.app_name) / f"{metadata.id}.json")
        self._audit("volume_backup.delete", metadata.domain, id=metadata.id, reason=reason)

    # ---------------------------------------------------------- destinations

    def remote_folder(self, metadata: VolumeBackupMetadata) -> str:
        """
        Name the folder a backup's target lives in on a destination.

        Args:
            metadata: A backup.

        Returns:
            ``volumes/<application>/<target slug>``.
        """
        return f"{REMOTE_ROOT}/{metadata.app_name}/{metadata.slug}"

    def push(
        self,
        backup_id: str,
        destination: str,
        *,
        scheduled: bool = False,
        retention_count: int | None = None,
        retention_days: int | None = None,
    ) -> dict[str, Any]:
        """
        Send a backup to a destination, after making sure it is a good one.

        A backup that fails its check is not sent: a bad copy on a destination
        is worse than none, because it is trusted.

        Args:
            backup_id: The id.
            destination: A destination created with ``noust backup destination add``.
            scheduled: Whether a schedule sends it, which makes it eligible
                for the destination's retention.
            retention_count: This server's scheduled backups of the target to
                keep there; None for no limit.
            retention_days: Maximum age in days of those; None for no limit.

        Returns:
            What the destination manager reports.

        Raises:
            BackupError: When there is no such backup, it fails its check, or
                the destination refuses it.
            ValidationError: When the destination does not exist.
        """
        metadata = self.require(backup_id)
        if self.destinations.get(destination) is None:
            raise ValidationError(
                f"Backup destination {destination!r} does not exist",
                details="Create it first with: noust backup destination add",
                field="destination",
            )
        verdict = self.verify(backup_id)
        if not verdict["valid"]:
            raise BackupError(
                f"{backup_id} failed its check and was not sent to {destination}",
                details="; ".join(verdict["errors"]),
            )
        metadata = self.require(backup_id)
        try:
            summary = self.destinations.push_file(
                self.archive_path(metadata),
                destination,
                self.remote_folder(metadata),
                # As it was signed, so a server holding the key can check it where
                # it lands; the destination's own fields (name, size, origin, and
                # these three) are outside what the MAC covers when it is read back.
                sidecar={
                    **json.loads(
                        (self._directory(metadata.app_name) / f"{metadata.id}.json").read_text()
                    ),
                    "version": 1,
                    "sha256": metadata.checksum,
                    "scheduled": scheduled,
                },
                retention_count=retention_count,
                retention_days=retention_days,
            )
        except NoustError:
            self._audit(
                "volume_backup.push",
                metadata.domain,
                "failure",
                id=backup_id,
                destination=destination,
            )
            raise
        metadata.remote_copies = [
            c for c in metadata.remote_copies if c.get("destination") != destination
        ] + [
            {
                "destination": destination,
                "folder": self.remote_folder(metadata),
                "pushed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "verified_by": summary.get("verified_by"),
            }
        ]
        self._write_sidecar(metadata)
        self._audit(
            "volume_backup.push",
            metadata.domain,
            id=backup_id,
            destination=destination,
            verified_by=summary.get("verified_by"),
        )
        return summary

    def remote_backups(self, destination: str, domain: str) -> dict[str, list[dict[str, Any]]]:
        """
        List what a destination holds of an application's data, by target.

        Args:
            destination: The destination.
            domain: The application.

        Returns:
            Each target's slug to its files, newest first, with their sidecars.

        Raises:
            BackupError: When the destination cannot be listed.
        """
        app_name = validate_app_name(domain_to_app_name(validate_domain(domain)))
        base = f"{REMOTE_ROOT}/{app_name}"
        listing: dict[str, list[dict[str, Any]]] = {}
        for slug in self.destinations.list_folders(destination, base):
            listing[slug] = [
                {
                    "name": entry.name,
                    "size": entry.size,
                    "modified": entry.modified,
                    "own": entry.own,
                    "target": (entry.sidecar or {}).get("target"),
                    "sha256": (entry.sidecar or {}).get("sha256"),
                }
                for entry in self.destinations.list_files(destination, f"{base}/{slug}")
                if entry.name.endswith(ARCHIVE_SUFFIX)
            ]
        return listing

    # ------------------------------------------------------------- retention

    def apply_retention(
        self,
        domain: str,
        *,
        retention_count: int | None,
        retention_days: int | None,
        protect: Sequence[str] = (),
    ) -> list[str]:
        """
        Delete an application's own scheduled backups beyond the limits, per target.

        Only backups a schedule took are ever considered: one taken by hand
        and the safety copy of a restore are not in the list, whatever their
        age, and the newest of a target is never deleted.

        Args:
            domain: The application.
            retention_count: Backups to keep per target; None for no limit.
            retention_days: Days to keep one; None for no limit.
            protect: Ids never deleted.

        Returns:
            The ids deleted.
        """
        if not retention_count and not retention_days:
            return []
        by_target: dict[str, list[VolumeBackupMetadata]] = {}
        for entry in self.list_backups(domain):
            if entry.scheduled and entry.id not in protect:
                by_target.setdefault(entry.target, []).append(entry)
        deleted: list[str] = []
        for entries in by_target.values():
            candidates = [(e.id, datetime.fromisoformat(e.created_at)) for e in entries]
            expired = select_expired(
                candidates,
                count=retention_count,
                days=retention_days,
                now=datetime.now(timezone.utc),
            )
            for entry in entries:
                if entry.id in expired:
                    self._remove(entry, reason="retention")
                    deleted.append(entry.id)
        return deleted

    # --------------------------------------------------------------- restore

    def restore(
        self,
        backup_id: str,
        *,
        safety_backup: bool = True,
        archive: Path | None = None,
        metadata: VolumeBackupMetadata | None = None,
    ) -> dict[str, Any]:
        """
        Put an application's data back as a backup held it.

        The data is replaced, not merged: what was written since goes (after
        the safety copy of it is taken). The application is stopped for the
        replacement and started afterwards through its health gate; when the
        gate fails the previous data is put back and the application started
        on it, and the error carries the gate's evidence.

        Args:
            backup_id: The id of a local backup.
            safety_backup: Archive the current data first. Turning it off
                leaves nothing to go back to when the restore fails.
            archive: A downloaded archive to restore instead of the local one
                (a restore from a destination); ``metadata`` must come with it.
            metadata: Its metadata.

        Returns:
            ``id``, ``target``, ``safety_backup`` (its id, if one was taken),
            ``started`` (the application was running and is again) and
            ``gate`` (what its health gate said).

        Raises:
            BackupError: When the backup is damaged, the restore fails, or the
                application does not pass its gate afterwards (the previous
                data is back when the error says so).
            AppBusyError: Another operation holds the application's lock.
        """
        require_server_role("Volume backups")
        found = metadata if metadata is not None else self.require(backup_id)
        source = archive if archive is not None else self.archive_path(found)
        app = self._app(found.domain)
        if archive is None:
            verdict = self.verify(found.id)
            if not verdict["valid"]:
                raise BackupError(
                    f"{found.id} failed its check; nothing was restored",
                    details="; ".join(verdict["errors"]),
                )
        elif file_sha256(source) != found.checksum:
            raise BackupError(
                f"The archive of {found.id} does not match its digest; nothing was restored"
            )
        wanted = next(
            (
                t
                for t in self.targets(app.domain, extra_paths=self._extra_for(found))
                if t.key == found.target
            ),
            None,
        )
        if wanted is None:
            raise BackupError(
                f"{app.domain} no longer declares {found.target}",
                details="Add it back (a persistent path, or --path in the policy) and restore "
                "again; nothing was restored.",
            )
        with app_lock(app.domain, "restore"):
            return self._restore_locked(app, found, wanted, source, safety_backup)

    def _extra_for(self, metadata: VolumeBackupMetadata) -> list[str]:
        """
        Args:
            metadata: A backup.

        Returns:
            The path of the backup's target as an added path, so a restore can
            find a data directory the policy added and the application does not declare.
        """
        return [metadata.source] if metadata.kind == "path" else []

    def _restore_locked(
        self,
        app: App,
        metadata: VolumeBackupMetadata,
        target: VolumeTarget,
        archive: Path,
        safety_backup: bool,
    ) -> dict[str, Any]:
        """
        Do what :meth:`restore` promises, with the application's lock held.

        Args:
            app: The application.
            metadata: The backup.
            target: Where it goes.
            archive: The archive.
            safety_backup: Archive the current data first.

        Returns:
            See :meth:`restore`.

        Raises:
            BackupError: See :meth:`restore`.
        """
        safety: VolumeBackupMetadata | None = None
        stopped: list[str] = []
        try:
            for unit in self._running_units(app):
                self.logger.info(f"Stopping {unit} for the restore")
                self.services.stop(unit)
                stopped.append(unit)
            if safety_backup and target.exists:
                # Stopped already, so what is copied is whole: the safety copy is
                # taken inside the window the restore needs anyway.
                self.logger.info(f"Taking a safety copy of {target.key} first")
                (safety,) = self.create(
                    app.domain,
                    targets=[target.key],
                    extra_paths=self._extra_for(metadata),
                    consistency="live",
                    description=f"Before restoring {metadata.id}",
                    tags=[SAFETY_TAG],
                )
            self._replace(app, metadata, target, archive)
        except NoustError as exc:
            self._start_again(stopped)
            kept = f" The safety copy is kept as {safety.id}." if safety else ""
            raise BackupError(
                f"Restoring {metadata.id} failed: {exc.message}",
                details=f"{exc.details}\nThe data was left as it was.{kept}".strip(),
            ) from exc
        gate = ""
        if stopped:
            ok, evidence = self._gate(app)
            gate = "passed" if ok else "failed"
            if not ok:
                raise self._after_failed_gate(app, metadata, target, safety, stopped, evidence)
        self._audit(
            "volume_backup.restore",
            app.domain,
            id=metadata.id,
            target=target.key,
            safety=safety.id if safety else None,
            gate=gate or None,
        )
        return {
            "id": metadata.id,
            "target": target.key,
            "safety_backup": safety.id if safety else None,
            "started": bool(stopped),
            "gate": gate or None,
        }

    def _after_failed_gate(
        self,
        app: App,
        metadata: VolumeBackupMetadata,
        target: VolumeTarget,
        safety: VolumeBackupMetadata | None,
        stopped: list[str],
        evidence: str,
    ) -> BackupError:
        """
        Put the previous data back after the restored data failed its health gate.

        Args:
            app: The application.
            metadata: The backup that was restored.
            target: Where it went.
            safety: The copy of what was there, if one was taken.
            stopped: The units the restore stopped.
            evidence: What the gate saw, verbatim.

        Returns:
            The error to raise, which says whether the previous data is back.
        """
        if safety is None:
            return BackupError(
                f"{app.domain} did not pass its health check on the restored data",
                details=evidence + "\n\nNo safety copy was taken, so the previous data is gone.",
            )
        try:
            for unit in stopped:
                self.services.stop(unit)
            self._replace(app, safety, target, self.archive_path(safety))
            self._start_again(stopped)
        except NoustError as exc:
            return BackupError(
                f"{app.domain} did not pass its health check on the restored data, and putting "
                "the previous data back failed too",
                details=f"{evidence}\n\n{exc}\nThe safety copy is kept as {safety.id}: "
                f"noust backup volumes restore {safety.id}",
            )
        return BackupError(
            f"{app.domain} did not pass its health check on the restored data",
            details=f"{evidence}\n\nThe data it had before was put back and it was started on "
            f"it. The backup is {metadata.id}; the safety copy {safety.id} is kept.",
        )

    def _start_again(self, units: list[str]) -> None:
        """
        Start units a restore stopped.

        Args:
            units: Their names.

        Raises:
            BackupError: When one cannot be started.
        """
        failed = []
        for unit in units:
            try:
                self.services.start(unit)
            except NoustError as exc:
                failed.append(f"{unit}: {exc}")
        if failed:
            raise BackupError(
                "The application could not be started again after the restore",
                details="\n".join(failed),
            )

    def _gate(self, app: App) -> tuple[bool, str]:
        """
        Start an application on what is there and ask whether it is up.

        Args:
            app: The application.

        Returns:
            Whether it is healthy, and the evidence when it is not.
        """
        from noust.deployers.lifecycle import health_gate_for

        return health_gate_for(app, self.store, self.logger).restart_and_probe()

    def _replace(
        self, app: App, metadata: VolumeBackupMetadata, target: VolumeTarget, archive: Path
    ) -> None:
        """
        Replace a target's data with an archive's contents.

        Args:
            app: The application.
            metadata: The backup, for the ownership it recorded.
            target: Where the data goes.
            archive: The archive.

        Raises:
            BackupError: When the archive cannot be extracted or the data cannot
                be swapped in (the previous data is put back first).
        """
        if target.kind == "docker":
            result = self.runner.run(
                docker_restore_argv(target.source, archive.parent, validate_filename(archive.name)),
                timeout=_DOCKER_TIMEOUT,
            )
            if not result.success:
                raise BackupError(
                    f"Failed to restore the volume {target.source}",
                    details=(result.stderr or result.stdout).strip(),
                )
            return
        if target.host_path is None:  # a path target always has one
            raise BackupError(f"{target.key} has no path on this server")
        destination = target.host_path
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        workspace = destination.parent / f".noust-restore-{destination.name}-{stamp}"
        try:
            self.fs.make_dir(destination.parent, mode=0o755, parents=True)
            self.fs.make_dir(workspace, mode=SECRET_DIR_MODE)
        except OSError as exc:
            raise BackupError(
                f"Could not create a restore workspace beside {destination}", details=str(exc)
            ) from exc
        extracted = workspace / "extracted"
        previous = workspace / "previous"
        swapped = False
        try:
            try:
                extract_archive(
                    archive,
                    extracted,
                    archive_format="tar.gz",
                    max_entries=MAX_ENTRIES,
                    max_total_bytes=MAX_BYTES,
                )
            except SourceError as exc:
                raise BackupError(f"Refusing to restore {archive.name}", details=str(exc)) from exc
            new_data = extracted / destination.name if metadata.is_file else extracted
            if metadata.is_file and not new_data.is_file():
                raise BackupError(f"{archive.name} does not hold the file {destination.name}")
            if destination.exists() or destination.is_symlink():
                self.fs.rename(destination, previous)
                swapped = True
            self.fs.rename(new_data, destination)
            self._hand_back(destination, metadata)
        except (BackupError, OSError) as exc:
            self._undo_swap(destination, previous, swapped, exc)
            self._clean(workspace)
            if isinstance(exc, BackupError):
                raise
            raise BackupError(f"Failed to restore {destination}", details=str(exc)) from exc
        self._clean(workspace)

    def _undo_swap(
        self, destination: Path, previous: Path, swapped: bool, failure: Exception
    ) -> None:
        """
        Put the data a failed restore moved aside back where it was.

        Args:
            destination: Where the data lives.
            previous: Where it was moved to.
            swapped: Whether it was moved.
            failure: What went wrong, for the error when this fails too.

        Raises:
            BackupError: When the previous data cannot be put back; it is then
                left at ``previous`` and the error says where.
        """
        if not swapped or not (previous.exists() or previous.is_symlink()):
            return
        try:
            if destination.is_dir() and not destination.is_symlink():
                self.fs.remove_tree(destination)
            elif destination.exists() or destination.is_symlink():
                self.fs.remove(destination)
            self.fs.rename(previous, destination)
        except OSError as exc:
            raise BackupError(
                f"Restoring {destination} failed and putting its data back failed too",
                details=f"{failure}\n{exc}\nThe data it had is at {previous}.",
            ) from exc

    def _hand_back(self, destination: Path, metadata: VolumeBackupMetadata) -> None:
        """
        Give a restored directory back to whoever owned it.

        The extractor writes as root and does not copy ownership, so the
        owner of the data root, recorded when the backup was taken, is put back.

        Args:
            destination: The restored data.
            metadata: The backup.
        """
        if not metadata.owner:
            return
        owner = f"{metadata.owner['uid']}:{metadata.owner['gid']}"
        result = self.runner.run(
            ["chown", "-R", owner, str(destination)], timeout=_SIGNAL_TIMEOUT * 10
        )
        if not result.success:
            self.logger.warning(
                f"Could not give {destination} back to {owner}; the application may fail with "
                f"EACCES: {result.stderr.strip()}"
            )

    def _clean(self, workspace: Path) -> None:
        """
        Remove a restore's workspace, which holds what the data was before.

        Args:
            workspace: The directory.
        """
        try:
            self.fs.remove_tree(workspace)
        except OSError as exc:
            self.logger.warning(
                f"The restore finished, but its workspace could not be removed: {exc}. "
                f"Remove {workspace} by hand."
            )

    def restore_remote(
        self,
        destination: str,
        domain: str,
        slug: str,
        file_name: str,
        *,
        safety_backup: bool = True,
    ) -> dict[str, Any]:
        """
        Download a backup from a destination, check it and restore it.

        Nothing is touched until the download matches the digest it was sent
        with and passes the MAC of its sidecar when this server has the key.

        Args:
            destination: The destination.
            domain: The application.
            slug: The target's folder there (see :meth:`remote_backups`).
            file_name: The archive's name there.
            safety_backup: Archive the current data first.

        Returns:
            What :meth:`restore` says.

        Raises:
            BackupError: When the download fails or does not match, or the restore fails.
        """
        domain = validate_domain(domain)
        app_name = validate_app_name(domain_to_app_name(domain))
        staging = self.root / ".remote-staging" / f"{int(time.time())}-{os.getpid()}"
        self.fs.make_dir(staging, mode=SECRET_DIR_MODE, parents=True)
        try:
            path, sidecar = self.destinations.download_file(
                destination, f"{REMOTE_ROOT}/{app_name}/{slug}", file_name, staging, fs=self.fs
            )
            if sidecar is None:
                raise BackupError(
                    f"{file_name} has no metadata on {destination}",
                    details="It was not sent by Noust, so what it holds and where it goes are "
                    "not known. Nothing was restored.",
                )
            # What the MAC covers is what Noust wrote; what the destination and the
            # sending added around it is not part of it.
            signed = {
                key: value
                for key, value in sidecar.items()
                if key in VolumeBackupMetadata.__dataclass_fields__
            }
            if sidecar_mac_state(signed) == "invalid":
                raise BackupError(
                    f"The metadata of {file_name} does not match its MAC; nothing was restored"
                )
            metadata = VolumeBackupMetadata.from_dict(signed)
            if metadata.domain != domain:
                raise BackupError(
                    f"{file_name} is the data of {metadata.domain}, not of {domain}",
                    details="Restore it into the application it belongs to.",
                )
            return self.restore(
                metadata.id,
                safety_backup=safety_backup,
                archive=path,
                metadata=metadata,
            )
        finally:
            try:
                self.fs.remove_tree(staging)
            except OSError as exc:
                self.logger.warning(f"Could not remove the download at {staging}: {exc}")

    # ------------------------------------------------------------ the policy

    def run_policy(
        self,
        domain: str,
        *,
        notify: bool = False,
        on_step: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """
        Do everything an application's policy promises: back up, check, send, prune.

        The order is the safety: a new archive is checked before anything is
        sent or deleted, so a backup that does not read can neither replace a
        good remote copy nor push an old one out. One destination failing does
        not stop the others. With no policy row (a store that moved while the
        timer kept firing) the data is still backed up, with no retention and
        no destinations.

        Args:
            domain: The application.
            notify: Announce a failure (and, when the operator switched it on,
                the success) through the notifier.
            on_step: Told what is happening, for a job's progress.

        Returns:
            ``backups`` (ids), ``destinations`` (``{"ok": ...}`` per
            destination and backup), ``local_deleted`` and ``policy_missing``.

        Raises:
            BackupError: When the backup fails, does not verify, or at least
                one destination could not be sent it; the local backup is
                kept either way.
            AppBusyError: Another operation held the application's lock for
                longer than a scheduled run waits.
        """
        domain = validate_domain(domain)
        policy = self.records.policy(domain)
        context = NotificationContext.from_config(self.config)

        def step(text: str) -> None:
            self.logger.info(text)
            if on_step:
                on_step(text)

        def announce(build: Callable[[], Any]) -> None:
            if notify:
                from noust.core.notifier import Notifier

                Notifier(self.config).notify(build())

        result: dict[str, Any] = {
            "domain": domain,
            "policy_missing": policy is None,
            "backups": [],
            "destinations": {},
            "local_deleted": [],
        }
        try:
            step(f"Backing up the data of {domain}")
            taken = self._create_when_free(domain, policy)
            result["backups"] = [entry.id for entry in taken]
            for entry in taken:
                days = verify_days(self.config)
                deep = days > 0 and self._verification_due(domain, entry, days)
                verdict = self.verify(entry.id, deep=deep)
                if not verdict["valid"]:
                    raise BackupError(
                        f"The backup {entry.id} failed its verification and was not sent",
                        details="; ".join(verdict["errors"]),
                    )
        except NoustError as exc:
            text = str(exc)
            self.records.record_run(domain, ok=False, error=text, backup=None)
            announce(lambda: compose_backup_failed(domain, text, context))
            raise

        failures: list[tuple[str, str]] = []
        for entry in taken:
            for destination in policy.destinations if policy else []:
                name = destination["name"]
                step(f"Sending {entry.id} to {name}")
                try:
                    summary = self.push(
                        entry.id,
                        name,
                        scheduled=True,
                        retention_count=destination.get("retention_count"),
                        retention_days=destination.get("retention_days"),
                    )
                    result["destinations"].setdefault(name, {})[entry.id] = {"ok": True, **summary}
                except NoustError as exc:
                    result["destinations"].setdefault(name, {})[entry.id] = {
                        "ok": False,
                        "error": str(exc),
                    }
                    failures.append((name, str(exc)))
                    announce(
                        lambda name=name, exc=exc: compose_backup_upload_failed(
                            domain, name, str(exc), context
                        )
                    )
        if policy:
            step("Applying local retention")
            result["local_deleted"] = self.apply_retention(
                domain,
                retention_count=policy.retention_count,
                retention_days=policy.retention_days,
            )
        last = taken[-1].id if taken else None
        if failures:
            error = BackupError(
                f"The data of {domain} was backed up but could not be sent to: "
                + ", ".join(sorted({name for name, _ in failures})),
                details="The local backups were kept.\n"
                + "\n".join(f"{name}: {text}" for name, text in failures),
            )
            self.records.record_run(domain, ok=False, error=str(error), backup=last)
            raise error
        self.records.record_run(domain, ok=True, error=None, backup=last)
        if taken:
            announce(
                lambda: compose_backup_completed(
                    domain,
                    ", ".join(entry.id for entry in taken),
                    context,
                    size_bytes=sum(entry.size_bytes for entry in taken),
                    destinations=list(result["destinations"]),
                )
            )
        return result

    def _create_when_free(
        self, domain: str, policy: VolumePolicy | None
    ) -> list[VolumeBackupMetadata]:
        """
        Take the scheduled backup, waiting for an operation that holds the lock.

        A deploy or an update running when the timer fires would otherwise cost
        the night's backup. The wait is bounded.

        Args:
            domain: The application.
            policy: Its policy, or None.

        Returns:
            The backups taken.

        Raises:
            AppBusyError: The lock was still held after the wait.
        """
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            try:
                return self.create(
                    domain,
                    targets=policy.targets if policy else (),
                    extra_paths=policy.extra_paths if policy else (),
                    consistency=policy.consistency if policy else "live",
                    tags=[SCHEDULED_TAG, "auto"],
                )
            except AppBusyError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(LOCK_RETRY_SECONDS)

    def _verification_due(self, domain: str, fresh: VolumeBackupMetadata, days: int) -> bool:
        """
        Whether a target's backups need a deep verification now.

        Args:
            domain: The application.
            fresh: The backup just taken.
            days: How old the newest good verification may be.

        Returns:
            True when no other backup of the target was verified good within ``days``.
        """
        horizon = datetime.now(timezone.utc) - timedelta(days=days)
        for other in self.list_backups(domain, fresh.target):
            if other.id == fresh.id or not other.verified_ok or not other.last_verified_at:
                continue
            if datetime.fromisoformat(other.last_verified_at) >= horizon:
                return False
        return True
