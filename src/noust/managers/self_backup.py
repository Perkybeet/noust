# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Noust's own state, in one encrypted file: ``noust self-backup``.

Everything Noust knows that is not in an application or a database: which
applications there are and how they deploy, the accounts and their second
factors, the destinations backups go to and the keys that open them, the
certificates it asked for, the units it wrote. Lose the server and, without
this, the applications' backups are files that nothing can place.

A bundle holds, consistent and in one ``.tar.gz`` sealed under a passphrase
(:mod:`noust.core.ens.envelope`: scrypt, AES-256-CBC by openssl, HMAC-SHA256,
no new dependency):

- the configuration (``/etc/noust``) and the state (``/var/lib/noust``): the
  store, the console's keys, the secrets (fleet keys, destination keys, the
  key that signs backup metadata), the TLS pair, the audit log. Every SQLite
  database is a snapshot taken with SQLite's backup API while Noust runs, never
  a copy of a file in use. What regenerates (the metrics and the observations)
  is left out, so the bundle stays small enough to send away every night;
- the certificates (``/etc/letsencrypt``) with the links certbot's ``live``
  folders are made of;
- the units Noust wrote (``/etc/systemd/system``: its own, every
  application's, their drop-ins), and which of them were enabled.

It holds what is needed to read its own secrets except the one thing that
cannot be stored beside them: the passphrase. A sealed store keeps its seal
header inside, so a bundle of a sealed central still asks for its sealing
passphrase after the bundle's own. Coolify's instance backup (a ``pg_dump`` of
its database) left out the key that decrypts it, which is why restoring one
failed with "MAC is invalid"; this is the lesson.

Files, links and what was left out are listed with their SHA-256 in a
``MANIFEST.json`` inside, and ``verify`` proves a bundle restorable without
restoring it: the envelope's MAC, every file's digest, the store's integrity
and that this version of Noust can open its schema.

Beside each bundle a signed sidecar says who took it and whether a schedule
did (only a schedule's bundles are deleted by its retention). A schedule keeps
the passphrase in the secret store: the bundles it sends away are protected
by what is not sent with them. Local copies are only as safe as the disk, and
the console says so.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
import shutil
import socket
import sqlite3
import stat
import tarfile
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from noust.central import backup as central_backup
from noust.core import audit, paths, sealing
from noust.core.config import SYSTEMD_DIR, Config, resolve_backup_directory
from noust.core.ens.envelope import EnvelopeError, open_envelope, read_header, seal_file
from noust.core.exceptions import BackupError, NoustError, SourceError, ValidationError
from noust.core.fs import SECRET_DIR_MODE, SECRET_MODE, FileSystem, get_fs
from noust.core.logger import Logger
from noust.core.notifications.composers import (
    compose_backup_completed,
    compose_backup_failed,
    compose_backup_upload_failed,
)
from noust.core.notifications.context import NotificationContext
from noust.core.runner import CommandRunner, get_runner
from noust.core.secrets import SecretStore
from noust.core.store import SCHEMA_VERSION, NoustStore, get_store
from noust.managers.backup_destination_files import DestinationFileManager, file_sha256
from noust.managers.backup_manager import server_id, sidecar_mac_state, sign_sidecar
from noust.managers.backup_scheduler import BackupScheduler, validate_calendar
from noust.managers.cert_manager import CertManager
from noust.managers.retention import select_expired
from noust.managers.self_backup_records import SelfBackupRecords, SelfBackupSchedule
from noust.managers.source_manager import extract_archive

__all__ = [
    "LABEL",
    "ROOT_NAME",
    "SECRET_NAME",
    "SelfBackup",
    "SelfBackupEntry",
    "SelfBackupManager",
    "SelfBackupNotFound",
    "default_directory",
]

_log = logging.getLogger(__name__)

#: The envelope's label, so a file says what it is without a passphrase.
LABEL = "noust-self-backup"

#: The top directory inside the archive.
ROOT_NAME = "noust-self"

#: The manifest's name inside it.
MANIFEST_NAME = central_backup.MANIFEST_NAME

#: Where a destination keeps them, then the server's name.
REMOTE_ROOT = "self-backups"

#: What a bundle's file name ends in.
SUFFIX = ".tar.gz.enc"

#: The secret the schedule's passphrase is kept under.
SECRET_NAME = "self-backup/passphrase"  # noqa: S105 - a secret's name, not a secret

#: History that regenerates, left out so a bundle stays small.
SKIP_FILES = frozenset({"metrics.db", "observations.db"})

#: Retention limits a schedule accepts.
MAX_RETENTION_COUNT = 365
MAX_RETENTION_DAYS = 3650

#: Members and bytes an extraction takes at most.
MAX_ENTRIES = 2_000_000
MAX_BYTES = 256 * 1024**3

#: Most units a bundle takes: a server runs hundreds, never tens of thousands.
_MAX_UNITS = 20_000

#: A bundle's file name inside the directory it lives in.
_NAME = re.compile(r"^noust-self-[A-Za-z0-9][A-Za-z0-9_.$-]*" + re.escape(SUFFIX) + r"$")

#: What a remote folder segment may be made of.
_SLUG = re.compile(r"[^A-Za-z0-9_$-]+")

#: The largest unit file a bundle takes: a unit is a few lines, never a quarter of a megabyte.
_UNIT_TEXT_LIMIT = 256 * 1024


class SelfBackupNotFound(BackupError):
    """There is no such bundle (a 404 to the API)."""


def default_directory(config: Config | None = None) -> Path:
    """
    Name where bundles are written.

    Args:
        config: The configuration; the server's when None.

    Returns:
        ``self`` under the backup directory.
    """
    return resolve_backup_directory((config or Config()).get("backup.directory")) / "self"


def _limit(value: Any, ceiling: int, name: str) -> int | None:
    """
    Check a retention value.

    Args:
        value: What the caller gave.
        ceiling: The most a schedule accepts.
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


@dataclass
class SelfBackup:
    """
    What a bundle is.

    Attributes:
        path: The encrypted file.
        size: Its size in bytes.
        sha256: Its SHA-256.
        manifest: What it holds: every file with its size and digest, what was
            left out and why, the links, the units enabled.
    """

    path: Path
    size: int
    sha256: str
    manifest: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The bundle as plain data, JSON-ready.
        """
        return {
            "path": str(self.path),
            "name": self.path.name,
            "size": self.size,
            "sha256": self.sha256,
            "files": len(self.manifest.get("files", [])),
            "links": len(self.manifest.get("links", [])),
            "units": len(self.manifest.get("enabled_units", [])),
            "skipped": self.manifest.get("skipped", []),
            "contains": self.manifest.get("contains", {}),
            "schema_version": self.manifest.get("schema_version"),
        }


@dataclass
class SelfBackupEntry:
    """
    A bundle on this server.

    Attributes:
        name: The file's name.
        path: Where it is.
        size: Bytes.
        created_at: When it was taken, from its envelope.
        host: The server that took it, from its name.
        scheduled: A schedule took it: the only kind its retention deletes.
        kind: ``manual``, ``scheduled`` or ``pre-restore``.
        sha256: Its digest, from its sidecar.
        remote_copies: Where it was sent.
        sidecar_mac: What the sidecar's MAC says (``valid``, ``invalid``, ``unsigned``...).
    """

    name: str
    path: Path
    size: int
    created_at: str | None
    host: str | None
    scheduled: bool
    kind: str
    sha256: str | None
    remote_copies: list[dict[str, Any]] = field(default_factory=list)
    sidecar_mac: str = "unsigned"

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The entry as plain data, JSON-ready.
        """
        data = asdict(self)
        data["path"] = str(self.path)
        return data


class SelfBackupManager:
    """
    Take, keep, send, check and schedule the bundle of Noust's own state.

    Restoring one is :mod:`noust.managers.self_restore`.
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
        secrets: SecretStore | None = None,
        actor: audit.Actor | None = None,
        directory: Path | None = None,
        sources: dict[str, Path] | None = None,
        letsencrypt: Path | None = None,
        systemd_dir: Path | None = None,
    ) -> None:
        """
        Args:
            runner: Runs openssl. The process-wide one when None.
            fs: Every change to the persistent tree goes through it.
            logger: Where progress goes.
            config: The configuration.
            store: The store the schedule is in.
            scheduler: Installs the timer.
            destinations: Sends and fetches files.
            secrets: Keeps the schedule's passphrase.
            actor: Who acts, for the audit trail.
            directory: Where bundles live; ``self`` under the backup directory by default.
            sources: What is backed up, by label (``config``, ``state``...); the
                server's own directories by default.
            letsencrypt: The certificates' directory.
            systemd_dir: Where the units are.
        """
        self._runner = runner
        self._fs = fs
        self.logger = logger or Logger(verbose=False)
        self.config = config or Config()
        self._store = store
        self._scheduler = scheduler
        self._destinations = destinations
        self._secrets = secrets
        self.actor = actor
        self._directory = directory
        self._sources = sources
        self.letsencrypt = letsencrypt if letsencrypt is not None else CertManager.LETSENCRYPT_DIR
        self.systemd_dir = systemd_dir if systemd_dir is not None else SYSTEMD_DIR

    # ------------------------------------------------------------ plumbing

    @property
    def runner(self) -> CommandRunner:
        """The runner openssl goes through."""
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
    def records(self) -> SelfBackupRecords:
        """The schedule's table."""
        return SelfBackupRecords(self.store)

    @property
    def scheduler(self) -> BackupScheduler:
        """The scheduler that owns the timer unit."""
        if self._scheduler is None:
            self._scheduler = BackupScheduler(runner=self._runner, fs=self._fs)
            self._scheduler.logger = self.logger
        return self._scheduler

    @property
    def destinations(self) -> DestinationFileManager:
        """The destinations, with the file verbs a bundle needs."""
        if self._destinations is None:
            self._destinations = DestinationFileManager(runner=self._runner)
        return self._destinations

    @property
    def secrets(self) -> SecretStore:
        """The secret store the schedule's passphrase is in."""
        if self._secrets is None:
            self._secrets = SecretStore()
        return self._secrets

    @property
    def directory(self) -> Path:
        """Where bundles live."""
        return self._directory if self._directory is not None else default_directory(self.config)

    def _audit(self, event: str, outcome: str = "ok", **details: Any) -> None:
        """
        Record a change in the audit trail.

        Args:
            event: A catalog name, such as ``self_backup.create``.
            outcome: ``ok``, ``failure`` or ``denied``.
            **details: Anything else worth keeping. Never the passphrase.
        """
        audit.record(
            event,
            actor=self.actor,
            target="noust:self",
            outcome=outcome,
            details={key: value for key, value in details.items() if value is not None},
        )

    def host_slug(self) -> str:
        """
        Returns:
            This server's name as a file name and a remote folder.
        """
        return _SLUG.sub("-", socket.gethostname()).strip("-") or "server"

    # ---------------------------------------------------------- the passphrase

    def stored_passphrase(self) -> str | None:
        """
        Read the passphrase a schedule seals its bundles under.

        Returns:
            The passphrase, or None when none is stored.

        Raises:
            SealError: The secret store is sealed and locked.
        """
        value = self.secrets.read(SECRET_NAME)
        return value.strip() if value else None

    def store_passphrase(self, passphrase: str) -> None:
        """
        Keep the passphrase a schedule seals its bundles under.

        Args:
            passphrase: At least the envelope's minimum length.

        Raises:
            ValidationError: When it is too short to protect anything.
        """
        if len(passphrase) < sealing.MIN_PASSPHRASE_LENGTH:
            raise ValidationError(
                f"The passphrase must have at least {sealing.MIN_PASSPHRASE_LENGTH} characters",
                details="Everything the bundle protects is as strong as its passphrase.",
                field="passphrase",
            )
        self.secrets.write(SECRET_NAME, passphrase)

    # --------------------------------------------------------- staging the files

    def _labels(self) -> dict[str, Path]:
        """
        Name the directories that are backed up.

        Returns:
            Label to directory: ``config``, ``state`` and, when the store is
            elsewhere, ``extra-N``.
        """
        if self._sources is not None:
            return dict(self._sources)
        return central_backup._labels(central_backup.default_sources())

    def restore_targets(self) -> dict[str, Path]:
        """
        Name where each part of a bundle goes on this server.

        Never the paths a bundle says it came from: a bundle of a server whose
        data directory was elsewhere still goes to this server's own.

        Returns:
            Label to this server's directory: ``config`` and ``state``
            whether or not they exist yet, any other directory this server
            backs up under the same label, ``letsencrypt`` and ``units``.
        """
        known = dict(self._labels())
        known.setdefault("config", paths.config_dir())
        known.setdefault("state", paths.state_dir())
        known["letsencrypt"] = self.letsencrypt
        known["units"] = self.systemd_dir
        return known

    def _stage_letsencrypt(self, staging: Path, manifest: dict[str, Any]) -> None:
        """
        Copy the certificates, and record the links certbot's ``live`` folders are made of.

        Args:
            staging: The archive's root directory.
            manifest: Filled with ``files``, ``links`` and ``skipped``.
        """
        root = self.letsencrypt
        if not root.is_dir() or root.is_symlink():
            return
        label = "letsencrypt"
        manifest["sources"][label] = str(root)
        for directory, dirnames, filenames in os.walk(root, followlinks=False):
            current = Path(directory)
            relative_dir = current.relative_to(root)
            target_dir = staging / label / relative_dir
            self.fs.make_dir(target_dir, mode=SECRET_DIR_MODE, parents=True)
            for name in sorted([*dirnames, *filenames]):
                full = current / name
                relative = (Path(label) / relative_dir / name).as_posix()
                if full.is_symlink():
                    target = os.readlink(full)
                    resolved = Path(os.path.normpath(os.path.join(full.parent, target)))
                    if os.path.isabs(target) or not resolved.is_relative_to(root):
                        manifest["skipped"].append(
                            {"path": relative, "reason": "link out of the tree"}
                        )
                    else:
                        manifest["links"].append({"path": relative, "target": target})
                    if name in dirnames:
                        # A link to a directory is a link, not a place to walk into.
                        dirnames.remove(name)
                    continue
                if name in filenames:
                    if not stat.S_ISREG(full.lstat().st_mode):
                        manifest["skipped"].append(
                            {"path": relative, "reason": "not a regular file"}
                        )
                        continue
                    destination = target_dir / name
                    shutil.copyfile(full, destination, follow_symlinks=False)
                    os.chmod(destination, SECRET_MODE)
                    manifest["files"].append(
                        {
                            "path": relative,
                            "size": destination.stat().st_size,
                            "sha256": file_sha256(destination),
                        }
                    )

    def _enabled_names(self) -> set[str]:
        """
        Name the units enabled by a symbolic link in a ``*.wants`` directory.

        Returns:
            Their file names.
        """
        enabled: set[str] = set()
        for wants in self.systemd_dir.glob("*.wants"):
            if not wants.is_dir():
                continue
            enabled.update(entry.name for entry in wants.iterdir() if entry.is_symlink())
        return enabled

    def _stage_units(self, staging: Path, manifest: dict[str, Any]) -> None:
        """
        Copy the units Noust wrote, their drop-ins, and note which were enabled.

        A unit is Noust's when it carries the marker Noust writes in every unit
        (:func:`noust.core.paths.carries_unit_marker`): a hand-written unit is
        the operator's, whatever it is called.

        Args:
            staging: The archive's root directory.
            manifest: Filled with ``files`` and ``enabled_units``.
        """
        if not self.systemd_dir.is_dir():
            return
        label = "units"
        manifest["sources"][label] = str(self.systemd_dir)
        enabled = self._enabled_names()
        found = 0
        target_dir = staging / label
        for suffix in ("service", "timer"):
            for unit in sorted(self.systemd_dir.glob(f"*.{suffix}")):
                if unit.is_symlink() or not unit.is_file() or found >= _MAX_UNITS:
                    continue
                try:
                    if unit.stat().st_size > _UNIT_TEXT_LIMIT:
                        continue
                    text = unit.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if not paths.carries_unit_marker(text):
                    continue
                found += 1
                self.fs.make_dir(target_dir, mode=SECRET_DIR_MODE, parents=True)
                destination = target_dir / unit.name
                self.fs.write_text(destination, text, mode=SECRET_MODE)
                manifest["files"].append(
                    {
                        "path": f"{label}/{unit.name}",
                        "size": destination.stat().st_size,
                        "sha256": file_sha256(destination),
                    }
                )
                if unit.name in enabled:
                    manifest["enabled_units"].append(unit.name)
                dropins = unit.with_name(f"{unit.name}.d")
                if dropins.is_dir() and not dropins.is_symlink():
                    for conf in sorted(dropins.glob("*.conf")):
                        if conf.is_symlink() or not conf.is_file():
                            continue
                        where = target_dir / dropins.name
                        self.fs.make_dir(where, mode=SECRET_DIR_MODE, parents=True)
                        copy = where / conf.name
                        shutil.copyfile(conf, copy, follow_symlinks=False)
                        os.chmod(copy, SECRET_MODE)
                        manifest["files"].append(
                            {
                                "path": f"{label}/{dropins.name}/{conf.name}",
                                "size": copy.stat().st_size,
                                "sha256": file_sha256(copy),
                            }
                        )

    @staticmethod
    def _contains(manifest: dict[str, Any]) -> dict[str, Any]:
        """
        Say what a bundle holds that a restore will need.

        Args:
            manifest: The bundle's manifest.

        Returns:
            ``store`` (the store's snapshot), ``secrets`` (any secret file),
            ``seal_header`` (a sealed store's header, so it can be opened with
            its passphrase), ``certificates`` and ``units``.
        """
        names = [entry["path"] for entry in manifest["files"]]
        return {
            "store": any(name.endswith(f"/{paths.STORE_NAME}") for name in names),
            "secrets": any("/secrets/" in name for name in names),
            "seal_header": any(name.endswith(f"/{sealing.SEAL_FILE}") for name in names),
            "certificates": any(name.startswith("letsencrypt/archive/") for name in names),
            "units": len(manifest["enabled_units"]),
        }

    def _store_version(self, snapshot: Path) -> int | None:
        """
        Read the schema version of a store snapshot.

        Args:
            snapshot: The snapshot file.

        Returns:
            The version, or None when it cannot be read.
        """
        try:
            connection = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True, timeout=30)
            try:
                row = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
            finally:
                connection.close()
        except sqlite3.Error:
            return None
        return int(row[0]) if row and row[0] is not None else None

    # ------------------------------------------------------------------ create

    def _destination(self, kind: str) -> Path:
        """
        Name a new bundle, one that does not exist yet.

        Args:
            kind: ``manual``, ``scheduled`` or ``pre-restore``.

        Returns:
            Its path.
        """
        stamp = datetime.now(timezone.utc)
        suffix = "-prerestore" if kind == "pre-restore" else ""
        while True:
            name = f"noust-self-{self.host_slug()}-{stamp:%Y%m%dT%H%M%SZ}{suffix}{SUFFIX}"
            if not (self.directory / name).exists():
                return self.directory / name
            stamp += timedelta(seconds=1)

    def create(self, passphrase: str, *, kind: str = "manual") -> SelfBackup:
        """
        Back Noust's own state up into one encrypted file.

        Args:
            passphrase: What the bundle is sealed under; at least 12
                characters, never written anywhere by this call. Without it the
                bundle is unreadable.
            kind: ``manual``, ``scheduled`` (a schedule's retention may delete
                it) or ``pre-restore`` (the copy a restore takes of what it
                replaces; never deleted by retention).

        Returns:
            What was written.

        Raises:
            EnvelopeError: The passphrase is too short, or openssl failed.
            BackupError: A file could not be read or the disk is full.
        """
        self.fs.make_dir(self.directory, mode=SECRET_DIR_MODE, parents=True)
        self.fs.chmod(self.directory, SECRET_DIR_MODE)
        from noust import __version__

        labels = self._labels()
        manifest: dict[str, Any] = {
            "format": LABEL,
            "version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "host": socket.gethostname(),
            "noust_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "sources": {label: str(path) for label, path in labels.items()},
            "files": [],
            "skipped": [],
            "links": [],
            "enabled_units": [],
        }
        exclude = {self.directory.resolve(), paths.backup_dir().resolve()}
        destination = self._destination(kind)
        try:
            with tempfile.TemporaryDirectory(
                prefix=".noust-self-backup-", dir=self.directory
            ) as scratch:
                staging = Path(scratch) / ROOT_NAME
                self.fs.make_dir(staging, mode=SECRET_DIR_MODE, parents=True)
                for label, directory in labels.items():
                    central_backup._copy_tree(
                        label, directory, staging, manifest, exclude, self.fs, SKIP_FILES
                    )
                self._stage_letsencrypt(staging, manifest)
                self._stage_units(staging, manifest)
                manifest["contains"] = self._contains(manifest)
                snapshot = next(
                    (
                        staging / entry["path"]
                        for entry in manifest["files"]
                        if entry["path"].endswith(f"/{paths.STORE_NAME}")
                    ),
                    None,
                )
                if snapshot is not None:
                    manifest["store_schema_version"] = self._store_version(snapshot)
                self.fs.write_text(
                    staging / MANIFEST_NAME,
                    json.dumps(manifest, indent=2, sort_keys=True),
                    mode=SECRET_MODE,
                )
                archive = Path(scratch) / "bundle.tar.gz"
                with tarfile.open(archive, "w:gz") as tar:
                    tar.add(staging, arcname=ROOT_NAME)
                info = seal_file(
                    archive,
                    destination,
                    passphrase,
                    runner=self.runner,
                    fs=self.fs,
                    label=LABEL,
                )
        except (OSError, sqlite3.Error, tarfile.TarError) as exc:
            raise BackupError(
                "Could not back Noust's own state up",
                details=f"{exc}. Check the disk space of {self.directory} and that every file "
                "of Noust's directories can be read.",
            ) from exc
        bundle = SelfBackup(path=info.path, size=info.size, sha256=info.sha256, manifest=manifest)
        self._write_sidecar(bundle, kind=kind)
        self._audit(
            "self_backup.create",
            name=info.path.name,
            size=info.size,
            sha256=info.sha256,
            files=len(manifest["files"]),
            kind=kind,
        )
        return bundle

    # ----------------------------------------------------------------- sidecars

    def _sidecar_path(self, bundle: Path) -> Path:
        """
        Args:
            bundle: A bundle's path.

        Returns:
            The path of the sidecar beside it.
        """
        return bundle.with_name(f"{bundle.name}.json")

    def _write_sidecar(
        self, bundle: SelfBackup | SelfBackupEntry, *, kind: str | None = None, **extra: Any
    ) -> None:
        """
        Write or update a bundle's signed sidecar.

        Args:
            bundle: The bundle.
            kind: ``manual``, ``scheduled`` or ``pre-restore``; kept when None.
            **extra: Fields to set (``remote_copies``...).
        """
        path = self._sidecar_path(bundle.path)
        data: dict[str, Any] = {}
        if path.is_file():
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                data = {}
        data.setdefault("format", LABEL)
        data.update(
            {
                "name": bundle.path.name,
                "size": bundle.size,
                "sha256": bundle.sha256,
                "origin": server_id(),
                "host": socket.gethostname(),
            }
        )
        if isinstance(bundle, SelfBackup):
            data["created_at"] = bundle.manifest.get("created_at")
            data["noust_version"] = bundle.manifest.get("noust_version")
            data["schema_version"] = bundle.manifest.get("schema_version")
        if kind is not None:
            data["kind"] = kind
        data["scheduled"] = data.get("kind") == "scheduled"
        data.setdefault("remote_copies", [])
        data.update(extra)
        data.pop("mac", None)
        data.pop("mac_key_id", None)
        self.fs.write_text(path, json.dumps(sign_sidecar(data), indent=2), mode=SECRET_MODE)

    # ------------------------------------------------------------------- listing

    def entries(self) -> list[SelfBackupEntry]:
        """
        List the bundles on this server, newest first.

        Returns:
            Each bundle with what its envelope and its sidecar say.
        """
        found: list[SelfBackupEntry] = []
        if not self.directory.is_dir():
            return found
        for path in sorted(self.directory.glob(f"*{SUFFIX}")):
            if path.is_symlink() or not path.is_file() or not _NAME.match(path.name):
                continue
            try:
                header = read_header(path)
            except EnvelopeError:
                continue
            if header.get("label") != LABEL:
                continue
            sidecar: dict[str, Any] = {}
            mac = "unsigned"
            sidecar_file = self._sidecar_path(path)
            if sidecar_file.is_file():
                try:
                    sidecar = json.loads(sidecar_file.read_text())
                    mac = sidecar_mac_state(sidecar)
                except (OSError, ValueError):
                    sidecar = {}
            kind = str(sidecar.get("kind") or "manual")
            found.append(
                SelfBackupEntry(
                    name=path.name,
                    path=path,
                    size=path.stat().st_size,
                    created_at=header.get("created_at"),
                    host=sidecar.get("host"),
                    scheduled=kind == "scheduled",
                    kind=kind,
                    sha256=sidecar.get("sha256"),
                    remote_copies=list(sidecar.get("remote_copies") or []),
                    sidecar_mac=mac,
                )
            )
        # The name carries the same moment to the second, so it breaks the tie of two bundles
        # sealed within one.
        return sorted(found, key=lambda entry: (entry.created_at or "", entry.name), reverse=True)

    def resolve(self, name_or_path: str) -> Path:
        """
        Find a bundle by the name this server listed it under, or by a path.

        Args:
            name_or_path: A name in :attr:`directory`, or a path to a bundle
                (one brought from another server).

        Returns:
            The bundle's path.

        Raises:
            SelfBackupNotFound: When there is no such file or it is not a bundle.
        """
        candidate = Path(name_or_path)
        path = candidate if len(candidate.parts) > 1 else self.directory / name_or_path
        if len(candidate.parts) == 1 and not _NAME.match(name_or_path):
            raise SelfBackupNotFound(
                f"{name_or_path!r} is not the name of a bundle",
                details="List them with: noust self-backup list",
            )
        if not path.is_file() or path.is_symlink():
            raise SelfBackupNotFound(
                f"No bundle at {path}",
                details="List them with: noust self-backup list, or give the whole path.",
            )
        try:
            header = read_header(path)
        except EnvelopeError as exc:
            raise SelfBackupNotFound(
                f"{path.name} is not a Noust bundle", details=exc.details
            ) from exc
        if header.get("label") != LABEL:
            raise SelfBackupNotFound(
                f"{path.name} is an envelope of something else ({header.get('label')!r})",
                details="A bundle of Noust's own state has the label " + LABEL + ".",
            )
        return path

    # ------------------------------------------------------------------- verify

    def open_bundle(
        self, path: Path, passphrase: str, scratch: Path
    ) -> tuple[Path, dict[str, Any]]:
        """
        Decrypt a bundle and extract it with the hardened extractor.

        Args:
            path: The bundle.
            passphrase: Its passphrase.
            scratch: A private directory to extract into.

        Returns:
            The extracted root directory and its manifest.

        Raises:
            EnvelopeError: Wrong passphrase, or the file was altered.
            BackupError: It does not hold a readable archive or manifest.
        """
        archive = scratch / "bundle.tar.gz"
        open_envelope(path, archive, passphrase, runner=self.runner, fs=self.fs)
        extracted = scratch / "extracted"
        try:
            extract_archive(
                archive,
                extracted,
                archive_format="tar.gz",
                max_entries=MAX_ENTRIES,
                max_total_bytes=MAX_BYTES,
            )
        except SourceError as exc:
            raise BackupError(
                f"{path.name} does not hold a safe archive", details=exc.message
            ) from exc
        self.fs.remove(archive)
        root = extracted / ROOT_NAME
        try:
            manifest = json.loads((root / MANIFEST_NAME).read_text())
        except (OSError, ValueError) as exc:
            raise BackupError(f"{path.name} has no readable manifest", details=str(exc)) from exc
        if manifest.get("format") != LABEL:
            raise BackupError(
                f"{path.name} holds {manifest.get('format')!r}, not Noust's own state",
                details="A central's bundle is checked with: noust central backup --verify",
            )
        return root, manifest

    def verify(self, name_or_path: str, passphrase: str) -> dict[str, Any]:
        """
        Prove a bundle restorable without restoring it.

        The envelope's MAC, every file against its digest, the store's own
        integrity check, and that this version of Noust can open its schema.

        Args:
            name_or_path: The bundle.
            passphrase: Its passphrase.

        Returns:
            ``ok``, ``files`` checked, ``problems``, ``warnings``, when and
            where it was taken, ``schema_version`` and whether this Noust can
            open it (``restorable_here``).

        Raises:
            EnvelopeError: Wrong passphrase, or the file was altered.
            BackupError: There is no such bundle, or it holds nothing readable.
        """
        path = self.resolve(name_or_path)
        problems: list[str] = []
        warnings: list[str] = []
        checked = 0
        with tempfile.TemporaryDirectory(
            prefix=".noust-self-verify-", dir=self._scratch_base()
        ) as scratch:
            root, manifest = self.open_bundle(path, passphrase, Path(scratch))
            for entry in manifest.get("files", []):
                file_path = root / str(entry.get("path", ""))
                if not file_path.is_file():
                    problems.append(f"{entry.get('path')} is missing")
                elif file_sha256(file_path) != entry.get("sha256"):
                    problems.append(f"{entry.get('path')} does not match its SHA-256")
                else:
                    checked += 1
            version: int | None = None
            snapshot = self._snapshot_in(root, manifest)
            if snapshot is None:
                problems.append("The bundle holds no snapshot of the store")
            else:
                version, integrity = self._check_snapshot(snapshot)
                if integrity != "ok":
                    problems.append(f"The store's snapshot is damaged: {integrity}")
                if version is None:
                    problems.append("The store's snapshot has no schema version")
        restorable = version is not None and version <= SCHEMA_VERSION
        if version is not None and version > SCHEMA_VERSION:
            warnings.append(
                f"The store is schema v{version}; this Noust understands up to v{SCHEMA_VERSION}. "
                "Restore it with the Noust version that took it, or a newer one."
            )
        if not manifest.get("contains", {}).get("secrets"):
            warnings.append("The bundle holds no secrets: nothing was in the secret store")
        report = {
            "ok": not problems,
            "name": path.name,
            "files": checked,
            "problems": problems,
            "warnings": warnings,
            "created_at": manifest.get("created_at"),
            "host": manifest.get("host"),
            "noust_version": manifest.get("noust_version"),
            "schema_version": version,
            "restorable_here": restorable and not problems,
            "contains": manifest.get("contains", {}),
        }
        self._audit(
            "self_backup.verify",
            "ok" if report["ok"] else "failure",
            name=path.name,
            problems=problems[:5] or None,
        )
        if self.records.get() is not None:
            self.records.record_verification(ok=bool(report["ok"]))
        return report

    def _scratch_base(self) -> Path:
        """
        Returns:
            A private directory beside the bundles, made when missing, for what
            is decrypted: on the disk sized for them, never in ``/tmp``.
        """
        self.fs.make_dir(self.directory, mode=SECRET_DIR_MODE, parents=True)
        return self.directory

    @staticmethod
    def _snapshot_in(root: Path, manifest: dict[str, Any]) -> Path | None:
        """
        Find the store's snapshot inside an extracted bundle.

        Args:
            root: The extracted root.
            manifest: Its manifest.

        Returns:
            The snapshot, or None.
        """
        for entry in manifest.get("files", []):
            if str(entry.get("path", "")).endswith(f"/{paths.STORE_NAME}"):
                return root / str(entry["path"])
        return None

    @staticmethod
    def _check_snapshot(snapshot: Path) -> tuple[int | None, str]:
        """
        Open a store snapshot and ask SQLite whether it is whole.

        Args:
            snapshot: The file.

        Returns:
            Its schema version (None when unreadable) and what
            ``PRAGMA integrity_check`` said (``ok`` when it is).
        """
        try:
            connection = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True, timeout=30)
            try:
                verdict = connection.execute("PRAGMA integrity_check").fetchone()
                row = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            return None, str(exc)
        return (
            int(row[0]) if row and row[0] is not None else None,
            str(verdict[0]) if verdict else "no answer",
        )

    # ------------------------------------------------------------------- delete

    def delete(self, name: str) -> bool:
        """
        Delete a bundle and its sidecar from this server. Copies on destinations stay.

        Args:
            name: The bundle's name here.

        Returns:
            True once it is gone.

        Raises:
            BackupError: When there is no such bundle.
        """
        path = self.resolve(name)
        if path.parent.resolve() != self.directory.resolve():
            raise BackupError(
                f"{path} is not in {self.directory}",
                details="Only a bundle this server keeps can be deleted here.",
            )
        self.fs.remove(path)
        self.fs.remove(self._sidecar_path(path))
        self._audit("self_backup.delete", name=path.name)
        return True

    # ------------------------------------------------------------- destinations

    def remote_folder(self) -> str:
        """
        Returns:
            Where this server's bundles live on a destination.
        """
        return f"{REMOTE_ROOT}/{self.host_slug()}"

    def push(
        self,
        name_or_path: str,
        destination: str,
        *,
        retention_count: int | None = None,
        retention_days: int | None = None,
    ) -> dict[str, Any]:
        """
        Send a bundle to a destination.

        The bundle is encrypted by its own passphrase, which is never sent, so a
        destination that is not encrypted still holds nothing readable.

        Args:
            name_or_path: The bundle.
            destination: A destination created with ``noust backup destination add``.
            retention_count: This server's scheduled bundles to keep there.
            retention_days: Maximum age in days of those.

        Returns:
            What the destination manager reports.

        Raises:
            BackupError: When there is no such bundle or the destination refuses it.
            ValidationError: When the destination does not exist.
        """
        path = self.resolve(name_or_path)
        if self.destinations.get(destination) is None:
            raise ValidationError(
                f"Backup destination {destination!r} does not exist",
                details="Create it first with: noust backup destination add",
                field="destination",
            )
        sidecar_file = self._sidecar_path(path)
        sidecar: dict[str, Any] = {}
        if sidecar_file.is_file():
            try:
                sidecar = json.loads(sidecar_file.read_text())
            except (OSError, ValueError):
                sidecar = {}
        digest = file_sha256(path)
        try:
            summary = self.destinations.push_file(
                path,
                destination,
                self.remote_folder(),
                sidecar={
                    **sidecar,
                    "version": 1,
                    "sha256": digest,
                    "scheduled": bool(sidecar.get("scheduled")),
                },
                retention_count=retention_count,
                retention_days=retention_days,
            )
        except NoustError:
            self._audit("self_backup.push", "failure", name=path.name, destination=destination)
            raise
        copies = [
            c for c in sidecar.get("remote_copies", []) if c.get("destination") != destination
        ]
        copies.append(
            {
                "destination": destination,
                "folder": self.remote_folder(),
                "pushed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "verified_by": summary.get("verified_by"),
            }
        )
        if sidecar_file.is_file():
            entry = next((e for e in self.entries() if e.path == path), None)
            if entry is not None:
                self._write_sidecar(entry, remote_copies=copies)
        self._audit(
            "self_backup.push",
            name=path.name,
            destination=destination,
            verified_by=summary.get("verified_by"),
        )
        return summary

    def remote_entries(self, destination: str, host: str | None = None) -> list[dict[str, Any]]:
        """
        List the bundles a destination holds of one server, newest first.

        Args:
            destination: The destination.
            host: The server's name; this one by default.

        Returns:
            ``name``, ``size``, ``modified``, ``own`` and ``scheduled`` each.

        Raises:
            BackupError: When the destination cannot be listed.
        """
        folder = f"{REMOTE_ROOT}/{_SLUG.sub('-', host).strip('-') if host else self.host_slug()}"
        return [
            {
                "name": entry.name,
                "size": entry.size,
                "modified": entry.modified,
                "own": entry.own,
                "scheduled": bool((entry.sidecar or {}).get("scheduled")),
            }
            for entry in self.destinations.list_files(destination, folder)
            if _NAME.match(entry.name)
        ]

    def remote_hosts(self, destination: str) -> list[str]:
        """
        List the servers that sent bundles to a destination.

        Args:
            destination: The destination.

        Returns:
            Their folder names there.
        """
        return self.destinations.list_folders(destination, REMOTE_ROOT)

    def download(self, destination: str, name: str, host: str, into: Path) -> Path:
        """
        Fetch a bundle from a destination, checked against the digest it was sent with.

        Args:
            destination: The destination.
            name: The bundle's name there.
            host: The server's folder there.
            into: A directory to put it in.

        Returns:
            The downloaded file.

        Raises:
            BackupError: When the download fails or does not match its digest.
        """
        if not _NAME.match(name):
            raise BackupError(f"{name!r} is not the name of a bundle")
        path, _ = self.destinations.download_file(
            destination,
            f"{REMOTE_ROOT}/{_SLUG.sub('-', host).strip('-')}",
            name,
            into,
            fs=self.fs,
        )
        return path

    # --------------------------------------------------------------- retention

    def apply_retention(
        self, *, retention_count: int | None, retention_days: int | None
    ) -> list[str]:
        """
        Delete this server's scheduled bundles beyond the limits.

        A bundle taken by hand and the copy a restore takes of what it replaces
        are never in the list, whatever their age, and the newest is never deleted.

        Args:
            retention_count: Bundles to keep; None for no limit.
            retention_days: Days to keep one; None for no limit.

        Returns:
            The names deleted.
        """
        if not retention_count and not retention_days:
            return []
        # By when the file was written, which is what a copy keeps and a listing shows.
        candidates = [
            (e.name, datetime.fromtimestamp(e.path.stat().st_mtime, tz=timezone.utc))
            for e in self.entries()
            if e.scheduled
        ]
        expired = select_expired(
            candidates,
            count=retention_count,
            days=retention_days,
            now=datetime.now(timezone.utc),
        )
        for name in expired:
            self.delete(name)
        return expired

    # ---------------------------------------------------------------- schedule

    def get_schedule(self) -> dict[str, Any]:
        """
        Read the schedule and the state of its timer.

        Returns:
            ``configured`` and, when it is, every field of the schedule, plus
            ``timer``, ``passphrase_stored`` and each destination's state.
        """
        schedule = self.records.get()
        data: dict[str, Any] = {"configured": schedule is not None, "timer": {}}
        try:
            data["passphrase_stored"] = self.stored_passphrase() is not None
        except NoustError:
            data["passphrase_stored"] = None
        if schedule is None:
            return data
        data.update(schedule.to_dict())
        if schedule.enabled:
            data["timer"] = self.scheduler.self_backup_timer_state()
        data["destinations"] = [
            {
                **entry,
                "exists": self.destinations.get(entry["name"]) is not None,
                "encrypted": bool(
                    (record := self.destinations.get(entry["name"])) and record.encrypted
                ),
            }
            for entry in schedule.destinations
        ]
        return data

    def set_schedule(
        self,
        *,
        schedule: str = "daily",
        retention_count: int | None = 7,
        retention_days: int | None = 30,
        destinations: Sequence[dict[str, Any]] = (),
        passphrase: str | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """
        Create or replace the schedule, and its timer.

        The passphrase is stored (in the secret store, 0600, sealed when the
        store is) so the timer can seal bundles unattended; it is asked only
        the first time and when it changes.

        Args:
            schedule: An alias (``hourly``, ``daily``, ``weekly``, ``monthly``)
                or a systemd calendar expression.
            retention_count: Scheduled bundles kept locally.
            retention_days: Days a scheduled bundle is kept locally.
            destinations: Remote copies, ``{"name", "retention_count",
                "retention_days"}`` each.
            passphrase: What bundles are sealed under; required while none is stored.
            enabled: Whether the timer runs.

        Returns:
            The schedule as :meth:`get_schedule` describes it.

        Raises:
            ValidationError: When a value is unusable, or no passphrase is known.
            BackupError: When the timer cannot be made.
        """
        calendar = validate_calendar(schedule)
        count = _limit(retention_count, MAX_RETENTION_COUNT, "retention_count")
        days = _limit(retention_days, MAX_RETENTION_DAYS, "retention_days")
        checked: list[dict[str, Any]] = []
        for entry in destinations:
            name = str(entry.get("name") or "")
            if not name or self.destinations.get(name) is None:
                raise ValidationError(
                    f"Backup destination {name!r} does not exist",
                    details="Create it first with: noust backup destination add",
                    field="destinations",
                )
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
        if passphrase is not None:
            self.store_passphrase(passphrase)
        elif self.stored_passphrase() is None:
            raise ValidationError(
                "A schedule needs a passphrase to seal its bundles under",
                details="Give one with the passphrase option; Noust keeps it in its secret "
                "store so the timer can run unattended. Keep a copy of your own: without it "
                "no bundle opens.",
                field="passphrase",
            )
        if enabled:
            self.scheduler.install_self_backup_timer(calendar)
        else:
            self.scheduler.remove_self_backup_timer()
        self.records.save(
            SelfBackupSchedule(
                schedule=calendar,
                retention_count=count,
                retention_days=days,
                destinations=checked,
                enabled=enabled,
            )
        )
        self._audit(
            "self_backup.schedule",
            schedule=calendar,
            retention_count=count,
            retention_days=days,
            destinations=[entry["name"] for entry in checked],
            enabled=enabled,
        )
        return self.get_schedule()

    def remove_schedule(self) -> bool:
        """
        Remove the schedule and its timer. The bundles stay, and so does the passphrase.

        Returns:
            Whether there was a schedule.
        """
        self.scheduler.remove_self_backup_timer()
        removed = self.records.delete()
        if removed:
            self._audit("self_backup.schedule.remove")
        return removed

    def run_schedule(
        self,
        *,
        notify: bool = False,
        on_step: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """
        Do everything the schedule promises: take a bundle, prove it, send it, prune.

        The order is the safety: the new bundle is proven restorable (its
        digests, its store's integrity) before anything is sent or deleted, so
        a bundle that would not restore can neither replace a good remote copy
        nor push an old one out. One destination failing does not stop the others.

        Args:
            notify: Announce a failure (and, when switched on, the success)
                through the notifier.
            on_step: Told what is happening, for a job's progress.

        Returns:
            ``file``, ``verification``, ``destinations`` and ``local_deleted``.

        Raises:
            ValidationError: When no passphrase is stored.
            BackupError: When the bundle fails, does not verify, or at least
                one destination could not be sent it; the local bundle is kept.
        """
        schedule = self.records.get() or SelfBackupSchedule(schedule="unknown")
        context = NotificationContext.from_config(self.config)

        def step(text: str) -> None:
            self.logger.info(text)
            if on_step:
                on_step(text)

        def announce(build: Callable[[], Any]) -> None:
            if notify:
                from noust.core.notifier import Notifier

                Notifier(self.config).notify(build())

        result: dict[str, Any] = {"destinations": {}, "local_deleted": [], "file": None}
        try:
            passphrase = self.stored_passphrase()
            if passphrase is None:
                raise ValidationError(
                    "No passphrase is stored for the scheduled backup of Noust's own state",
                    details="Set it again with: noust self-backup schedule set",
                    field="passphrase",
                )
            step("Backing up Noust's own state")
            bundle = self.create(passphrase, kind="scheduled")
            result["file"] = bundle.path.name
            step("Proving the bundle restorable")
            verdict = self.verify(bundle.path.name, passphrase)
            result["verification"] = verdict
            if not verdict["ok"]:
                raise BackupError(
                    f"The bundle {bundle.path.name} failed its verification and was not sent",
                    details="; ".join(verdict["problems"]),
                )
        except NoustError as exc:
            text = str(exc)
            if self.records.get():
                self.records.record_run(ok=False, error=text, file=result["file"])
            announce(lambda: compose_backup_failed(None, text, context))
            raise

        failures: list[tuple[str, str]] = []
        for entry in schedule.destinations:
            name = entry["name"]
            step(f"Sending {bundle.path.name} to {name}")
            try:
                summary = self.push(
                    bundle.path.name,
                    name,
                    retention_count=entry.get("retention_count"),
                    retention_days=entry.get("retention_days"),
                )
                result["destinations"][name] = {"ok": True, **summary}
            except NoustError as exc:
                result["destinations"][name] = {"ok": False, "error": str(exc)}
                failures.append((name, str(exc)))
                announce(
                    functools.partial(
                        compose_backup_upload_failed, "Noust", name, str(exc), context
                    )
                )
        step("Applying local retention")
        result["local_deleted"] = self.apply_retention(
            retention_count=schedule.retention_count, retention_days=schedule.retention_days
        )
        if failures:
            error = BackupError(
                "The backup of Noust's own state was taken but could not be sent to: "
                + ", ".join(name for name, _ in failures),
                details="The local bundle was kept.\n"
                + "\n".join(f"{name}: {text}" for name, text in failures),
            )
            if self.records.get():
                self.records.record_run(ok=False, error=str(error), file=bundle.path.name)
            raise error
        if self.records.get():
            self.records.record_run(ok=True, error=None, file=bundle.path.name)
        announce(
            lambda: compose_backup_completed(
                "Noust",
                bundle.path.name,
                context,
                size_bytes=bundle.size,
                destinations=list(result["destinations"]),
            )
        )
        return result
