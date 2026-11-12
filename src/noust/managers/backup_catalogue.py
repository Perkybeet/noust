# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every copy a server has, as the files a central can carry away (item 195).

The tunnel a central opens to a server goes one way on purpose (the central dials, nothing on
the server can reach back), so a server cannot send its copies anywhere: the central comes and
takes them (:mod:`noust.fleet.collect`). This is the server's half of that. It lists what there
is, by asking each kind's own manager and never reading a directory itself (rule 3), and it is the
only thing that decides which file of this server a central may read:

* **A central reads what the catalogue lists and nothing else.** A file is found by its kind, its
  copy's identifier and its name in the list; nothing a caller sends is joined to a path. A name
  that is not in the list is not found, whatever it looks like.
* **A copy is its files.** An application's or a volume's is an archive and the sidecar that says
  what it is (and carries a MAC under this server's key, which another server tells apart as
  somebody else's and still restores from); a database's is the dump; Noust's own is the bundle,
  which is encrypted under its passphrase whatever else is true of it, which is why it may leave.
* **The digest is the one Noust recorded when it made the file**, never one worked out now for the
  occasion: a dump of three gigabytes is not read again at every listing. A file that has none (a
  dump somebody imported by hand) says so, and the one who takes it records its own.
* **A link is not a file.** A path in the list that is a link, or is not a regular file, is left
  out: whatever it pointed at would be read as this server's root.

What a listing that fails for one kind does is say so and list the others: a copy of Noust's own
state is still worth taking when the database engines cannot be asked.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from noust.core.backup_files import is_sealed
from noust.core.exceptions import NoustError
from noust.core.utils import domain_to_app_name

if TYPE_CHECKING:
    from noust.managers.backup_manager import BackupManager
    from noust.managers.database.backups import DatabaseBackups
    from noust.managers.self_backup import SelfBackupManager
    from noust.managers.volume_backups import VolumeBackupManager

__all__ = [
    "KINDS",
    "KIND_APP",
    "KIND_DATABASE",
    "KIND_NOUST",
    "KIND_VOLUME",
    "ORIGINS",
    "BackupCatalogue",
    "Catalogue",
    "CopyFile",
    "CopyItem",
    "open_for_reading",
]

_log = logging.getLogger(__name__)

#: The kinds of copy there are: an application's, a database's, an application's data (its
#: volumes and paths) and the bundle of Noust's own state.
KIND_APP = "app"
KIND_DATABASE = "database"
KIND_VOLUME = "volume"
KIND_NOUST = "noust"
KINDS: tuple[str, ...] = (KIND_APP, KIND_DATABASE, KIND_VOLUME, KIND_NOUST)

#: Who made a copy, in the three words a retention needs: a schedule's (the only kind one deletes),
#: one made on purpose, and one a restore or an update made for its own safety.
ORIGINS: tuple[str, ...] = ("scheduled", "manual", "safety")

#: What a sidecar may weigh. It is a few kilobytes of JSON; a file that is not is not one.
MAX_SIDECAR_BYTES = 1024 * 1024

#: Tags of an application's or a volume's copy that say a restore or an update made it for its own
#: safety.
_SAFETY_TAGS = frozenset({"safety", "pre-deploy", "pre-restore", "pre-update"})

#: What listing one kind may fail with: the manager's own errors, the disk and the store.
_LISTING_ERRORS = (NoustError, OSError, sqlite3.Error)


@dataclass(frozen=True)
class CopyFile:
    """
    One file of a copy.

    Attributes:
        name: Its name, which is how a central asks for it and what it is called there.
        size: Its size in bytes now.
        sha256: The digest Noust recorded for it, or None when none was.
        role: ``archive`` (the copy), or ``sidecar`` (what says what it is; written again when the
            copy is verified, so it is the one file whose digest is not promised).
        path: Where it is on this server. Never part of what is listed.
    """

    name: str
    size: int
    sha256: str | None
    role: str
    path: Path = field(compare=False)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The file as a central sees it: no path.
        """
        return {"name": self.name, "size": self.size, "sha256": self.sha256, "role": self.role}


@dataclass(frozen=True)
class CopyItem:
    """
    One copy, as the files it is made of.

    Attributes:
        kind: One of :data:`KINDS`.
        id: Its identifier on this server, unique for its kind.
        owner: What it is a copy of, as a person reads it (``shop.example.com``,
            ``postgresql/shop``, ``noust``).
        folder: The same as one plain name, for a place on disk of a central's.
        created_at: When it was taken, UTC, ISO 8601.
        origin: One of :data:`ORIGINS`.
        sealed: Whether the file is encrypted where it lies.
        files: The files; the copy is the first.
        meta: Small facts a restore needs (the application, the engine and the database, the
            target).
        blocked: Why a central may not take it, or None: ``sealing_required`` when this server
            lets a copy leave only encrypted (the ``ens-medium`` profile, or ``backup.encryption:
            required``) and this one is not. Listed, so that the central can say why it is not
            taken, and refused where its files are read.
    """

    kind: str
    id: str
    owner: str
    folder: str
    created_at: str
    origin: str
    sealed: bool
    files: tuple[CopyFile, ...]
    meta: dict[str, str] = field(default_factory=dict)
    blocked: str | None = None

    @property
    def size(self) -> int:
        """Returns: What all its files weigh."""
        return sum(item.size for item in self.files)

    def file(self, name: str) -> CopyFile | None:
        """
        Args:
            name: A file's name as listed.

        Returns:
            The file, or None when the copy has none by that name.
        """
        return next((item for item in self.files if item.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The copy as a central sees it, JSON-ready and with no path in it.
        """
        return {
            "kind": self.kind,
            "id": self.id,
            "owner": self.owner,
            "folder": self.folder,
            "created_at": self.created_at,
            "origin": self.origin,
            "sealed": self.sealed,
            "size": self.size,
            "files": [item.to_dict() for item in self.files],
            "meta": dict(self.meta),
            "blocked": self.blocked,
        }


@dataclass(frozen=True)
class Catalogue:
    """
    What a server has.

    Attributes:
        items: Every copy, newest first within a kind.
        problems: A kind that could not be listed, and why: ``{"kind", "message"}``.
    """

    items: tuple[CopyItem, ...]
    problems: tuple[dict[str, str], ...] = ()

    @property
    def fingerprint(self) -> str:
        """
        Returns:
            A short digest of what is listed, which changes when a copy appears, goes or changes
            size: what a central compares to know whether there is anything to look at.
        """
        lines = sorted(
            f"{item.kind}|{item.id}|{item.files[0].size if item.files else 0}|{item.created_at}"
            for item in self.items
        )
        return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:24]


def _utc(value: str | datetime | None, fallback: Path | None = None) -> str:
    """
    Say when something was taken, in UTC.

    A time with no zone was written by this server in its own: it is read as that.

    Args:
        value: The moment as a manager records it.
        fallback: The file, whose own modification time stands in when the moment cannot be read.

    Returns:
        ISO 8601 in UTC, to the second.
    """
    moment: datetime | None = None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            moment = None
    if moment is None:
        try:
            moment = datetime.fromtimestamp(fallback.stat().st_mtime) if fallback else None
        except OSError:
            moment = None
    if moment is None:
        moment = datetime.fromtimestamp(0, timezone.utc)
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _regular(path: Path) -> os.stat_result | None:
    """
    Args:
        path: A file of a copy.

    Returns:
        Its status when it is a regular file that is not a link; None otherwise.
    """
    try:
        status = path.lstat()
    except OSError:
        return None
    return status if (status.st_mode & 0o170000) == 0o100000 else None


def _file(path: Path, role: str, sha256: str | None) -> CopyFile | None:
    """
    Describe a file of a copy, or leave it out when it is not one.

    Args:
        path: Where it is.
        role: ``archive`` or ``sidecar``.
        sha256: The digest Noust recorded for it, if any.

    Returns:
        The file, or None when it is a link, is missing or is not a regular file.
    """
    status = _regular(path)
    if status is None:
        return None
    if role == "sidecar" and status.st_size > MAX_SIDECAR_BYTES:
        return None
    return CopyFile(path.name, status.st_size, sha256 or None, role, path)


def _origin(tags: Iterable[str], *, scheduled: bool) -> str:
    """
    Args:
        tags: A copy's tags.
        scheduled: Whether a schedule took it.

    Returns:
        One of :data:`ORIGINS`.
    """
    if scheduled:
        return "scheduled"
    return "safety" if _SAFETY_TAGS & set(tags) else "manual"


class BackupCatalogue:
    """
    List the copies of this server, and find the file a central asks for.

    Every collaborator is the manager of its kind, made on first use; a test hands its own.

    Args:
        apps: The application backups.
        databases: The database dumps.
        volumes: The data backups of applications.
        bundles: The bundles of Noust's own state.
        sealing_required: Whether a copy may leave this server only encrypted; the server's own
            rule (:func:`noust.core.ens.profile.backup_encryption_required`) by default.
    """

    def __init__(
        self,
        *,
        apps: BackupManager | None = None,
        databases: DatabaseBackups | None = None,
        volumes: VolumeBackupManager | None = None,
        bundles: SelfBackupManager | None = None,
        sealing_required: Callable[[], bool] | None = None,
    ) -> None:
        self._apps = apps
        self._databases = databases
        self._volumes = volumes
        self._bundles = bundles
        self._sealing_required = sealing_required

    # ------------------------------------------------------------- collaborators

    @property
    def apps(self) -> BackupManager:
        """The application backups."""
        if self._apps is None:
            from noust.managers.backup_manager import BackupManager

            self._apps = BackupManager(verbose=False)
        return self._apps

    @property
    def databases(self) -> DatabaseBackups:
        """The database dumps."""
        if self._databases is None:
            from noust.managers.database.backups import DatabaseBackups
            from noust.managers.database.service import DatabaseService

            self._databases = DatabaseBackups(DatabaseService())
        return self._databases

    @property
    def volumes(self) -> VolumeBackupManager:
        """The data backups of applications."""
        if self._volumes is None:
            from noust.managers.volume_backups import VolumeBackupManager

            self._volumes = VolumeBackupManager()
        return self._volumes

    @property
    def bundles(self) -> SelfBackupManager:
        """The bundles of Noust's own state."""
        if self._bundles is None:
            from noust.managers.self_backup import SelfBackupManager

            self._bundles = SelfBackupManager()
        return self._bundles

    # ------------------------------------------------------------------- listing

    def build(self, kinds: Iterable[str] | None = None) -> Catalogue:
        """
        List the copies there are.

        Args:
            kinds: Only these kinds; every kind when None.

        Returns:
            What there is, and the kinds that could not be listed.
        """
        wanted = [kind for kind in KINDS if kinds is None or kind in set(kinds)]
        listers = {
            KIND_APP: self._apps_items,
            KIND_DATABASE: self._database_items,
            KIND_VOLUME: self._volume_items,
            KIND_NOUST: self._bundle_items,
        }
        items: list[CopyItem] = []
        problems: list[dict[str, str]] = []
        for kind in wanted:
            try:
                items.extend(listers[kind]())
            except _LISTING_ERRORS as exc:
                _log.warning("Could not list the %s copies for a central: %s", kind, exc)
                problems.append({"kind": kind, "message": str(exc)[:300]})
        if items and self._must_be_sealed():
            # The one place a copy is judged against the server's rule, so what is listed and what
            # is read cannot disagree (rule 4).
            items = [
                item if item.sealed else replace(item, blocked="sealing_required") for item in items
            ]
        return Catalogue(tuple(items), tuple(problems))

    def _must_be_sealed(self) -> bool:
        """
        Returns:
            Whether this server lets a copy leave only encrypted.
        """
        if self._sealing_required is not None:
            return self._sealing_required()
        from noust.core.ens.profile import backup_encryption_required

        return backup_encryption_required()

    def item(self, kind: str, item_id: str) -> CopyItem | None:
        """
        Find one copy by what a central was told it is called.

        Args:
            kind: One of :data:`KINDS`.
            item_id: Its identifier as listed.

        Returns:
            The copy, or None when this server does not list one by that name.
        """
        if kind not in KINDS:
            return None
        return next((it for it in self.build([kind]).items if it.id == item_id), None)

    def _apps_items(self) -> Iterator[CopyItem]:
        """
        Yields:
            Each application backup.
        """
        for backup in self.apps.list_backups():
            archive, sidecar = self.apps.local_files(backup)
            files = [
                _file(archive, "archive", backup.checksum),
                _file(sidecar, "sidecar", None),
            ]
            if files[0] is None:
                continue
            yield CopyItem(
                kind=KIND_APP,
                id=backup.id,
                owner=backup.domain,
                folder=domain_to_app_name(backup.domain),
                created_at=_utc(backup.created_at, archive),
                origin=_origin(backup.tags, scheduled="scheduled" in backup.tags),
                sealed=bool(is_sealed(archive)),
                files=tuple(item for item in files if item is not None),
                meta={"domain": backup.domain},
            )

    def _volume_items(self) -> Iterator[CopyItem]:
        """
        Yields:
            Each data backup of an application.
        """
        for backup in self.volumes.list_backups():
            archive = self.volumes.archive_path(backup)
            sidecar = archive.with_name(f"{backup.id}.json")
            files = [
                _file(archive, "archive", backup.checksum),
                _file(sidecar, "sidecar", None),
            ]
            if files[0] is None:
                continue
            yield CopyItem(
                kind=KIND_VOLUME,
                id=backup.id,
                owner=backup.domain,
                folder=domain_to_app_name(backup.domain),
                created_at=_utc(backup.created_at, archive),
                origin=_origin(backup.tags, scheduled=backup.scheduled),
                sealed=bool(is_sealed(archive)),
                files=tuple(item for item in files if item is not None),
                meta={"domain": backup.domain, "target": backup.target},
            )

    def _database_items(self) -> Iterator[CopyItem]:
        """
        Yields:
            Each database dump.
        """
        for view in self.databases.list_dumps():
            info = view.info
            record = view.record
            main = _file(info.path, "archive", record.sha256 if record else None)
            if main is None:
                continue
            engine = str(info.engine)
            database = (record.db_name if record and record.db_name else None) or info.database
            kind = record.origin if record else "unknown"
            yield CopyItem(
                kind=KIND_DATABASE,
                id=f"{engine}:{info.path.name}",
                owner=f"{engine}/{database}",
                folder=_slug(f"{engine}.{database}"),
                created_at=_utc(info.created, info.path),
                origin="scheduled"
                if kind == "scheduled"
                else "safety"
                if kind == "safety"
                else "manual",
                sealed=bool(is_sealed(info.path)),
                files=(main,),
                meta={"engine": engine, "database": database},
            )

    def _bundle_items(self) -> Iterator[CopyItem]:
        """
        Yields:
            Each bundle of Noust's own state. Every one is encrypted under its passphrase.
        """
        for entry in self.bundles.entries():
            main = _file(entry.path, "archive", entry.sha256)
            if main is None:
                continue
            sidecar = _file(self.bundles.sidecar_path(entry.path), "sidecar", None)
            yield CopyItem(
                kind=KIND_NOUST,
                id=entry.name,
                owner="noust",
                folder="noust",
                created_at=_utc(entry.created_at, entry.path),
                origin="scheduled"
                if entry.scheduled
                else "safety"
                if entry.kind == "pre-restore"
                else "manual",
                sealed=True,
                files=tuple(item for item in (main, sidecar) if item is not None),
                meta={"host": entry.host or ""},
            )


def _slug(text: str) -> str:
    """
    Args:
        text: What a copy is of.

    Returns:
        The same as one plain name: letters, digits, dots, dashes and underscores.
    """
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in text).strip(".-")
    return cleaned[:120] or "copy"


def open_for_reading(file: CopyFile) -> int:
    """
    Open a listed file without following a link.

    Args:
        file: A file of the catalogue.

    Returns:
        A file descriptor open on it, read only; the caller closes it.

    Raises:
        OSError: The file went away, or became a link, since it was listed.
    """
    return os.open(file.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
