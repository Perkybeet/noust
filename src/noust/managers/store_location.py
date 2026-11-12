# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where Noust's store is, and moving it to where a server keeps it: ``noust setup move-store``.

The store lives in the state directory (``/var/lib/noust``) when that exists and is writable, and
in the user's directory (``~/.local/share/noust``) otherwise, and a store that exists wins over a
location that would get an empty one (:mod:`noust.core.store`, which is the one place that
decides). A server whose store never moved from the user's directory, and that later got an empty
``noust.db`` in ``/var/lib/noust``, runs fine until something reads the system's state directory
as if the store were in it: a bundle of Noust's own state, a copy of that directory, a package
upgrade. :func:`assess` says whether that is the case and the doctor names it.

**Moving a store is its whole folder, not one file.** ``deploy-logs``, ``job-logs``, ``locks``,
``security``, ``threats.db`` and above all ``secrets/`` go with it, and the secrets are the part
that cannot be copied: the protected ones (a backup destination's credentials, the GitHub leases)
are sealed under a key that lives beside the store in the user's directory and at
``/etc/noust/secrets.key`` for the system's (:func:`~noust.core.secrets.at_rest_key_path`), so a
copied file would be ciphertext nothing can open, and the destination would lose its password and
Noust would sign with new keys. Each is opened with the key of where it was and sealed again under
the key of where it goes.

What makes the move safe to run on a server:

1. every check that can refuse runs before anything is touched: an existing store at the
   destination is never overwritten, a sealed store is not moved, every protected secret must
   open with the key it has, and there must be room;
2. the services that write are stopped, and a copy of both folders and both keys is kept first;
3. the destination is written beside the old one, the store's SQLite file by SQLite's backup API
   (never a copy of a file in use) and last, in one rename, which is the moment the resolution
   of :mod:`noust.core.store` changes;
4. the copy is verified (the database's integrity and what it holds, every secret opening with
   the new key and giving what it gave) before the rename, and anything that fails undoes what
   was written and starts the services again;
5. the old store file is renamed, not deleted, so nothing opens it again and going back is a
   rename; the rest of the old folder stays for the operator to remove.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import stat
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from noust.central.backup import SQLITE_COMPANIONS, _is_sqlite, _snapshot
from noust.core import paths, sealing
from noust.core.exceptions import ConfigError, NoustError, SetupError
from noust.core.fs import SECRET_DIR_MODE, SECRET_MODE, FileSystem, get_fs, is_rehearsal
from noust.core.logger import Logger
from noust.core.runner import CommandRunner, get_runner
from noust.core.secrets import AT_REST_KEY_FILE, PROTECTED_DOMAIN, SecretStore, at_rest_key_path
from noust.core.store import NoustStore, get_store
from noust.core.timeutil import utc_now
from noust.managers.store_writers import start_writers, stop_writers

__all__ = [
    "StoreLocation",
    "StoreMovePlan",
    "StoreMoveResult",
    "StoreMover",
    "assess",
]

#: Room kept free beyond the size of what is copied, for the safety copy's own files.
_SPARE_BYTES = 64 * 1024 * 1024

#: The secrets directory's name inside a store's folder.
_SECRETS = "secrets"

#: Where the safety copies of a move are kept, under the backups directory.
_SAFETY_ROOT = "store-move"


@dataclass(frozen=True)
class StoreLocation:
    """
    Where the store is, against where a server keeps it.

    Attributes:
        store: The file Noust uses.
        state: The state directory of this server.
        orphan: An empty ``noust.db`` in the state directory that is not the store, or None.
    """

    store: Path
    state: Path
    orphan: Path | None = None

    @property
    def outside(self) -> bool:
        """Whether the store is somewhere else than the state directory."""
        return self.store.parent.resolve() != self.state.resolve()


def assess(store: Path | None = None, state: Path | None = None) -> StoreLocation:
    """
    Say where the store is and whether the state directory holds a file that is not one.

    Args:
        store: The store's file; the one Noust uses (:class:`~noust.core.store.NoustStore` decides
            it) when None.
        state: The state directory; this server's when None.

    Returns:
        The location.
    """
    found = store if store is not None else get_store().db_path
    directory = state if state is not None else paths.state_dir()
    candidate = directory / paths.STORE_NAME
    outside = found.parent.resolve() != directory.resolve()
    empty = outside and candidate.is_file() and candidate.stat().st_size == 0
    return StoreLocation(found, directory, candidate if empty else None)


@dataclass(frozen=True)
class StoreMovePlan:
    """
    What a move would do, checked: nothing has been touched.

    Attributes:
        source: The store's file now.
        destination: Where it will be.
        files: Files that will be copied.
        size: Their bytes.
        secrets: Secrets that will be sealed again under the destination's key.
        orphan: The empty file at the destination that the store replaces, or None.
    """

    source: Path
    destination: Path
    files: int
    size: int
    secrets: int
    orphan: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The plan as plain data, JSON-ready.
        """
        data = asdict(self)
        for name in ("source", "destination", "orphan"):
            data[name] = str(data[name]) if data[name] is not None else None
        return data


@dataclass
class StoreMoveResult:
    """
    What a move did.

    Attributes:
        plan: What it was going to do.
        moved: Whether the store is now in the state directory.
        rehearsed: It was a ``--dry-run``: nothing was written.
        safety_copy: Where the copy of both folders and both keys is.
        retired: The old store file, renamed so nothing opens it again.
        notes: What the operator should know next.
    """

    plan: StoreMovePlan
    moved: bool = False
    rehearsed: bool = False
    safety_copy: Path | None = None
    retired: Path | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The result as plain data, JSON-ready.
        """
        return {
            "plan": self.plan.to_dict(),
            "moved": self.moved,
            "rehearsed": self.rehearsed,
            "safety_copy": str(self.safety_copy) if self.safety_copy else None,
            "retired": str(self.retired) if self.retired else None,
            "notes": list(self.notes),
        }


class _Journal:
    """What a move wrote, so that a move that fails can be undone to the byte."""

    def __init__(self) -> None:
        self.files: list[Path] = []
        self.directories: list[Path] = []


class StoreMover:
    """
    Move the store, with everything that lives beside it, into the state directory.

    Construct one per command; it holds no state beyond its collaborators.
    """

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        logger: Logger | None = None,
        location: StoreLocation | None = None,
        safety_root: Path | None = None,
    ) -> None:
        """
        Args:
            runner: Runs systemctl. The process-wide one when None.
            fs: Every change goes through it. The process-wide one when None.
            logger: Where progress goes.
            location: Where the store is; asked of :func:`assess` when None.
            safety_root: Where the copy of what is replaced is kept; ``store-move`` under the
                backups directory when None.
        """
        self._runner = runner
        self._fs = fs
        self.logger = logger or Logger(verbose=False)
        self._location = location
        self._safety_root = safety_root

    # --------------------------------------------------------------- plumbing

    @property
    def runner(self) -> CommandRunner:
        """The runner systemctl goes through."""
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """The filesystem every change goes through."""
        return self._fs or get_fs()

    @property
    def location(self) -> StoreLocation:
        """Where the store is."""
        if self._location is None:
            self._location = assess()
        return self._location

    @property
    def source(self) -> Path:
        """The folder the store is in now."""
        return self.location.store.parent

    @property
    def destination(self) -> Path:
        """The state directory, where it goes."""
        return self.location.state

    def _secret_names(self, root: Path) -> Iterator[str]:
        """
        Name the secrets of a secrets directory.

        Args:
            root: The directory.

        Yields:
            The path of each regular file, relative to it, with ``/``.
        """
        for directory, _names, filenames in os.walk(root, followlinks=False):
            for name in sorted(filenames):
                full = Path(directory) / name
                if stat.S_ISREG(full.lstat().st_mode):
                    yield full.relative_to(root).as_posix()

    @staticmethod
    def _is_protected(path: Path) -> bool:
        """
        Tell whether a secret is one sealed under the server's own key.

        Args:
            path: Its file.

        Returns:
            True for a protected secret; False for any other, a file that is not text included.
        """
        try:
            return PROTECTED_DOMAIN.is_sealed(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return False

    def _files(self, source: Path) -> Iterator[tuple[Path, str]]:
        """
        List what a move copies: every regular file but SQLite's companions and the key.

        Args:
            source: The folder.

        Yields:
            The file and its path relative to the folder, with ``/``.
        """
        for directory, _dirs, filenames in os.walk(source, followlinks=False):
            for name in sorted(filenames):
                full = Path(directory) / name
                relative = full.relative_to(source).as_posix()
                if name.endswith(SQLITE_COMPANIONS) or relative == AT_REST_KEY_FILE:
                    continue
                if stat.S_ISREG(full.lstat().st_mode):
                    yield full, relative

    # ------------------------------------------------------------------- plan

    def plan(self) -> StoreMovePlan:
        """
        Check that the store can be moved, and say what moving it would do.

        Nothing is changed. Every refusal is here, so a move that is refused has stopped no
        service and written no file.

        Returns:
            The plan.

        Raises:
            SetupError: The store is already in the state directory, there is a store with
                content there, the secrets are sealed or cannot be opened, a file would be
                overwritten, the state directory cannot be written, or there is no room.
        """
        location = self.location
        source, destination = self.source, self.destination
        if not location.outside:
            raise SetupError(
                f"Noust's store is already in {destination}",
                details="There is nothing to move.",
            )
        if not location.store.is_file() or location.store.stat().st_size == 0:
            raise SetupError(
                f"There is no store at {location.store}",
                details="Run 'noust store path' to see where Noust looks for it.",
            )
        target = destination / paths.STORE_NAME
        if target.is_file() and target.stat().st_size > 0:
            raise SetupError(
                f"{destination} already holds a store, and so does {source}",
                details="Moving one over the other would lose one of them. Look at both and move "
                f"the one you do not want away: {target} or {location.store}.",
            )
        if not os.access(destination.parent if not destination.exists() else destination, os.W_OK):
            raise SetupError(
                f"{destination} cannot be written by this account",
                details="The state directory is root's: run the command as root.",
            )
        secrets = source / _SECRETS
        if sealing.is_sealed(secrets):
            raise SetupError(
                f"The secrets in {secrets} are sealed",
                details="A sealed store belongs to a central, which keeps its data in a "
                "directory of its own: it was never in the user's directory.",
            )
        size = 0
        count = 0
        clashes: list[str] = []
        for full, relative in self._files(source):
            if relative == paths.STORE_NAME:
                continue
            count += 1
            size += full.stat().st_size
            if (destination / relative).exists():
                clashes.append(relative)
        if clashes:
            raise SetupError(
                f"{len(clashes)} file(s) of {source} are already in {destination}: "
                f"{', '.join(sorted(clashes)[:5])}",
                details="Nothing is overwritten. Move those away, or compare them, then run "
                "the command again.",
            )
        size += location.store.stat().st_size
        count += 1
        sealed = self._protected_names(source / _SECRETS)
        self._check_room(destination, size)
        return StoreMovePlan(
            source=location.store,
            destination=target,
            files=count,
            size=size,
            secrets=len(sealed),
            orphan=location.orphan,
        )

    def _protected_names(self, root: Path) -> list[str]:
        """
        Name the protected secrets of a directory, proving each one opens with its key.

        Args:
            root: The secrets directory.

        Returns:
            Their names.

        Raises:
            SetupError: A protected secret cannot be opened, so it could not be sealed again.
        """
        if not root.is_dir():
            return []
        reader = SecretStore(root=root, fs=self.fs)
        names = [name for name in self._secret_names(root) if self._is_protected(root / name)]
        for name in names:
            try:
                reader.read_protected_json(name)
            except ConfigError as exc:
                raise SetupError(
                    f"The secret {name} cannot be opened with the key it is sealed under",
                    details=f"{exc.message}. {exc.details} Nothing was changed.",
                ) from exc
        return names

    def _check_room(self, destination: Path, size: int) -> None:
        """
        Refuse when the state directory or the backups directory cannot hold the copies.

        Args:
            destination: The state directory.
            size: Bytes that will be written into it (and into the safety copy).

        Raises:
            SetupError: When a disk lacks the room.
        """
        for where, wanted in (
            (destination if destination.exists() else destination.parent, size + _SPARE_BYTES),
            (self.safety_root if self.safety_root.exists() else self.safety_root.parent, size),
        ):
            try:
                free = shutil.disk_usage(where).free
            except OSError:
                continue
            if free < wanted:
                raise SetupError(
                    f"There is not enough room in {where}",
                    details=f"It needs about {wanted // 2**20} MiB and has {free // 2**20} MiB.",
                )

    @property
    def safety_root(self) -> Path:
        """Where the copies of what a move replaces are kept."""
        return self._safety_root or paths.backup_dir() / _SAFETY_ROOT

    # ------------------------------------------------------------------- move

    def move(self, plan: StoreMovePlan | None = None) -> StoreMoveResult:
        """
        Move the store and everything beside it into the state directory.

        Args:
            plan: What :meth:`plan` said, when the caller has shown it already; checked again
                here when None.

        Returns:
            What was done. Under ``--dry-run`` nothing is written and ``rehearsed`` says so.

        Raises:
            SetupError: A refusal of :meth:`plan`, or a step that failed after what had been
                written was undone (the message says where the copy of the old folder is).
        """
        plan = plan if plan is not None else self.plan()
        result = StoreMoveResult(plan=plan)
        if is_rehearsal():
            result.rehearsed = True
            return result
        stopped: list[str] = []
        journal = _Journal()
        try:
            stopped = stop_writers(self.runner, self.logger)
            result.safety_copy = self._keep_a_copy()
            self._write(plan, journal)
            self._verify(plan)
            self._flip(plan, journal)
        except (NoustError, OSError, sqlite3.Error) as exc:
            self._undo(journal)
            start_writers(self.runner, self.logger, stopped)
            raise SetupError(
                "The store was not moved, and what was written was undone",
                details=f"{exc}. The old store is where it was"
                + (f"; a copy of it is in {result.safety_copy}" if result.safety_copy else "")
                + ".",
            ) from exc
        result.moved = True
        # This process still holds the old file open: whatever asks for the store next gets the new.
        NoustStore.reset_instance()
        result.retired = self._retire(plan, result)
        start_writers(self.runner, self.logger, stopped)
        result.notes.append(
            f"The store is in {plan.destination.parent} now. Run 'noust setup doctor' to confirm."
        )
        return result

    def _keep_a_copy(self) -> Path:
        """
        Keep a copy of the old folder, of what the move replaces and of the keys of both places.

        Returns:
            The directory the copy is in.
        """
        stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
        kept = self.safety_root / stamp
        self.fs.make_dir(kept, mode=SECRET_DIR_MODE, parents=True)
        self.fs.chmod(self.safety_root, SECRET_DIR_MODE)
        self.logger.info(f"Keeping a copy of {self.source} in {kept}")
        self.fs.copy_tree(self.source, kept / "source")
        target = self.destination / paths.STORE_NAME
        if target.is_file():
            shutil.copyfile(target, kept / "replaced-noust.db")
        key = at_rest_key_path(self.destination / _SECRETS)
        if key.is_file():
            shutil.copyfile(key, kept / "destination-secrets.key")
        self.fs.chmod(kept, SECRET_DIR_MODE)
        return kept

    def _write(self, plan: StoreMovePlan, journal: _Journal) -> None:
        """
        Write the destination: every file, the secrets sealed again, the store beside its place.

        Args:
            plan: What is being moved.
            journal: Told of each thing written.

        Raises:
            OSError: A file could not be written.
            sqlite3.Error: A database could not be snapshotted.
        """
        source, destination = self.source, self.destination
        self.logger.info(f"Copying {source} into {destination}")
        for full, relative in self._files(source):
            if relative == paths.STORE_NAME:
                continue
            if relative.partition("/")[0] == _SECRETS and self._is_protected(full):
                continue
            target = destination / relative
            self._make_parents(target.parent, journal)
            if _is_sqlite(full):
                _snapshot(full, target)
            else:
                shutil.copyfile(full, target)
            self.fs.chmod(target, SECRET_MODE)
            journal.files.append(target)
        self._reseal(source / _SECRETS, destination / _SECRETS, journal)
        staged = destination / f".{paths.STORE_NAME}.moving"
        _snapshot(plan.source, staged)
        self.fs.chmod(staged, SECRET_MODE)
        journal.files.append(staged)

    def _make_parents(self, directory: Path, journal: _Journal) -> None:
        """
        Make a directory and the ones above it that are missing, noting each.

        Args:
            directory: The directory.
            journal: Told of each one made.
        """
        missing: list[Path] = []
        current = directory
        while not current.exists() and current != self.destination.parent:
            missing.append(current)
            current = current.parent
        for made in reversed(missing):
            self.fs.make_dir(made, mode=SECRET_DIR_MODE)
            journal.directories.append(made)

    def _reseal(self, old: Path, new: Path, journal: _Journal) -> None:
        """
        Put each protected secret under the key of the place it is going to.

        Args:
            old: The secrets directory it is in.
            new: The secrets directory it is going to.
            journal: Told of each file written, and of the key if this made it.
        """
        if not old.is_dir():
            return
        reader = SecretStore(root=old, fs=self.fs)
        writer = SecretStore(root=new, fs=self.fs)
        key = at_rest_key_path(new)
        key_existed = key.exists()
        for name in self._secret_names(old):
            if not self._is_protected(old / name):
                continue
            target = new / PurePosixPath(name)
            self._make_parents(target.parent, journal)
            writer.write_protected_json(name, reader.read_protected_json(name))
            journal.files.append(target)
        if not key_existed and key.exists():
            journal.files.append(key)

    def _verify(self, plan: StoreMovePlan) -> None:
        """
        Prove the copy before the store is switched: the database whole, the secrets opening.

        Args:
            plan: What was moved.

        Raises:
            SetupError: The copy is not what was there.
        """
        destination = self.destination
        staged = destination / f".{paths.STORE_NAME}.moving"
        counts = []
        for database in (plan.source, staged):
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=30)
            try:
                if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise SetupError(f"The copy of the store is damaged: {database}")
                counts.append(
                    (
                        connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0],
                        connection.execute("SELECT COUNT(*) FROM apps").fetchone()[0],
                    )
                )
            finally:
                connection.close()
        if counts[0] != counts[1]:
            raise SetupError("The copy of the store does not hold what the store holds")
        old, new = self.source / _SECRETS, destination / _SECRETS
        if old.is_dir():
            reader = SecretStore(root=old, fs=self.fs)
            writer = SecretStore(root=new, fs=self.fs)
            for name in self._secret_names(old):
                if self._is_protected(old / name) and writer.read_protected_json(
                    name
                ) != reader.read_protected_json(name):
                    raise SetupError(f"The secret {name} does not open to what it held")

    def _flip(self, plan: StoreMovePlan, journal: _Journal) -> None:
        """
        Put the store in its place, in one rename: this is when the resolution changes.

        Args:
            plan: What is being moved.
            journal: Told that the staged file is the store now, and so not to be undone.
        """
        staged = plan.destination.parent / f".{paths.STORE_NAME}.moving"
        # A rename over the empty file that was there is atomic: the store is whole or not there.
        self.fs.rename(staged, plan.destination)
        journal.files.remove(staged)

    def _retire(self, plan: StoreMovePlan, result: StoreMoveResult) -> Path | None:
        """
        Rename the old store file (and its write-ahead companions), so nothing opens it again.

        A failure here is a note, not a failure: the store is moved and the one in the state
        directory is the one Noust picks.

        Args:
            plan: What was moved.
            result: Told what the operator should know.

        Returns:
            What the store file was renamed to, or None.
        """
        stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
        retired = plan.source.with_name(f"{plan.source.name}.moved-{stamp}")
        try:
            for companion in ("-wal", "-shm", "-journal"):
                found = plan.source.with_name(plan.source.name + companion)
                if found.exists():
                    self.fs.rename(found, found.with_name(found.name + f".moved-{stamp}"))
            self.fs.rename(plan.source, retired)
        except OSError as exc:
            result.notes.append(
                f"The old store file {plan.source} could not be renamed ({exc}). The one in "
                f"{plan.destination.parent} is the one Noust uses; rename the old one by hand."
            )
            return None
        result.notes.append(
            f"The old folder {self.source} stays, with its store renamed {retired.name}: remove "
            "the folder when you have seen that everything works."
        )
        return retired

    def _undo(self, journal: _Journal) -> None:
        """
        Remove what a move that failed had written, and put back what it replaced.

        Args:
            journal: What was written.
        """
        for written in reversed(journal.files):
            try:
                self.fs.remove(written)
            except OSError as exc:
                self.logger.warning(f"Could not remove {written}: {exc}")
        for made in reversed(journal.directories):
            try:
                # Made by this move and emptied above: nothing else is in it.
                self.fs.remove_tree(made)
            except OSError as exc:
                self.logger.warning(f"Could not remove {made}: {exc}")
