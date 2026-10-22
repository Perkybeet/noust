# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Putting Noust's own state back from a bundle: ``noust self-backup restore``.

The other half of :mod:`noust.managers.self_backup`, for a server that was
lost or is new. The order is what keeps a restore from making things worse:

1. **Everything is proven before anything changes.** The bundle is opened (its
   MAC first), extracted with the hardened extractor, every file checked
   against its digest, the store's snapshot asked whether it is whole and
   whether this Noust can open its schema. A bundle that fails any of it
   changes nothing.
2. **What is there is kept.** A server that already holds state (applications,
   secrets) is not overwritten without ``--force``, and a bundle of what it
   holds is taken first, under the same passphrase (``pre-restore``, never
   deleted by retention), so the restore itself can be undone.
3. **The services that write are stopped.** The console, the monitor and the
   previews sweep, and the backup and cron timers, so nothing writes to the
   store while its file is replaced; those that were running are started again.
4. **Files go back one by one, atomically.** Each is written beside its place
   and renamed over it, 0600 (a unit 0644), through no symbolic link: a path
   in a bundle is untrusted input, so it must be relative, climb nowhere and
   cross no link. The places are this server's own (``/etc/noust``, the state
   directory, ``/etc/letsencrypt``, ``/etc/systemd/system``, the web servers' directories),
   never the paths
   the bundle says it came from. A store's ``-wal`` and ``-shm`` files are
   removed first: a write-ahead log of the old database replayed over the new
   one corrupts it.
5. **The store climbs to this version's schema** by opening it, which is how
   every store migrates (the copy it keeps before migrating included).
6. **The units are enabled** as they were, the timers started and the
   services that were running started again.

Files the bundle does not hold are left alone: a restore overlays, it does not
empty. What it does not do is deploy anything: the applications are
:mod:`noust.managers.restore_server`'s.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from noust.core import paths
from noust.core.exceptions import BackupError, NoustError
from noust.core.fs import SECRET_DIR_MODE, SECRET_MODE
from noust.core.store import SCHEMA_VERSION, NoustStore, get_store
from noust.managers.backup_destination_files import file_sha256
from noust.managers.self_backup import SelfBackupManager

__all__ = ["RestoreReport", "SelfRestore"]

_log = logging.getLogger(__name__)

#: A unit file's mode, like every unit Noust writes.
_UNIT_MODE = 0o644

#: Deadline for a systemctl verb.
_SYSTEMCTL_TIMEOUT = 120

#: The units that write to the store, stopped while its file is replaced.
WRITERS = (paths.WEB_UNIT, paths.MONITOR_UNIT, paths.PREVIEWS_UNIT)

#: Timers that start jobs that write, stopped the same way.
TIMER_PATTERNS = (f"{paths.BACKUP_UNIT_PREFIX}*.timer", f"{paths.CRON_UNIT_PREFIX}*.timer")

#: What a database's write-ahead files are called beside it.
_COMPANIONS = ("-wal", "-shm", "-journal")


@dataclass
class RestoreReport:
    """
    What a restore did.

    Attributes:
        files: Files put back.
        links: Links recreated.
        skipped: What was not put back and why.
        schema_before: The store's schema version in the bundle.
        schema_after: The store's schema version after it was opened here.
        safety_copy: The bundle of what was here, taken first; None when
            nothing was.
        stopped: The services stopped for the restore.
        started: The services and timers started afterwards.
        enabled: The units enabled.
        notes: Anything the operator should know next.
    """

    files: int = 0
    links: int = 0
    skipped: list[dict[str, str]] = field(default_factory=list)
    schema_before: int | None = None
    schema_after: int | None = None
    safety_copy: str | None = None
    stopped: list[str] = field(default_factory=list)
    started: list[str] = field(default_factory=list)
    enabled: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The report as plain data, JSON-ready.
        """
        return asdict(self)


class SelfRestore:
    """
    Restore a bundle of Noust's own state onto this server.

    Construct one per command; it holds no state beyond its collaborators.
    """

    def __init__(self, manager: SelfBackupManager, *, restart: bool = True) -> None:
        """
        Args:
            manager: The bundle manager: its runner, filesystem, logger,
                directories and the places this server keeps things.
            restart: Start again what was stopped, and the timers and own
                services of the units that were enabled.
        """
        self.manager = manager
        self.restart = restart

    # ---------------------------------------------------------------- helpers

    @property
    def runner(self) -> Any:
        """The runner systemctl goes through."""
        return self.manager.runner

    def _systemctl(self, *args: str) -> Any:
        """
        Run a systemctl verb.

        Args:
            args: Arguments after the program name.

        Returns:
            The command's outcome.
        """
        return self.runner.run(["systemctl", *args], timeout=_SYSTEMCTL_TIMEOUT)

    def _targets(self, manifest: dict[str, Any], report: RestoreReport) -> dict[str, Path]:
        """
        Decide where each part of a bundle goes on this server.

        Never from the paths the bundle says it came from: a bundle of a server
        whose data directory was elsewhere (a container's ``/data``) still goes
        to this server's own.

        Args:
            manifest: The bundle's manifest.
            report: Notes what is left out.

        Returns:
            Label to directory.
        """
        known = self.manager.restore_targets()
        targets: dict[str, Path] = {}
        for label, recorded in manifest.get("sources", {}).items():
            if label in known:
                targets[label] = known[label]
                continue
            report.skipped.append(
                {
                    "path": label,
                    "reason": f"it came from {recorded}, which is not a place this server keeps "
                    "Noust's files",
                }
            )
        return targets

    @staticmethod
    def _checked(root: Path, relative: str) -> Path:
        """
        Resolve a file's place under a directory, never through a link.

        Args:
            root: The directory it goes under.
            relative: The path inside the bundle, below its label.

        Returns:
            Where it goes.

        Raises:
            BackupError: When the path is absolute, climbs, is empty, or an
                existing directory on the way is a symbolic link.
        """
        pure = PurePosixPath(relative)
        if (
            not relative
            or pure.is_absolute()
            or any(part in ("", ".", "..") for part in pure.parts)
        ):
            raise BackupError(
                f"The bundle names a path it must not: {relative!r}",
                details="A path in a bundle is relative and stays inside the directory it is "
                "restored to. Nothing was restored.",
            )
        place = root.joinpath(*pure.parts)
        walker = root
        for part in pure.parts[:-1]:
            walker = walker / part
            if walker.is_symlink():
                raise BackupError(
                    f"{walker} is a symbolic link; nothing is restored through one",
                    details="Remove the link, or restore onto a clean install.",
                )
        return place

    def _place(self, source: Path, destination: Path, mode: int) -> None:
        """
        Put a file in its place: written beside it, then renamed over it.

        Args:
            source: The extracted file.
            destination: Where it goes.
            mode: Its permission bits.
        """
        fs = self.manager.fs
        fs.make_dir(destination.parent, mode=SECRET_DIR_MODE, parents=True)
        staged = destination.with_name(f".{destination.name}.noust-restore")
        shutil.copyfile(source, staged, follow_symlinks=False)
        os.chmod(staged, mode)
        with staged.open("rb") as handle:
            os.fsync(handle.fileno())
        fs.rename(staged, destination)
        fs.chmod(destination, mode)

    def _has_state(self, targets: dict[str, Path]) -> bool:
        """
        Whether this server already holds something worth not overwriting.

        Args:
            targets: Label to this server's directory.

        Returns:
            True when the store in the state directory records an application
            (or cannot be read), or the secret store has files.
        """
        state = targets.get("state")
        if state is None:
            return False
        store_file = state / paths.STORE_NAME
        if store_file.is_file() and store_file.stat().st_size > 0:
            try:
                connection = sqlite3.connect(f"file:{store_file}?mode=ro", uri=True, timeout=5)
                try:
                    if connection.execute("SELECT COUNT(*) FROM apps").fetchone()[0] > 0:
                        return True
                finally:
                    connection.close()
            except sqlite3.OperationalError as exc:
                # No apps table is a store nothing was ever deployed with; anything else
                # (a damaged file, a lock) is not an empty one and is not overwritten unasked.
                if "no such table" not in str(exc):
                    _log.warning("Could not read the store to see what it holds: %s", exc)
                    return True
            except sqlite3.Error as exc:
                _log.warning("Could not read the store to see what it holds: %s", exc)
                return True
        secrets = state / "secrets"
        return secrets.is_dir() and any(path.is_file() for path in secrets.rglob("*"))

    # ---------------------------------------------------------------- restore

    def restore(
        self,
        name_or_path: str,
        passphrase: str,
        *,
        force: bool = False,
        safety_copy: bool = True,
    ) -> RestoreReport:
        """
        Put Noust's own state back from a bundle.

        Args:
            name_or_path: The bundle, by its name here or its path.
            passphrase: Its passphrase.
            force: Restore over a server that already holds state.
            safety_copy: Take a bundle of what is there first, under the same
                passphrase. Turning it off leaves nothing to undo the restore with.

        Returns:
            What was done.

        Raises:
            EnvelopeError: Wrong passphrase, or the bundle was altered.
            BackupError: The bundle does not verify, was taken by a newer Noust,
                a server that holds state would be overwritten without
                ``force``, or a step failed (the safety copy is named).
        """
        manager = self.manager
        path = manager.resolve(name_or_path)
        report = RestoreReport()
        with tempfile.TemporaryDirectory(
            prefix=".noust-self-restore-", dir=manager._scratch_base()
        ) as scratch:
            root, manifest = manager.open_bundle(path, passphrase, Path(scratch))
            self._prove(root, manifest, report)
            targets = self._targets(manifest, report)
            plan = self._plan(root, manifest, targets, report)
            existing = self._has_state(targets)
            if existing and not force:
                raise BackupError(
                    "This server already holds Noust state, and a restore replaces it",
                    details="Restore onto a clean install, or confirm with --force. A bundle of "
                    "what is here is taken first, so it can be put back.",
                )
            if existing and safety_copy:
                self.manager.logger.info("Taking a bundle of what is here first")
                try:
                    report.safety_copy = manager.create(passphrase, kind="pre-restore").path.name
                except BackupError as exc:
                    raise BackupError(
                        "Could not take a bundle of what is here before replacing it",
                        details=f"{exc.details}\nFix that, or restore without a safety copy: "
                        "noust self-backup restore --no-safety-copy",
                    ) from exc
            report.stopped = self._stop_writers()
            try:
                self._put_back(plan, report)
                self._migrate(root, manifest, targets, report)
            except (BackupError, OSError) as exc:
                self._start(report.stopped)
                kept = (
                    f" What was here is kept in {report.safety_copy}." if report.safety_copy else ""
                )
                if isinstance(exc, BackupError):
                    raise BackupError(
                        f"The restore stopped: {exc.message}",
                        details=f"{exc.details}{kept}\nThe services that were running were started "
                        "again.",
                    ) from exc
                raise BackupError(
                    "The restore stopped on a file that could not be written",
                    details=f"{exc}.{kept}",
                ) from exc
            self._units(manifest, targets, report)
        manager._audit(
            "self_backup.restore",
            name=path.name,
            files=report.files,
            schema=report.schema_after,
            safety=report.safety_copy,
            replaced=existing,
        )
        return report

    def _prove(self, root: Path, manifest: dict[str, Any], report: RestoreReport) -> None:
        """
        Check a bundle end to end before anything changes.

        Args:
            root: The extracted root.
            manifest: Its manifest.
            report: Notes the schema version.

        Raises:
            BackupError: A file is missing or altered, the store's snapshot is
                damaged, or the store is from a newer Noust.
        """
        problems: list[str] = []
        for entry in manifest.get("files", []):
            file_path = root / str(entry.get("path", ""))
            if not file_path.is_file():
                problems.append(f"{entry.get('path')} is missing")
            elif file_sha256(file_path) != entry.get("sha256"):
                problems.append(f"{entry.get('path')} does not match its SHA-256")
        snapshot = self.manager._snapshot_in(root, manifest)
        if snapshot is None:
            problems.append("The bundle holds no snapshot of the store")
        else:
            version, integrity = self.manager._check_snapshot(snapshot)
            report.schema_before = version
            if integrity != "ok":
                problems.append(f"The store's snapshot is damaged: {integrity}")
            elif version is not None and version > SCHEMA_VERSION:
                raise BackupError(
                    f"The bundle's store is schema v{version}; this Noust understands up to "
                    f"v{SCHEMA_VERSION}",
                    details="Install the Noust version that took it, or a newer one, then restore. "
                    "Nothing was changed.",
                )
        if problems:
            raise BackupError(
                "The bundle does not verify; nothing was restored",
                details="\n".join(problems[:10]),
            )

    @staticmethod
    def _mode_of(label: str, entry: dict[str, Any]) -> int:
        """
        Decide a restored file's permission bits.

        Units are 0644 and everything else 0600, except a web server's files, which get the
        mode they had (a configuration stays readable by the tools that read it, a key stays
        private). What a bundle asks for is clamped: never writable by group or others, never
        set-uid, set-gid or sticky.

        Args:
            label: What the file belongs to.
            entry: Its manifest entry.

        Returns:
            The mode.
        """
        if label == "units":
            return _UNIT_MODE
        recorded = entry.get("mode")
        if label in paths.WEBSERVER_CONFIG_DIRS and isinstance(recorded, int):
            return recorded & 0o755 & ~0o022
        return SECRET_MODE

    def _stop_writers(self) -> list[str]:
        """
        Stop what writes to the store, remembering what was running.

        Returns:
            The services that were running and are stopped.
        """
        running: list[str] = []
        for unit in WRITERS:
            if self._systemctl("is-active", "--quiet", f"{unit}.service").success:
                self.manager.logger.info(f"Stopping {unit}")
                self._systemctl("stop", f"{unit}.service")
                running.append(unit)
        # Timers whose jobs write; whatever matches nothing is fine.
        self._systemctl("stop", *TIMER_PATTERNS)
        return running

    def _start(self, units: list[str]) -> None:
        """
        Start services again.

        Args:
            units: Their names.
        """
        if not self.restart:
            return
        for unit in units:
            result = self._systemctl("start", f"{unit}.service")
            if not result.success:
                self.manager.logger.warning(
                    f"Could not start {unit}: {result.stderr.strip() or 'see systemctl status'}"
                )

    def _plan(
        self,
        root: Path,
        manifest: dict[str, Any],
        targets: dict[str, Path],
        report: RestoreReport,
    ) -> list[tuple[str, Path, Path, str, int]]:
        """
        Decide where every file and link of the bundle goes, before any is written.

        Every path is checked here, so a bundle that names one it must not
        changes nothing: not a file, not a service stopped.

        Args:
            root: The extracted root.
            manifest: Its manifest.
            targets: Label to this server's directory.
            report: Notes the links that are not made.

        Returns:
            One ``(kind, source, destination, link target, mode)`` per thing to
            put back; ``kind`` is ``file`` or ``link``.

        Raises:
            BackupError: When a path in the bundle is not one to restore.
        """
        plan: list[tuple[str, Path, Path, str, int]] = []
        for entry in manifest.get("files", []):
            relative = str(entry["path"])
            label, _, below = relative.partition("/")
            target_root = targets.get(label)
            if target_root is None:
                continue
            plan.append(
                (
                    "file",
                    root / relative,
                    self._checked(target_root, below),
                    "",
                    self._mode_of(label, entry),
                )
            )
        for link in manifest.get("links", []):
            relative = str(link["path"])
            label, _, below = relative.partition("/")
            target_root = targets.get(label)
            if target_root is None:
                continue
            place = self._checked(target_root, below)
            wanted = str(link["target"])
            resolved = Path(os.path.normpath(os.path.join(place.parent, wanted)))
            if os.path.isabs(wanted) or not resolved.is_relative_to(target_root):
                report.skipped.append({"path": relative, "reason": "a link that leaves its tree"})
                continue
            plan.append(("link", root, place, wanted, 0))
        return plan

    def _put_back(
        self, plan: list[tuple[str, Path, Path, str, int]], report: RestoreReport
    ) -> None:
        """
        Write every file and link of a plan where it belongs.

        Args:
            plan: What :meth:`_plan` decided.
            report: Counts what was done.
        """
        NoustStore.reset_instance()
        fs = self.manager.fs
        for kind, source, destination, wanted, mode in plan:
            if kind == "link":
                fs.make_dir(destination.parent, mode=SECRET_DIR_MODE, parents=True)
                fs.remove(destination)
                fs.symlink(Path(wanted), destination)
                report.links += 1
                continue
            if destination.name.endswith((".db", ".sqlite", ".sqlite3")):
                for suffix in _COMPANIONS:
                    # A write-ahead log of the old database, replayed over the new one.
                    fs.remove(destination.with_name(destination.name + suffix))
            self._place(source, destination, mode)
            report.files += 1

    def _migrate(
        self,
        root: Path,
        manifest: dict[str, Any],
        targets: dict[str, Path],
        report: RestoreReport,
    ) -> None:
        """
        Open the restored store, which brings its schema to this version.

        Args:
            root: The extracted root.
            manifest: Its manifest.
            targets: Label to this server's directory.
            report: Notes the version afterwards.

        Raises:
            BackupError: When the restored store cannot be opened.
        """
        NoustStore.reset_instance()
        restored = next(
            (
                targets[str(entry["path"]).partition("/")[0]].joinpath(
                    *PurePosixPath(str(entry["path"]).partition("/")[2]).parts
                )
                for entry in manifest.get("files", [])
                if str(entry["path"]).endswith(f"/{paths.STORE_NAME}")
                and str(entry["path"]).partition("/")[0] in targets
            ),
            None,
        )
        try:
            store = get_store(restored) if restored is not None else get_store()
            report.schema_after = SCHEMA_VERSION
            report.notes.append(
                f"The store was opened at {store.db_path} and is schema v{SCHEMA_VERSION}"
            )
        except (NoustError, sqlite3.Error, OSError) as exc:
            raise BackupError(
                "The restored store could not be opened by this version of Noust",
                details=f"{exc}",
            ) from exc

    def _units(
        self, manifest: dict[str, Any], targets: dict[str, Path], report: RestoreReport
    ) -> None:
        """
        Reload systemd, enable the units that were enabled and start what ran.

        Args:
            manifest: The bundle's manifest.
            targets: Label to this server's directory.
            report: Notes what was enabled and started.
        """
        if "units" not in targets:
            self._start(report.stopped)
            report.started = list(report.stopped) if self.restart else []
            return
        self._systemctl("daemon-reload")
        for unit in manifest.get("enabled_units", []):
            if self._systemctl("enable", unit).success:
                report.enabled.append(unit)
            else:
                report.notes.append(f"Could not enable {unit}: enable it with systemctl")
        if self.restart:
            for unit in report.enabled:
                stem = unit.rsplit(".", 1)[0]
                if unit.endswith(".timer") or stem in WRITERS:
                    if self._systemctl("start", unit).success:
                        report.started.append(unit)
            for unit in report.stopped:
                if f"{unit}.service" not in report.started:
                    self._start([unit])
                    report.started.append(f"{unit}.service")
        if manifest.get("contains", {}).get("seal_header"):
            report.notes.append(
                "The secrets are sealed: unlock them with 'noust central unlock' before the "
                "console can use them"
            )
        report.notes.append(
            "Noust's state is back. Its applications are not: noust restore-server brings "
            "them up from their backups"
        )
