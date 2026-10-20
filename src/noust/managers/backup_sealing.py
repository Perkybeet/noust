# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Sealing the backups that were already on the disk when sealing was turned on (checklist H-08).

``noust backup encryption enable`` seals what is made from then on; a backup made before it stays in
clear until its retention removes it, and that copy is the one someone who gets the disk reads. This
seals them in place, keeping their names, kind by kind:

* **applications' archives** (``BackupManager``) and **data backups** (``VolumeBackupManager``),
  whose sidecar holds the archive's size and checksum and a MAC over them: sealing changes the bytes,
  so the sidecar is written again, signed, with the stored file's;
* **database dumps** (``DatabaseBackups``), whose record holds the same two values, checked on every
  verification;
* **base backups of point-in-time recovery** (``PitrManager``), whose sidecar's digest is checked
  before every recovery.

Each file goes through :func:`~noust.core.backup_files.reseal`, which checks it against its record
before sealing (a damaged backup is left in clear and said, not sealed into something that looks
sound), seals it beside itself and renames it into place, and repairs a file that was sealed but
whose record was not written. So the run can be stopped at any point and started again. An
application's backups are sealed under its operation lock: a restore or a backup of the same
application that is running makes it wait for the next run instead of racing the rename.

**What it does not cover**, and says: the archived write-ahead log (the engine writes it one segment
at a time, outside the backups directory), a file no record describes (a safety copy kept by a
restore, a dump put there by hand) and what is on a remote destination, which keeps what it was sent.
:attr:`SealReport.clear_left` counts what is still in clear on this disk.

It needs space for one more copy of the largest backup, for a moment.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from noust.core import audit
from noust.core.applock import AppBusyError, app_lock
from noust.core.backup_files import BackupKeys, ResealOutcome, inventory, is_sealed, reseal
from noust.core.exceptions import BackupError, NoustError
from noust.core.fs import FileSystem
from noust.core.runner import CommandRunner

__all__ = ["SealExisting", "SealItem", "SealReport", "encryption_status"]

#: States that leave a file in clear or unproven, and so make a run "not ok".
_PROBLEMS = frozenset({"damaged", "mismatch", "unreadable", "failed", "busy", "missing"})

#: States after which the file is sealed and its record current.
_DONE = frozenset({"sealed", "repaired", "already"})

#: What the four kinds are called in a report.
KINDS = ("application", "data", "database", "pitr")


@dataclass(frozen=True)
class _Target:
    """
    One backup a record describes.

    Attributes:
        domain: The application whose lock it is sealed under; empty when it belongs to none.
        name: Its id or file name.
        path: The file.
        recorded: The checksum its record holds; None when it holds none.
        record: Writes the stored file's size and checksum into the record.
    """

    domain: str
    name: str
    path: Path
    recorded: str | None
    record: Callable[[int, str], None]


@dataclass(frozen=True)
class SealItem:
    """
    What became of one backup.

    Attributes:
        kind: ``application``, ``data``, ``database`` or ``pitr``.
        name: The backup's id or file name; ``(all)`` for a kind that could not be listed.
        state: See :class:`~noust.core.backup_files.ResealOutcome`, plus ``busy`` (its
            application is being operated on) and ``failed``.
        detail: What to tell the operator for a state that left it alone.
    """

    kind: str
    name: str
    state: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        """
        Returns:
            The item as plain data.
        """
        return {"kind": self.kind, "name": self.name, "state": self.state, "detail": self.detail}


@dataclass
class SealReport:
    """
    What a run did.

    Attributes:
        items: One per backup looked at, in the order they were.
        clear_left: Files still in clear on this disk after the run, whatever kind.
    """

    items: list[SealItem] = field(default_factory=list)
    clear_left: int = 0

    def counts(self) -> dict[str, int]:
        """
        Returns:
            How many items ended in each state.
        """
        found: dict[str, int] = {}
        for item in self.items:
            found[item.state] = found.get(item.state, 0) + 1
        return found

    @property
    def sealed(self) -> int:
        """How many backups this run sealed or repaired."""
        counts = self.counts()
        return counts.get("sealed", 0) + counts.get("repaired", 0)

    @property
    def ok(self) -> bool:
        """Whether every backup looked at is sealed, or would be (a rehearsal)."""
        return not any(item.state in _PROBLEMS for item in self.items)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The report as plain data, for ``--json`` and the API.
        """
        return {
            "ok": self.ok,
            "counts": self.counts(),
            "clear_left": self.clear_left,
            "items": [item.to_dict() for item in self.items],
        }


def encryption_status(keys: BackupKeys | None = None) -> dict[str, Any]:
    """
    Say whether new backups are sealed, with which key, and how many are on the disk each way.

    The one answer ``noust backup encryption status`` and ``GET /api/backups/encryption`` give.

    Args:
        keys: The keys. A default set when None.

    Returns:
        ``enabled``, ``current_key``, ``keys`` (each ``id`` and ``current``, never a value),
        ``sealed`` and ``clear``.

    Raises:
        NoustError: When the secret store cannot be read.
    """
    ring = keys or BackupKeys()
    return {
        "enabled": ring.enabled(),
        "current_key": ring.current_id(),
        "keys": [{"id": key.id, "current": key.current} for key in ring.keys()],
        **inventory(),
    }


class SealExisting:
    """
    Seal the backups on this disk that were made in clear.

    Args:
        keys: The keys. A default set when None.
        apps: The application backup manager. Left out (``...``, the default) a real one is
            built on first use; None leaves the kind out, which is what a test of one kind does.
        volumes: The data backup manager, the same way.
        databases: The database backups, the same way.
        pitr: The point-in-time recovery manager, the same way.
        runner: The command runner for openssl. The process-wide one when None.
        fs: The filesystem seam. The process-wide one when None.
    """

    def __init__(
        self,
        *,
        keys: BackupKeys | None = None,
        apps: Any = ...,
        volumes: Any = ...,
        databases: Any = ...,
        pitr: Any = ...,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
    ) -> None:
        self.keys = keys or BackupKeys()
        self._managers: dict[str, Any] = {
            "application": apps,
            "data": volumes,
            "database": databases,
            "pitr": pitr,
        }
        self._runner = runner
        self._fs = fs

    # ----------------------------------------------------------------- the managers

    def _manager(self, kind: str) -> Any | None:
        """
        Args:
            kind: One of :data:`KINDS`.

        Returns:
            The manager of that kind, built on first use; None when the caller left it out.
        """
        found = self._managers[kind]
        if found is not ...:
            return found
        if kind == "application":
            from noust.managers.backup_manager import BackupManager

            found = BackupManager(verbose=False)
        elif kind == "data":
            from noust.managers.volume_backups import VolumeBackupManager

            found = VolumeBackupManager()
        elif kind == "database":
            from noust.managers.database.backups import DatabaseBackups
            from noust.managers.database.service import DatabaseService

            found = DatabaseBackups(DatabaseService())
        else:
            from noust.managers.database.pitr import PitrManager

            found = PitrManager()
        self._managers[kind] = found
        return found

    # ----------------------------------------------------------------- what is there

    def _targets(self, kind: str) -> list[_Target]:
        """
        List the backups of a kind that a record describes.

        Args:
            kind: One of :data:`KINDS`.

        Returns:
            The backups, each with how to write its record.

        Raises:
            NoustError: When the kind cannot be listed.
            OSError: Likewise.
        """
        manager = self._manager(kind)
        if manager is None:
            return []
        found: list[_Target] = []
        if kind in ("application", "data"):
            for entry in manager.list_backups():
                path = (
                    manager.local_files(entry)[0]
                    if kind == "application"
                    else manager.archive_path(entry)
                )

                def record(size: int, sha: str, entry: Any = entry, manager: Any = manager) -> None:
                    manager.record_resealed(entry, size_bytes=size, checksum=sha)

                found.append(_Target(entry.domain, entry.id, path, entry.checksum, record))
        elif kind == "database":
            for view in manager.list_dumps():
                recorded = view.record.sha256 if view.record is not None else None

                def record_dump(
                    size: int, sha: str, view: Any = view, manager: Any = manager
                ) -> None:
                    manager.record_resealed(view, size=size, sha256=sha)

                name = f"{view.info.engine}/{view.info.path.name}"
                found.append(_Target("", name, view.info.path, recorded, record_dump))
        else:
            policy = manager.records.get("postgresql")
            if policy is not None:
                for base in manager.list_bases(policy.system_id):

                    def record_base(
                        size: int,
                        sha: str,
                        base: Any = base,
                        manager: Any = manager,
                        system_id: str = policy.system_id,
                    ) -> None:
                        manager.record_resealed(system_id, base, size=size, sha256=sha)

                    path = manager.base_path(policy.system_id, base)
                    found.append(_Target("", base.id, path, base.sha256, record_base))
        return found

    def plan(self) -> dict[str, Any]:
        """
        Count the backups a run would seal, without touching any.

        Returns:
            ``applications``, ``data``, ``databases`` and ``pitr``: how many are in clear;
            ``total``; and ``sealing``, whether new backups are sealed (a run needs it).
        """
        counts = {"application": 0, "data": 0, "database": 0, "pitr": 0}
        for kind in KINDS:
            try:
                counts[kind] = sum(
                    1 for target in self._targets(kind) if not is_sealed(target.path)
                )
            except (NoustError, OSError):
                counts[kind] = 0
        return {
            "applications": counts["application"],
            "data": counts["data"],
            "databases": counts["database"],
            "pitr": counts["pitr"],
            "total": sum(counts.values()),
            "sealing": self.keys.enabled(),
        }

    # ----------------------------------------------------------------- doing it

    def run(self, on_item: Callable[[SealItem], None] | None = None) -> SealReport:
        """
        Seal every backup in clear and record what each now is.

        Args:
            on_item: Called with each item as it is done, for a progress line.

        Returns:
            What became of each backup, and how much is still in clear on this disk.

        Raises:
            BackupError: When backups are not sealed on this server: there is no key to seal
                with, and sealing the existing ones is the second step after the first.
        """
        if not self.keys.enabled():
            raise BackupError(
                "Backups are not sealed on this server",
                details="Turn sealing on first: noust backup encryption enable. Then seal what "
                "is already there with: noust backup encryption seal-existing",
            )
        report = SealReport()

        def add(item: SealItem) -> None:
            report.items.append(item)
            if on_item is not None:
                on_item(item)

        for kind in KINDS:
            try:
                targets = self._targets(kind)
            except (NoustError, OSError) as exc:
                add(SealItem(kind, "(all)", "failed", _say(exc)))
                continue
            for domain, group in _by_domain(targets):
                self._seal_group(kind, domain, group, add)
        report.clear_left = inventory()["clear"]
        if report.sealed:
            audit.record(
                "backup.encryption.seal",
                target="backups:local",
                details={"sealed": report.sealed, "clear_left": report.clear_left},
            )
        return report

    def _seal_group(
        self,
        kind: str,
        domain: str,
        group: list[_Target],
        add: Callable[[SealItem], None],
    ) -> None:
        """
        Seal the backups of one application, or the ones that belong to none, under its lock.

        Args:
            kind: One of :data:`KINDS`.
            domain: The application, or ``""``.
            group: Its backups, as :meth:`_targets` lists them.
            add: Takes each finished item.
        """
        try:
            if domain:
                with app_lock(domain, "sealing its backups"):
                    for target in group:
                        add(self._seal_one(kind, target))
            else:
                for target in group:
                    add(self._seal_one(kind, target))
        except AppBusyError as exc:
            for target in group:
                add(SealItem(kind, target.name, "busy", f"{exc.message}; the next run seals it"))

    def _seal_one(self, kind: str, target: _Target) -> SealItem:
        """
        Seal one backup and write its record.

        Args:
            kind: One of :data:`KINDS`.
            target: As :meth:`_targets` lists it.

        Returns:
            What became of it.
        """
        outcome: ResealOutcome = reseal(
            target.path, target.recorded, keys=self.keys, runner=self._runner, fs=self._fs
        )
        wants_record = outcome.changed or (outcome.state == "already" and target.recorded is None)
        if wants_record and outcome.size is not None and outcome.sha256 is not None:
            try:
                target.record(outcome.size, outcome.sha256)
            except (NoustError, OSError) as exc:
                return SealItem(kind, target.name, "failed", _say(exc))
        return SealItem(kind, target.name, outcome.state, outcome.detail)


def _say(error: BaseException) -> str:
    """
    Args:
        error: Why a kind could not be listed or a record written.

    Returns:
        Its message and details as one line a report can carry.
    """
    if isinstance(error, NoustError):
        details = f" {error.details}" if error.details else ""
        return f"{error.message}.{details}".replace("..", ".")
    return str(error)


def _by_domain(targets: Iterable[_Target]) -> list[tuple[str, list[_Target]]]:
    """
    Args:
        targets: Backups as :meth:`SealExisting._targets` lists them.

    Returns:
        The same, grouped by application in order of first appearance, so each application's
        lock is taken once and the ones that belong to none stay together.
    """
    order: list[str] = []
    grouped: dict[str, list[_Target]] = {}
    for target in targets:
        if target.domain not in grouped:
            order.append(target.domain)
            grouped[target.domain] = []
        grouped[target.domain].append(target)
    return [(domain, grouped[domain]) for domain in order]
