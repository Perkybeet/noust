# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Move a server WASM 2.x ran onto Noust's names.

Until 3.0 the product was called WASM. Its configuration lived in
``/etc/wasm``, its store in ``/var/lib/wasm/wasm.db``, its backups in
``/var/backups/wasm``, its blue/green upstream snippets in
``/etc/nginx/wasm-upstreams``, the PHP-FPM pools it wrote were ``wasm-<app>``,
and its own systemd units were ``wasm-web``, ``wasm-monitor``,
``wasm-previews``, ``wasm-cron-*`` and ``wasm-backup-*``. This module renames
all of it, once, on the first privileged run of Noust 3.0 (and on demand with
``noust migrate-from-wasm``).

What it guarantees, because each is a way this could go wrong on a server
with real applications on it:

- **Nothing is copied.** Every directory moves with rename(2), which is
  atomic and on the same filesystem only. When the two names are on
  different filesystems the step is refused and explained: a copy
  interrupted halfway leaves two partial trees, the worst of both.
- **An empty store never shadows a full one.** The store file is renamed in
  place after a checkpoint, never recreated, and it is refused while another
  process holds it open. :mod:`noust.core.store` looks at every intermediate
  name, so a run stopped between two steps still finds it.
- **Old paths keep working.** ``/etc/wasm``, ``/var/lib/wasm``,
  ``/var/backups/wasm`` and ``/etc/nginx/wasm-upstreams`` become symlinks to
  the new directories, so scripts, cron lines, backup paths recorded in the
  store and anything else that spelled them keeps working.
- **Every step is undone when it fails.** nginx and PHP-FPM are tested
  before they are reloaded and put back as they were when the test fails;
  a unit is renamed one at a time and the old one restored, enabled and
  started as it was when the new one does not come up.
- **It can be interrupted.** Each step looks at what is on disk, not at a
  journal, so running it again finishes whatever was left.

Applications' own units keep their names; WASM never gave them its prefix
after 0.14.1, and the ones from before stay recognised as they are.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import logging
import os
import re
import sqlite3
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from noust.core import paths
from noust.core.exceptions import NoustError
from noust.core.fs import SECRET_MODE, FileSystem, get_fs
from noust.core.runner import CommandRunner, get_runner

logger = logging.getLogger(__name__)

#: Deadline for one systemctl, nginx or php-fpm call.
_CONTROL_TIMEOUT = 60

#: How long an automatic run waits for another process's migration.
LOCK_WAIT_SECONDS = 300

#: Set to 1 to keep a privileged run from migrating on its own. The explicit
#: command still works.
DISABLE_ENV = "NOUST_NO_AUTO_MIGRATE"

#: Suffix of the symlink built beside a directory before it is renamed, and
#: renamed over the old name after: a run stopped between the two renames
#: finds it and finishes the job.
_PENDING_LINK_SUFFIX = ".noust-migration"

#: Config values that were WASM's defaults, and what they are now. Only an
#: exact default is rewritten: a path the operator chose stays theirs (and
#: keeps working through the compatibility symlinks).
CONFIG_DEFAULTS: dict[str, tuple[str, str]] = {
    "backup.directory": (str(paths.LEGACY_BACKUP_DIR), str(paths.BACKUP_DIR)),
    "logging.file": (
        str(paths.LEGACY_LOG_DIR / "wasm.log"),
        str(paths.LOG_DIR / "noust.log"),
    ),
    "monitor.log_file": (
        str(paths.LEGACY_LOG_DIR / "monitor.log"),
        str(paths.LOG_DIR / "monitor.log"),
    ),
}

#: Unit names in WASM's own units that are rewritten to Noust's: the
#: console, the monitor, the previews sweep, and the scheduled-work prefixes.
_LEGACY_OWN_STEMS = re.compile(
    r"\bwasm-(web|monitor|previews|cron-[A-Za-z0-9_.@-]+|backup-[A-Za-z0-9_.@-]+)\b"
)


#: Why the units wait for the directories: the renamed monitor's
#: StateDirectory is /var/lib/noust, which must hold the data first.
_UNITS_BLOCKED = (
    "WASM's directories did not all move, and the renamed units expect them under "
    "Noust's names. Fix what the directory steps above say, then run: "
    "noust migrate-from-wasm"
)


class MigrationError(NoustError):
    """A step of the migration from WASM could not be completed."""


@dataclass(frozen=True)
class Layout:
    """
    Every location the migration reads or changes.

    The defaults are the real system; tests build one under a temporary
    directory.

    Attributes:
        config_dir: ``/etc/noust``.
        legacy_config_dir: ``/etc/wasm``.
        state_dir: ``/var/lib/noust``.
        legacy_state_dir: ``/var/lib/wasm``.
        backup_dir: ``/var/backups/noust``.
        legacy_backup_dir: ``/var/backups/wasm``.
        user_data_dir: ``~/.local/share/noust`` of the user running it.
        legacy_user_data_dir: ``~/.local/share/wasm``.
        upstreams_dir: ``/etc/nginx/noust-upstreams``.
        legacy_upstreams_dir: ``/etc/nginx/wasm-upstreams``.
        nginx_site_dirs: Directories whose files may include an upstream
            snippet or pass requests to a pool's socket.
        systemd_dir: Where WASM wrote its units.
        php_root: Filesystem root :func:`find_fpm` looks under.
        lock_file: Serialises concurrent runs.
    """

    config_dir: Path = paths.CONFIG_DIR
    legacy_config_dir: Path = paths.LEGACY_CONFIG_DIR
    state_dir: Path = paths.STATE_DIR
    legacy_state_dir: Path = paths.LEGACY_STATE_DIR
    backup_dir: Path = paths.BACKUP_DIR
    legacy_backup_dir: Path = paths.LEGACY_BACKUP_DIR
    user_data_dir: Path = field(default_factory=paths.user_data_dir)
    legacy_user_data_dir: Path = field(default_factory=paths.legacy_user_data_dir)
    upstreams_dir: Path = paths.NGINX_UPSTREAMS_DIR
    legacy_upstreams_dir: Path = paths.LEGACY_NGINX_UPSTREAMS_DIR
    nginx_site_dirs: tuple[Path, ...] = (
        Path("/etc/nginx/sites-available"),
        Path("/etc/nginx/sites-enabled"),
        Path("/etc/nginx/conf.d"),
    )
    systemd_dir: Path = Path("/etc/systemd/system")
    php_root: Path = Path("/")
    lock_file: Path = Path("/run/noust-migrate-from-wasm.lock")

    @property
    def record(self) -> Path:
        """Where the record of what was done is kept, in the new state dir."""
        return self.state_dir / paths.MIGRATION_RECORD.name

    def directory_pairs(self) -> list[tuple[str, Path, Path, bool]]:
        """
        List the directories that move, with whether each holds a store.

        Returns:
            ``(label, legacy, new, holds_store)`` in the order they move.
        """
        return [
            ("configuration", self.legacy_config_dir, self.config_dir, False),
            ("state", self.legacy_state_dir, self.state_dir, True),
            ("backups", self.legacy_backup_dir, self.backup_dir, False),
            ("per-user data", self.legacy_user_data_dir, self.user_data_dir, True),
        ]


@dataclass
class Step:
    """
    One thing the migration does, has done or would do.

    Attributes:
        kind: ``directory``, ``store``, ``config``, ``nginx``, ``php`` or ``unit``.
        description: What it does, in a sentence.
        status: ``pending``, ``done``, ``refused`` (not attempted, with the
            reason in ``detail``), ``failed`` (attempted and undone) or
            ``skipped``.
        detail: Why it was refused or failed, or what it left behind.
    """

    kind: str
    description: str
    status: str = "pending"
    detail: str = ""


@dataclass
class _UnitGroup:
    """A WASM unit and its timer (or the service a timer starts), moved together."""

    legacy_stem: str
    new_stem: str
    files: list[Path]
    enabled: dict[str, bool] = field(default_factory=dict)
    active: dict[str, bool] = field(default_factory=dict)


@dataclass
class MigrationReport:
    """
    What a run did, or would do.

    Attributes:
        steps: Every step, in order.
        dry_run: Whether nothing was changed.
    """

    steps: list[Step] = field(default_factory=list)
    dry_run: bool = False

    @property
    def changed(self) -> bool:
        """Whether anything was done."""
        return any(step.status == "done" for step in self.steps)

    @property
    def complete(self) -> bool:
        """Whether nothing is left to do: no step pending, refused or failed."""
        return all(step.status in ("done", "skipped") for step in self.steps)

    def as_dict(self) -> dict[str, Any]:
        """
        Serialise for ``--json`` and the record file.

        Returns:
            The report as plain data.
        """
        return {
            "dry_run": self.dry_run,
            "complete": self.complete,
            "steps": [asdict(step) for step in self.steps],
        }


# -- detection ---------------------------------------------------------------


def _is_real_dir(path: Path) -> bool:
    """
    Report whether a path is a directory and not a symlink to one.

    Args:
        path: The path.

    Returns:
        True for a real directory.
    """
    return path.is_dir() and not path.is_symlink()


def same_filesystem(first: Path, second: Path) -> bool:
    """
    Report whether two existing paths are on the same filesystem.

    Args:
        first: A path.
        second: Another path.

    Returns:
        True when rename(2) can move one next to the other.
    """
    return first.stat().st_dev == second.stat().st_dev


def _pending_link(legacy: Path) -> Path:
    """
    Name the symlink prepared beside a directory before it moves.

    Args:
        legacy: The WASM directory.

    Returns:
        ``<parent>/.<name>.noust-migration``.
    """
    return legacy.with_name(f".{legacy.name}{_PENDING_LINK_SUFFIX}")


def _legacy_unit_files(systemd_dir: Path) -> list[Path]:
    """
    Find WASM's own unit files: its names and its marker, both required.

    Args:
        systemd_dir: Where WASM wrote units.

    Returns:
        Regular files named like one of WASM's own units that carry
        "Generated by WASM", sorted.
    """
    found: list[Path] = []
    try:
        entries = sorted(systemd_dir.iterdir())
    except OSError:
        return found
    for entry in entries:
        if entry.suffix not in (".service", ".timer") or entry.is_symlink():
            continue
        stem = entry.stem
        if not stem.startswith(paths.LEGACY_UNIT_PREFIX):
            continue
        if stem not in paths.OWN_UNITS and not stem.startswith(paths.OWN_UNIT_PREFIXES):
            continue
        try:
            content = entry.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if paths.LEGACY_UNIT_MARKER in content:
            found.append(entry)
    return found


def _legacy_pools(layout: Layout) -> list[Path]:
    """
    Find the PHP-FPM pool files WASM wrote, enabled and stopped.

    Args:
        layout: Where to look.

    Returns:
        ``wasm-<app>.conf`` and ``wasm-<app>.conf.disabled`` files.
    """
    pool_dir = _pool_dir(layout)
    if pool_dir is None:
        return []
    prefix = paths.LEGACY_PHP_POOL_PREFIX
    return sorted(
        entry
        for entry in pool_dir.glob(f"{prefix}*.conf*")
        if entry.is_file()
        and not entry.is_symlink()
        and (entry.name.endswith(".conf") or entry.name.endswith(".conf.disabled"))
    )


def _pool_dir(layout: Layout) -> Path | None:
    """
    Find the directory PHP-FPM pools are written to, when PHP-FPM is installed.

    Args:
        layout: Where to look.

    Returns:
        The pool directory, or None.
    """
    from noust.deployers.helpers.php_fpm import find_fpm

    try:
        return find_fpm(layout.php_root).pool_dir
    except NoustError:
        return None


def needs_migration(layout: Layout | None = None) -> bool:
    """
    Report, cheaply, whether anything of WASM's is left to move.

    This runs on every privileged command, so it only looks at names: a few
    stats and one directory listing.

    Args:
        layout: Where to look; the real system by default.

    Returns:
        True when a WASM directory is still a real directory (or half moved),
        a WASM unit remains, or the upstreams directory still has its old name.
    """
    layout = layout or Layout()
    for _label, legacy, _new, _store in layout.directory_pairs():
        if _is_real_dir(legacy) or os.path.lexists(_pending_link(legacy)):
            return True
    upstreams = layout.legacy_upstreams_dir
    if _is_real_dir(upstreams) or os.path.lexists(_pending_link(upstreams)):
        return True
    if _legacy_pools(layout):
        return True
    return bool(_legacy_unit_files(layout.systemd_dir))


# -- the migration -----------------------------------------------------------


class Migrator:
    """
    Plans and performs the move from WASM's names to Noust's.

    Every change goes through the injected runner and filesystem, like every
    other change Noust makes.
    """

    def __init__(
        self,
        layout: Layout | None = None,
        *,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        announce: Callable[[str], None] | None = None,
        noust_executable: str | None = None,
    ) -> None:
        """
        Initialize the migrator.

        Args:
            layout: Where everything is; the real system by default.
            runner: Runs systemctl, nginx and php-fpm.
            fs: Changes files.
            announce: Receives one line per step as it completes.
            noust_executable: The absolute path units should run; found on
                PATH by default.
        """
        self.layout = layout or Layout()
        self.runner = runner or get_runner()
        self.fs = fs or get_fs()
        self.announce = announce or (lambda _line: None)
        self._noust_executable = noust_executable

    # -- public ---------------------------------------------------------------

    def plan(self) -> MigrationReport:
        """
        Say what a run would do, without changing anything.

        Returns:
            The steps, all ``pending`` or ``refused``.
        """
        report = MigrationReport(dry_run=True)
        for label, legacy, new, holds_store in self.layout.directory_pairs():
            step = self._directory_step(label, legacy, new)
            if step is not None:
                report.steps.append(step)
                if holds_store:
                    store = self._store_step(legacy, new)
                    if store is not None:
                        report.steps.append(store)
        report.steps.extend(self._config_steps())
        nginx = self._nginx_step()
        if nginx is not None:
            report.steps.append(nginx)
        pools = [p for p in _legacy_pools(self.layout) if p.name.endswith(".conf")]
        stopped = [p for p in _legacy_pools(self.layout) if p.name.endswith(".disabled")]
        if pools or stopped:
            report.steps.append(
                Step(
                    "php",
                    f"Rename {len(pools) + len(stopped)} PHP-FPM pool(s) from wasm-<app> to "
                    "noust-<app>, with their sockets, without dropping a request",
                )
            )
        blocked = any(
            step.kind in ("directory", "store") and step.status == "refused"
            for step in report.steps
        )
        for group in self._unit_groups():
            step = Step(
                "unit",
                f"Replace {', '.join(p.name for p in group.files)} with the same unit(s) "
                f"named {group.new_stem}, in the same state",
            )
            if blocked:
                step.status = "refused"
                step.detail = _UNITS_BLOCKED
            report.steps.append(step)
        return report

    def run(self) -> MigrationReport:
        """
        Perform every step that is left, under the lock.

        Returns:
            What was done, refused and failed.
        """
        with self._locked():
            report = MigrationReport()
            quiesced = self._quiesce()
            try:
                data_ok = self._run_directories(report)
                self._run_config(report)
                self._run_nginx(report)
                self._run_php(report)
                if data_ok:
                    self._run_units(report, quiesced)
                else:
                    for group in self._unit_groups():
                        report.steps.append(
                            Step(
                                "unit",
                                f"Rename {', '.join(p.name for p in group.files)}",
                                status="refused",
                                detail=_UNITS_BLOCKED,
                            )
                        )
            finally:
                self._resume(quiesced)
            if report.changed:
                self._write_record(report)
            return report

    # -- locking --------------------------------------------------------------

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        """
        Hold the migration lock, waiting for another run to finish.

        Yields:
            Nothing; the lock is held for the body.

        Raises:
            MigrationError: When another run holds it for too long.
        """
        lock_path = self.layout.lock_file
        with contextlib.suppress(OSError):
            self.fs.make_dir(lock_path.parent)
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            deadline = time.monotonic() + LOCK_WAIT_SECONDS
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise MigrationError(
                            "Another process has been migrating this server from WASM "
                            f"for over {LOCK_WAIT_SECONDS}s",
                            details=f"If none is running, remove {lock_path} and try again.",
                        ) from exc
                    time.sleep(0.5)
            yield
        finally:
            os.close(descriptor)

    # -- quiescing ------------------------------------------------------------

    def _quiesce(self) -> list[str]:
        """
        Stop WASM's console and monitor while the store moves under them.

        They hold the store open; renaming it under a running writer is how a
        write-ahead log ends up beside the wrong file. Only needed when a
        store is about to move.

        Returns:
            The units that were running and were stopped.
        """
        store_moves = any(
            self._store_step(legacy, new) is not None
            for _label, legacy, new, holds in self.layout.directory_pairs()
            if holds
        )
        if not store_moves:
            return []
        stopped: list[str] = []
        for unit in (f"{paths.LEGACY_WEB_UNIT}.service", f"{paths.LEGACY_MONITOR_UNIT}.service"):
            if not (self.layout.systemd_dir / unit).exists():
                continue
            if self._systemctl("is-active", "--quiet", unit).success:
                if self._systemctl("stop", unit).success:
                    stopped.append(unit)
                    self.announce(f"stopped {unit} while its store moves")
        return stopped

    def _resume(self, stopped: list[str]) -> None:
        """
        Start again what :meth:`_quiesce` stopped and the units step did not replace.

        Args:
            stopped: Units that were running before the migration.
        """
        for unit in stopped:
            if not (self.layout.systemd_dir / unit).exists():
                # Replaced by its Noust unit, which the units step started.
                continue
            if self._systemctl("start", unit).success:
                self.announce(f"started {unit} again")
            else:
                logger.warning("Could not start %s again after the migration", unit)

    # -- directories and the store --------------------------------------------

    def _directory_step(self, label: str, legacy: Path, new: Path) -> Step | None:
        """
        Describe the move of one directory, or None when there is nothing to move.

        Args:
            label: What the directory holds.
            legacy: WASM's name.
            new: Noust's name.

        Returns:
            A pending or refused step, or None.
        """
        pending = _pending_link(legacy)
        if not _is_real_dir(legacy):
            if not legacy.exists() and os.path.lexists(pending) and new.is_dir():
                return Step("directory", f"Finish linking {legacy} to {new} ({label})")
            return None
        description = f"Rename {legacy} to {new} ({label}) and leave {legacy} as a link to it"
        refusal = self._refusal(legacy, new)
        if refusal:
            return Step("directory", description, status="refused", detail=refusal)
        return Step("directory", description)

    def _refusal(self, legacy: Path, new: Path) -> str:
        """
        Say why a directory cannot be renamed, or nothing when it can.

        Args:
            legacy: WASM's name.
            new: Noust's name.

        Returns:
            The reason, or an empty string.
        """
        parent = new.parent
        if not parent.is_dir():
            return f"{parent} does not exist"
        if not same_filesystem(legacy, parent):
            return (
                f"{legacy} and {parent} are on different filesystems, and a directory is only "
                "ever renamed, never copied: a copy interrupted halfway leaves two partial "
                f"trees. Noust keeps using {legacy} where it is. To move it, stop Noust's "
                f"services, copy it to {new} yourself, check the copy, remove {legacy} and "
                f"link it: ln -s {new} {legacy}"
            )
        if new.is_symlink() or (new.exists() and not new.is_dir()):
            return f"{new} exists and is not a directory; move it out of the way first"
        return ""

    def _store_step(self, legacy: Path, new: Path) -> Step | None:
        """
        Describe the rename of the store file inside a directory that holds one.

        Args:
            legacy: WASM's directory.
            new: Noust's directory.

        Returns:
            A step when ``wasm.db`` is still there under either directory.
        """
        for directory in (legacy, new):
            old = directory / paths.LEGACY_STORE_NAME
            if old.is_file() and not (directory / paths.STORE_NAME).exists():
                return Step("store", f"Rename {old} to {directory / paths.STORE_NAME}")
        return None

    def _run_directories(self, report: MigrationReport) -> bool:
        """
        Move every directory and store that is left.

        Args:
            report: Receives the steps.

        Returns:
            True when nothing of WASM's is left unmoved.
        """
        all_moved = True
        for label, legacy, new, holds_store in self.layout.directory_pairs():
            if holds_store:
                # The store is renamed inside the directory first, so the
                # directory rename that follows moves one file, not two names.
                for directory in (legacy, new):
                    store = self._store_step(directory, directory)
                    if store is None:
                        continue
                    report.steps.append(store)
                    self._rename_store(directory, store)
                    if store.status != "done":
                        all_moved = False
            step = self._directory_step(label, legacy, new)
            if step is None:
                continue
            report.steps.append(step)
            if step.status == "refused":
                all_moved = False
                self.announce(f"not moved: {legacy}: {step.detail}")
                continue
            try:
                self._move_directory(legacy, new, step)
            except OSError as exc:
                step.status = "failed"
                step.detail = (
                    f"{exc}. Nothing was lost: {legacy} is where it was"
                    if _is_real_dir(legacy)
                    else str(exc)
                )
                all_moved = False
                self.announce(f"could not move {legacy}: {exc}")
        return all_moved

    def _move_directory(self, legacy: Path, new: Path, step: Step) -> None:
        """
        Rename one directory and leave a symlink with its old name.

        The link is built first, under a temporary name, so a run stopped
        right after the rename finds it and completes the job.

        Args:
            legacy: WASM's name.
            new: Noust's name.
            step: Updated with the outcome.

        Raises:
            OSError: When a rename fails; what was renamed is put back.
        """
        pending = _pending_link(legacy)
        if not _is_real_dir(legacy):
            # Resumed: the directory already moved and its link is waiting.
            self.fs.rename(pending, legacy)
            step.status = "done"
            self.announce(f"linked {legacy} to {new}")
            return

        if os.path.lexists(pending):
            # Left by a run stopped before the rename; it is rebuilt below.
            self.fs.remove(pending)
        if new.is_dir():
            self._clear_new_directory(new, step)

        self.fs.symlink(new, pending)
        try:
            self.fs.rename(legacy, new)
        except OSError as exc:
            with contextlib.suppress(OSError):
                self.fs.remove(pending)
            if exc.errno == errno.EXDEV:
                raise OSError(
                    exc.errno,
                    f"{legacy} and {new.parent} are on different filesystems; nothing was copied",
                ) from exc
            raise
        self.fs.rename(pending, legacy)
        step.status = "done"
        self.announce(f"renamed {legacy} to {new}; {legacy} links to it")

    def _clear_new_directory(self, new: Path, step: Step) -> None:
        """
        Make way for a WASM directory where Noust's name already exists.

        A package may create ``/etc/noust`` or ``/var/lib/noust`` on install,
        and systemd creates a unit's ``StateDirectory``. Nothing Noust runs
        writes there while a real WASM directory exists (it reads the WASM one
        instead, see :func:`noust.core.paths.resolve_dir`), so whatever is
        there is a default. An empty directory is removed; anything else is
        moved aside, beside it, and named in the step.

        Args:
            new: Noust's name, which exists.
            step: Receives what was set aside.
        """
        if not any(new.iterdir()):
            self.fs.remove_tree(new)
            return
        aside = new.with_name(f"{new.name}.before-migration-{time.strftime('%Y%m%d%H%M%S')}")
        self.fs.rename(new, aside)
        step.detail = f"what was already in {new} is kept in {aside}"
        self.announce(f"set aside {new} as {aside}")

    def _rename_store(self, directory: Path, step: Step) -> None:
        """
        Rename ``wasm.db`` to ``noust.db`` inside a directory, safely.

        The write-ahead log is folded into the database first. When the log
        and its index are still there after this process let go, another
        process has the store open, and the rename is refused: moving a store
        under a writer is how its recent writes end up in a file nobody reads.

        Args:
            directory: The directory holding ``wasm.db``.
            step: Updated with the outcome.
        """
        old = directory / paths.LEGACY_STORE_NAME
        new = directory / paths.STORE_NAME
        try:
            connection = sqlite3.connect(f"file:{old}?mode=rw", uri=True, timeout=10)
            try:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                connection.close()
        except sqlite3.Error as exc:
            step.status = "refused"
            step.detail = f"Could not read {old}: {exc}. It was left where it is, untouched."
            self.announce(f"not renamed: {old}: {exc}")
            return
        leftovers = [
            side
            for side in (Path(f"{old}-wal"), Path(f"{old}-shm"))
            if side.exists() and (side.name.endswith("-shm") or side.stat().st_size > 0)
        ]
        if leftovers:
            step.status = "refused"
            step.detail = (
                f"Another process has {old} open ({', '.join(p.name for p in leftovers)} "
                "still in use). Stop it (systemctl stop wasm-web wasm-monitor, or whatever "
                "runs WASM), then run: noust migrate-from-wasm"
            )
            self.announce(f"not renamed: {old} is in use")
            return
        wal = Path(f"{old}-wal")
        if wal.exists():
            # Empty after the checkpoint; it would otherwise sit beside a
            # database of another name, which SQLite would never read anyway.
            self.fs.remove(wal)
        self.fs.rename(old, new)
        step.status = "done"
        self.announce(f"renamed {old} to {new}")

    # -- configuration ----------------------------------------------------------

    def _config_file(self) -> Path:
        """Where config.yaml is, before or after its directory moved."""
        for directory in (self.layout.config_dir, self.layout.legacy_config_dir):
            candidate = directory / "config.yaml"
            if candidate.is_file():
                return candidate
        return self.layout.config_dir / "config.yaml"

    def _config_steps(self) -> list[Step]:
        """
        Describe the rewrite of WASM's default paths in config.yaml.

        Returns:
            One step, or none when no value is a WASM default.
        """
        path = self._config_file()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return []
        found = [key for key, (old, _new) in CONFIG_DEFAULTS.items() if _config_has(text, old)]
        if not found:
            return []
        return [
            Step(
                "config",
                f"Point {', '.join(found)} in config.yaml at Noust's default paths",
            )
        ]

    def _run_config(self, report: MigrationReport) -> None:
        """
        Rewrite WASM's default paths in config.yaml, keeping everything else.

        The file is edited as text, not reloaded and dumped, so the operator's
        comments and ordering survive.

        Args:
            report: Receives the step.
        """
        steps = self._config_steps()
        if not steps:
            return
        step = steps[0]
        report.steps.append(step)
        path = self._config_file()
        text = path.read_text(encoding="utf-8")
        for key, (old, new) in CONFIG_DEFAULTS.items():
            if key == "backup.directory" and _is_real_dir(self.layout.legacy_backup_dir):
                # The backups did not move: pointing the setting at the new,
                # empty directory would hide every one of them.
                continue
            text = _config_replace(text, old, new)
        try:
            self.fs.write_text(path, text, mode=SECRET_MODE)
        except OSError as exc:
            step.status = "failed"
            step.detail = f"{exc}. The old paths keep working through their links."
            return
        step.status = "done"
        self.announce("pointed config.yaml at Noust's default paths")

    # -- nginx --------------------------------------------------------------------

    def _nginx(self) -> Any:
        """
        Build the nginx manager the migration tests and reloads through.

        Returns:
            A :class:`~noust.managers.nginx_manager.NginxManager`.
        """
        from noust.managers.nginx_manager import NginxManager

        return NginxManager(runner=self.runner, fs=self.fs)

    def _site_files(self, needle: str) -> list[Path]:
        """
        Find the site files that mention a path.

        Args:
            needle: The text to look for.

        Returns:
            Regular files (not the symlinks that enable them) containing it.
        """
        found: list[Path] = []
        for directory in self.layout.nginx_site_dirs:
            if not directory.is_dir():
                continue
            for entry in sorted(directory.iterdir()):
                if entry.is_symlink() or not entry.is_file():
                    continue
                try:
                    if needle in entry.read_text(encoding="utf-8", errors="replace"):
                        found.append(entry)
                except OSError:
                    continue
        return found

    def _rewrite_sites(self, replacements: list[tuple[str, str]]) -> dict[Path, str]:
        """
        Apply text replacements to every site file that needs one.

        Args:
            replacements: ``(old, new)`` pairs.

        Returns:
            The original content of every file changed, to restore it.
        """
        originals: dict[Path, str] = {}
        for old, _new in replacements:
            for site in self._site_files(old):
                if site not in originals:
                    originals[site] = site.read_text(encoding="utf-8")
        for site, content in originals.items():
            updated = content
            for old, new in replacements:
                updated = updated.replace(old, new)
            mode = site.stat().st_mode & 0o777
            self.fs.write_text(site, updated, mode=mode)
        return originals

    def _restore_sites(self, originals: dict[Path, str]) -> None:
        """
        Put site files back as they were.

        Args:
            originals: What :meth:`_rewrite_sites` returned.
        """
        for site, content in originals.items():
            mode = site.stat().st_mode & 0o777
            self.fs.write_text(site, content, mode=mode)

    def _nginx_step(self) -> Step | None:
        """
        Describe what is left of the upstreams move, or None when nothing is.

        Returns:
            A pending or refused step.
        """
        legacy = self.layout.legacy_upstreams_dir
        new = self.layout.upstreams_dir
        pending = _pending_link(legacy)
        moving = _is_real_dir(legacy) or (not legacy.exists() and os.path.lexists(pending))
        repointing = bool(self._site_files(f"{legacy}/"))
        if not moving and not repointing:
            return None
        description = f"Rename {legacy} to {new} and repoint the sites that include it"
        if _is_real_dir(legacy):
            refusal = self._refusal(legacy, new)
            if refusal:
                return Step("nginx", description, status="refused", detail=refusal)
        return Step("nginx", description)

    def _run_nginx(self, report: MigrationReport) -> None:
        """
        Rename the upstreams directory, then repoint every include.

        The directory moves like the others, leaving a link with its old name,
        so every site keeps loading at every instant. The includes are then
        rewritten, nginx tested and reloaded; when the test fails the sites are
        put back, still valid through the link, and nginx's words reported.

        Args:
            report: Receives the step.
        """
        step = self._nginx_step()
        if step is None:
            return
        report.steps.append(step)
        if step.status == "refused":
            return
        legacy = self.layout.legacy_upstreams_dir
        new = self.layout.upstreams_dir
        if _is_real_dir(legacy) or (not legacy.exists() and os.path.lexists(_pending_link(legacy))):
            try:
                self._move_directory(legacy, new, Step("directory", ""))
            except OSError as exc:
                step.status = "failed"
                step.detail = f"{exc}. {legacy} is where it was."
                return
        manager = self._nginx()
        originals: dict[Path, str] = {}
        try:
            originals = self._rewrite_sites([(f"{legacy}/", f"{new}/")])
            errors = manager.config_errors()
            if errors:
                raise MigrationError("nginx rejected the repointed includes", output=errors)
            if originals and not manager.reload():
                raise MigrationError("nginx did not reload")
        except (MigrationError, OSError) as exc:
            self._restore_sites(originals)
            step.status = "failed"
            output = getattr(exc, "output", "") or ""
            step.detail = (
                f"{exc}; the sites were put back, and they keep loading through {legacy}, "
                f"which links to {new}." + (f"\n{output}" if output else "")
            )
            self.announce(f"nginx: sites put back: {exc}")
            return
        step.status = "done"
        self.announce(f"renamed {legacy} to {new}; {len(originals)} site(s) repointed")

    # -- PHP-FPM ------------------------------------------------------------------

    def _run_php(self, report: MigrationReport) -> None:
        """
        Rename WASM's PHP-FPM pools without dropping a request.

        The new pools are added beside the old ones and FPM is reloaded, so
        both sockets answer; the sites are repointed and nginx reloaded; only
        then are the old pools removed. When a test fails, everything added is
        removed and every site put back.

        Args:
            report: Receives the step.
        """
        from noust.core.logger import Logger
        from noust.deployers.helpers.php_fpm import FpmService, find_fpm

        legacy_files = _legacy_pools(self.layout)
        if not legacy_files:
            return
        step = Step("php", f"Rename {len(legacy_files)} PHP-FPM pool(s) to noust-<app>")
        report.steps.append(step)
        installation = find_fpm(self.layout.php_root)
        fpm = FpmService(installation, runner=self.runner, fs=self.fs, logger=Logger())
        old_prefix = paths.LEGACY_PHP_POOL_PREFIX
        new_prefix = paths.PHP_POOL_PREFIX

        added: list[Path] = []
        socket_pairs: list[tuple[str, str]] = []
        for legacy in legacy_files:
            name = new_prefix + legacy.name.removeprefix(old_prefix)
            target = legacy.with_name(name)
            app = legacy.name.removeprefix(old_prefix).split(".conf")[0]
            old_socket = str(installation.socket_dir / f"{old_prefix}{app}.sock")
            new_socket = str(installation.socket_dir / f"{new_prefix}{app}.sock")
            # Every site is repointed, even for a pool a stopped run already
            # added: its old pool is removed below either way.
            socket_pairs.append((old_socket, new_socket))
            if target.exists():
                continue
            content = legacy.read_text(encoding="utf-8")
            content = (
                content.replace(f"[{old_prefix}{app}]", f"[{new_prefix}{app}]")
                .replace(old_socket, new_socket)
                .replace(paths.LEGACY_UNIT_MARKER, paths.UNIT_MARKER)
            )
            self.fs.write_text(target, content, mode=legacy.stat().st_mode & 0o777)
            added.append(target)

        manager = self._nginx()
        originals: dict[Path, str] = {}
        try:
            errors = fpm.config_errors()
            if errors:
                raise MigrationError("php-fpm rejected the renamed pools", output=errors)
            fpm.reload()
            originals = self._rewrite_sites(socket_pairs)
            errors = manager.config_errors()
            if errors:
                raise MigrationError("nginx rejected the renamed sockets", output=errors)
            if not manager.reload():
                raise MigrationError("nginx did not reload")
        except (NoustError, OSError) as exc:
            self._restore_sites(originals)
            if originals:
                manager.reload()
            for path in added:
                self.fs.remove(path)
            with contextlib.suppress(NoustError):
                fpm.reload()
            step.status = "failed"
            output = getattr(exc, "output", "") or ""
            step.detail = f"{exc}; the pools and sites were put back as they were." + (
                f"\n{output}" if output else ""
            )
            self.announce(f"php-fpm: undone: {exc}")
            return

        for legacy in legacy_files:
            self.fs.remove(legacy)
        try:
            fpm.reload()
        except NoustError as exc:
            # The new pools serve; the old ones only linger until FPM restarts.
            step.detail = f"The old pools are removed but FPM did not reload: {exc}"
        step.status = "done"
        self.announce(f"renamed {len(legacy_files)} PHP-FPM pool(s) to noust-<app>")

    # -- units ----------------------------------------------------------------------

    def _systemctl(self, *args: str) -> Any:
        """
        Run systemctl.

        Args:
            args: Its arguments.

        Returns:
            The command's result.
        """
        return self.runner.run(["systemctl", *args], timeout=_CONTROL_TIMEOUT)

    def _unit_groups(self) -> list[_UnitGroup]:
        """
        Group WASM's unit files by name: a timer moves with its service.

        Returns:
            One group per unit name, in order.
        """
        groups: dict[str, _UnitGroup] = {}
        for path in _legacy_unit_files(self.layout.systemd_dir):
            stem = path.stem
            new_stem = paths.UNIT_PREFIX + stem.removeprefix(paths.LEGACY_UNIT_PREFIX)
            group = groups.setdefault(stem, _UnitGroup(stem, new_stem, []))
            group.files.append(path)
        return list(groups.values())

    def _executable(self) -> str | None:
        """The noust executable units should run, when it can be found."""
        if self._noust_executable is None:
            from noust.core.utils import find_noust_executable

            found = find_noust_executable()
            self._noust_executable = found or ""
        return self._noust_executable or None

    def _rewrite_unit(self, content: str) -> str:
        """
        Turn one of WASM's units into the same unit under Noust's names.

        The marker, the unit names it mentions, its state and log directories,
        WASM's paths and the executable are renamed; nothing else changes.

        Args:
            content: The WASM unit.

        Returns:
            The Noust unit.
        """
        text = content.replace(paths.LEGACY_UNIT_MARKER, paths.UNIT_MARKER)
        text = _LEGACY_OWN_STEMS.sub(lambda m: f"{paths.UNIT_PREFIX}{m.group(1)}", text)
        text = re.sub(r"(?m)^(StateDirectory|LogsDirectory)=wasm$", rf"\1={paths.NAME}", text)
        text = re.sub(r"(?m)^Description=WASM\b", "Description=Noust", text)
        text = re.sub(r"(?m)^# WASM\b", "# Noust", text)
        for legacy, new in (
            (paths.LEGACY_CONFIG_DIR, paths.CONFIG_DIR),
            (paths.LEGACY_STATE_DIR, paths.STATE_DIR),
            (paths.LEGACY_BACKUP_DIR, paths.BACKUP_DIR),
        ):
            text = re.sub(rf"{re.escape(str(legacy))}(?=[/\s'\"]|$)", str(new), text, flags=re.M)
        executable = self._executable()
        if executable:
            text = re.sub(
                r"(?m)^(ExecStart=)(\S*/)?wasm(?=\s|$)",
                lambda m: f"{m.group(1)}{executable}",
                text,
            )
        return text

    def _record_state(self, group: _UnitGroup) -> None:
        """
        Read whether each unit of a group is enabled and running.

        Args:
            group: Updated in place.
        """
        for path in group.files:
            name = path.name
            group.enabled[name] = self._systemctl("is-enabled", "--quiet", name).success
            group.active[name] = self._systemctl("is-active", "--quiet", name).success

    def _run_units(self, report: MigrationReport, quiesced: list[str]) -> None:
        """
        Replace each of WASM's units with Noust's, one group at a time.

        Args:
            report: Receives one step per group.
            quiesced: Units :meth:`_quiesce` stopped; they count as running.
        """
        for group in self._unit_groups():
            step = Step(
                "unit",
                f"Replace {', '.join(p.name for p in group.files)} with {group.new_stem}",
            )
            report.steps.append(step)
            self._record_state(group)
            for name in quiesced:
                if name in group.active:
                    group.active[name] = True
            busy = [
                path.name
                for path in group.files
                if path.suffix == ".service"
                and group.active.get(path.name)
                and any(p.suffix == ".timer" for p in group.files)
            ]
            if busy:
                step.status = "refused"
                step.detail = (
                    f"{', '.join(busy)} is running a job right now; it was left alone. "
                    "Run noust migrate-from-wasm again once it finishes."
                )
                continue
            try:
                self._replace_group(group)
            except MigrationError as exc:
                step.status = "failed"
                step.detail = f"{exc.message}; {group.legacy_stem} was put back as it was." + (
                    f"\n{exc.output}" if exc.output else ""
                )
                self.announce(f"unit {group.legacy_stem}: undone: {exc.message}")
                continue
            step.status = "done"
            self.announce(f"replaced {group.legacy_stem} with {group.new_stem}")

    def _replace_group(self, group: _UnitGroup) -> None:
        """
        Swap one unit (and its timer) for its Noust twin, or put it back.

        Args:
            group: The unit files and their recorded state.

        Raises:
            MigrationError: When a step failed; everything was undone.
        """
        directory = self.layout.systemd_dir
        new_files: list[Path] = []
        for path in group.files:
            target = directory / f"{group.new_stem}{path.suffix}"
            if target.exists():
                raise MigrationError(f"{target.name} already exists and was not replaced")
            new_files.append(target)

        # Stopped first: the console's two names cannot hold its port at once.
        for path in group.files:
            if group.active.get(path.name):
                self._systemctl("stop", path.name)
        try:
            for path, target in zip(group.files, new_files, strict=True):
                content = self._rewrite_unit(path.read_text(encoding="utf-8"))
                self.fs.write_text(target, content, mode=0o644)
            self._require(self._systemctl("daemon-reload"), "systemctl daemon-reload")
            for path, target in zip(group.files, new_files, strict=True):
                if group.enabled.get(path.name):
                    self._require(self._systemctl("enable", target.name), f"enable {target.name}")
            for path, target in zip(group.files, new_files, strict=True):
                if group.active.get(path.name):
                    self._require(self._systemctl("start", target.name), f"start {target.name}")
                    self._require(
                        self._systemctl("is-active", "--quiet", target.name),
                        f"{target.name} did not stay up",
                        unit=target.name,
                    )
        except MigrationError:
            for target in new_files:
                self._systemctl("stop", target.name)
                self._systemctl("disable", target.name)
                with contextlib.suppress(OSError):
                    self.fs.remove(target)
            self._systemctl("daemon-reload")
            for path in group.files:
                if group.enabled.get(path.name):
                    self._systemctl("enable", path.name)
            for path in group.files:
                if group.active.get(path.name):
                    self._systemctl("start", path.name)
            raise

        for path in group.files:
            self._systemctl("disable", path.name)
            self.fs.remove(path)
        self._systemctl("daemon-reload")

    def _require(self, result: Any, what: str, *, unit: str | None = None) -> None:
        """
        Turn a failed systemctl call into an error that carries its output.

        Args:
            result: The command's result.
            what: What was attempted.
            unit: A unit whose journal explains the failure.

        Raises:
            MigrationError: When the call failed.
        """
        if result.success:
            return
        output = "\n".join(s for s in (result.stderr, result.stdout) if s and s.strip())
        if unit:
            journal = self.runner.run(
                ["journalctl", "-u", unit, "-n", "30", "--no-pager"], timeout=_CONTROL_TIMEOUT
            )
            output = "\n".join(s for s in (output, journal.stdout) if s and s.strip())
        raise MigrationError(f"{what} failed", output=output or None)

    # -- the record ---------------------------------------------------------------

    def _write_record(self, report: MigrationReport) -> None:
        """
        Append this run to the record the console reads.

        Args:
            report: What was done.
        """
        from noust import __version__

        record = self.layout.record
        history: list[dict[str, Any]] = []
        with contextlib.suppress(OSError, ValueError):
            loaded = json.loads(record.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                history = loaded
        history.append(
            {
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "version": __version__,
                **report.as_dict(),
            }
        )
        if not record.parent.is_dir():
            return
        try:
            self.fs.write_text(record, json.dumps(history, indent=2) + "\n", mode=0o644)
        except OSError as exc:
            logger.warning("Could not write %s: %s", record, exc)


# -- config helpers ---------------------------------------------------------------


def _config_has(text: str, value: str) -> bool:
    """
    Report whether config.yaml sets a value to exactly this path.

    Args:
        text: The file.
        value: The path.

    Returns:
        True when a ``key: value`` line (quoted or not) holds it.
    """
    return re.search(_value_pattern(value), text) is not None


def _config_replace(text: str, old: str, new: str) -> str:
    """
    Replace a path that is a whole value in config.yaml.

    Args:
        text: The file.
        old: The value to replace.
        new: Its replacement.

    Returns:
        The edited text.
    """
    return re.sub(_value_pattern(old), lambda m: f"{m.group(1)}{new}{m.group(3)}", text)


def _value_pattern(value: str) -> str:
    """
    Build the pattern of a YAML scalar that is exactly ``value``.

    Args:
        value: The path.

    Returns:
        A multiline pattern with the prefix, the value and the suffix grouped.
    """
    return rf"(?m)(:\s*['\"]?)({re.escape(value)})(['\"]?\s*(?:#.*)?$)"


# -- the automatic run ------------------------------------------------------------


def should_run_automatically(argv: list[str], environ: dict[str, str] | None = None) -> bool:
    """
    Decide whether this invocation migrates before running its command.

    Only an operator's privileged command does: never a process systemd
    started (the console's own unit would stop itself), never a rehearsal,
    never shell completion, and never the explicit command, which runs it
    itself.

    Args:
        argv: The command line after the program name.
        environ: The environment; the process's by default.

    Returns:
        True when the run should happen.
    """
    env = os.environ if environ is None else environ
    if os.geteuid() != 0:
        return False
    if env.get(DISABLE_ENV) == "1" or env.get("INVOCATION_ID"):
        return False
    if any(key in env for key in ("_NOUST_COMPLETE", "_WASM_COMPLETE")):
        return False
    if "--dry-run" in argv or "migrate-from-wasm" in argv:
        return False
    return True


def run_automatically(argv: list[str], announce: Callable[[str], None]) -> MigrationReport | None:
    """
    Migrate on the first privileged run, when anything is left to migrate.

    Every failure is contained: the command the operator asked for runs
    either way, reading whatever has not moved where it still is.

    Args:
        argv: The command line after the program name.
        announce: Receives one line per step.

    Returns:
        The report, or None when nothing ran.
    """
    if not should_run_automatically(argv) or not needs_migration():
        return None
    plan = Migrator().plan()
    if all(step.status == "refused" for step in plan.steps):
        # Only what cannot be done on its own is left (two filesystems, a
        # store in use): saying so on every command would bury the output of
        # the command itself. The explicit command explains it.
        logger.info("The migration from WASM waits on steps it cannot do on its own")
        return None
    announce("migrating this server from WASM to Noust's names (see: noust migrate-from-wasm)")
    try:
        report = Migrator(announce=announce).run()
    except (NoustError, OSError) as exc:
        # The boundary of an automatic step: the operator's command still runs.
        logger.warning("The migration from WASM stopped: %s", exc)
        announce(f"the migration from WASM stopped: {exc}; run: noust migrate-from-wasm")
        return None
    if not report.complete:
        announce("some steps were not done; see: noust migrate-from-wasm --dry-run")
    return report
