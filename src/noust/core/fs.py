# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The seam through which Noust changes the filesystem.

:mod:`noust.core.runner` made ``--dry-run`` true for anything Noust *executes*.
It was not true for anything Noust *writes*, and an adversarial review proved
it: ``noust --dry-run backup delete <id> --force`` printed "no changes will be
made to this machine" and then deleted the archive, because the deletion is a
``Path.unlink`` and never went near a subprocess.

That is the same defect the flag was supposed to fix, one layer down. A
rehearsal that quietly performs half the operation is worse than no rehearsal,
because the operator now trusts it.

So mutations go through a :class:`FileSystem`, and ``--dry-run`` installs one
that refuses. Reads are not routed here: they change nothing, they are on every
hot path, and making every ``Path.exists()`` go through an object would buy
nothing but noise.
"""

from __future__ import annotations

import errno
import grp
import os
import pwd
import shutil
import stat
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

#: Mode for anything that holds a credential: config, tokens, .env files,
#: database dumps and the store.
SECRET_MODE = 0o600

#: Mode for a directory that holds secrets.
SECRET_DIR_MODE = 0o700

#: Called after each change :class:`RealFileSystem` makes, with the operation
#: (``write``, ``mkdir``, ``remove``...), the path and, for a move, a copy or a
#: link, the other path. The host action ledger (noust.core.audit.ledger)
#: listens here; a rehearsal changes nothing and so reports nothing.
_change_listeners: list[Callable[[str, Path, Path | None], None]] = []


def add_change_listener(listener: Callable[[str, Path, Path | None], None]) -> None:
    """
    Be told of every change the real filesystem makes.

    Args:
        listener: Called after each change. It must not raise: the change has
            already happened.
    """
    if listener not in _change_listeners:
        _change_listeners.append(listener)


def remove_change_listener(listener: Callable[[str, Path, Path | None], None]) -> None:
    """
    Stop telling a listener about changes.

    Args:
        listener: One passed to :func:`add_change_listener`.
    """
    if listener in _change_listeners:
        _change_listeners.remove(listener)


def _changed(operation: str, path: Path, destination: Path | None = None) -> None:
    for listener in tuple(_change_listeners):
        listener(operation, path, destination)


class FileSystem(ABC):
    """Changes the filesystem. The only thing in the codebase that may."""

    @abstractmethod
    def write_text(
        self,
        path: Path,
        content: str,
        *,
        mode: int = 0o644,
        owner: tuple[int, int] | None = None,
    ) -> None:
        """
        Write a text file, replacing it atomically.

        A partially written unit file or nginx config is worse than none: the
        next daemon-reload or reload picks it up. Writing to a temporary file
        in the same directory and renaming makes the change all-or-nothing.

        Args:
            path: File to write.
            content: What to write.
            mode: Permissions to create it with. Use SECRET_MODE for anything
                holding a credential; the mode is applied at creation, not
                afterwards, so the file is never briefly world-readable.
            owner: The uid and gid the file must end up with. They are given
                to the temporary file through its descriptor before it takes
                the path's place, because a chown by name afterwards follows
                whatever an account controlling the directory swapped in.
        """

    @abstractmethod
    def make_dir(
        self, path: Path, *, mode: int = 0o755, parents: bool = True, exist_ok: bool = True
    ) -> None:
        """
        Create a directory.

        Args:
            path: Directory to create.
            mode: Permissions, applied to every level this call creates.
            parents: Create missing parents.
            exist_ok: Accept a directory that is already there. False makes
                the creation of the leaf a claim: exactly one caller can win
                it, which is how two deploys racing for the same release name
                end up in different directories instead of the same one.

        Raises:
            FileExistsError: exist_ok is False and the path already exists.
        """

    @abstractmethod
    def remove(self, path: Path, *, missing_ok: bool = True) -> None:
        """
        Delete a file.

        Args:
            path: File to delete.
            missing_ok: Do not complain when it is already gone.
        """

    @abstractmethod
    def remove_tree(self, path: Path) -> None:
        """
        Delete a directory and everything under it.

        Args:
            path: Directory to delete.
        """

    @abstractmethod
    def move(self, source: Path, destination: Path) -> None:
        """
        Move a file or directory.

        Args:
            source: What to move.
            destination: Where to move it.
        """

    @abstractmethod
    def rename(self, source: Path, destination: Path) -> None:
        """
        Rename a file or directory in one step, on the same filesystem only.

        Unlike :meth:`move`, which falls back to copying and deleting when the
        two paths are on different filesystems, this fails instead: a copy
        interrupted halfway leaves two partial trees, and the callers that
        need a rename (the migration from WASM's paths) must be able to say
        "this was not done" rather than "this was half done".

        Args:
            source: What to rename.
            destination: Its new name, which must not exist as a non-empty
                directory.

        Raises:
            OSError: ``EXDEV`` when the paths are on different filesystems,
                or whatever rename(2) reports.
        """

    @abstractmethod
    def copy_tree(self, source: Path, destination: Path) -> None:
        """
        Copy a directory recursively.

        Args:
            source: Directory to copy.
            destination: Where to copy it.
        """

    @abstractmethod
    def chmod(self, path: Path, mode: int, *, follow_symlinks: bool = True) -> None:
        """
        Change permissions.

        Args:
            path: What to change.
            mode: New mode.
            follow_symlinks: False for any path found in a tree Noust does not
                control (a repository, a release, ``shared/``): the change is
                made to the entry itself, and a symbolic link is refused, so a
                link committed as ``.env`` cannot make root change the mode of
                the file it points at.

        Raises:
            OSError: ``ELOOP`` when ``follow_symlinks`` is False and ``path``
                is a symbolic link.
        """

    @abstractmethod
    def set_owner(self, path: Path, *, user: str, group: str, mode: int) -> None:
        """
        Give an existing file or directory to an account, and set its mode.

        The entry is pinned without following a symbolic link and both
        changes are made to that inode, never by name: a chown or chmod by
        name in a directory another account controls follows a link swapped
        in between a check and the change, which hands root's files (say
        ``/etc/shadow``) to that account.

        Args:
            path: The entry.
            user: Account that must own it.
            group: Group that must own it.
            mode: Permission bits; setuid, setgid and sticky are never set.

        Raises:
            KeyError: When the account or the group does not exist.
            OSError: ``ELOOP`` when the entry is a symbolic link, or what the
                open, the chown or the chmod report.
        """

    @abstractmethod
    def symlink(self, target: Path, link: Path) -> None:
        """
        Create a symbolic link, atomically replacing one that is already there.

        Anything that follows the path while it changes sees either the old
        target or the new one, never nothing. That is what lets a release swap
        happen under a running service and nginx: removing the old link first
        and creating the new one leaves a window in which ``current`` does not
        exist.

        Args:
            target: What the link points at, stored verbatim: a relative target
                stays relative, so the tree it lives in can be moved.
            link: The link to create.
        """


def _chmod_entry(path: Path, mode: int) -> None:
    """
    Change the mode of a directory entry without following a symbolic link.

    Linux has no lchmod, and a check followed by a chmod leaves a window in
    which the entry can be swapped for a link. So the entry is opened with
    ``O_PATH | O_NOFOLLOW``, which pins the inode without reading it (a FIFO
    or a device is never opened for real), and the mode is changed through
    the descriptor's ``/proc`` alias, which names that inode and nothing else.

    Args:
        path: The entry.
        mode: New mode.

    Raises:
        OSError: ``ELOOP`` when the entry is a symbolic link, or what the
            open or the chmod report.
    """
    descriptor = os.open(path, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if stat.S_ISLNK(os.fstat(descriptor).st_mode):
            raise OSError(
                errno.ELOOP, "refusing to change the mode through a symbolic link", str(path)
            )
        os.chmod(f"/proc/self/fd/{descriptor}", mode)
    finally:
        os.close(descriptor)


#: The permission bits Noust ever hands out: setuid, setgid and sticky are
#: never carried onto a file or directory it gives to another account.
_PERMISSION_BITS = 0o777


def _set_owner_entry(path: Path, uid: int, gid: int, mode: int) -> None:
    """
    Change the owner and mode of a directory entry without following a link.

    Same technique as :func:`_chmod_entry`: the inode is pinned with
    ``O_PATH | O_NOFOLLOW`` and changed through the descriptor's ``/proc``
    alias, so a link swapped in after the open changes nothing.

    Args:
        path: The entry.
        uid: New owner.
        gid: New group.
        mode: New mode; anything beyond the permission bits is dropped.

    Raises:
        OSError: ``ELOOP`` when the entry is a symbolic link, or what the
            open, the chown or the chmod report.
    """
    descriptor = os.open(path, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if stat.S_ISLNK(os.fstat(descriptor).st_mode):
            raise OSError(
                errno.ELOOP, "refusing to change the owner through a symbolic link", str(path)
            )
        alias = f"/proc/self/fd/{descriptor}"
        os.chown(alias, uid, gid)
        # After the chown: chown(2) clears setuid and setgid, and the mode
        # asked for must be the last word.
        os.chmod(alias, mode & _PERMISSION_BITS)
    finally:
        os.close(descriptor)


class RealFileSystem(FileSystem):
    """Actually changes the filesystem."""

    def write_text(
        self,
        path: Path,
        content: str,
        *,
        mode: int = 0o644,
        owner: tuple[int, int] | None = None,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

        # A fixed temporary name is a predictable path an attacker can plant a
        # symlink at: O_CREAT alone would then follow it and write wherever it
        # points, as root. O_EXCL refuses an existing entry of any kind, and
        # O_NOFOLLOW refuses a symlink, so neither a squatted regular file nor
        # a squatted link can redirect the write. A random suffix on top means
        # a loser of that race retries rather than failing.
        temporary = path.with_name(f".{path.name}.{os.urandom(6).hex()}.wasm-tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW

        # The mode is set at creation. A chmod afterwards leaves a window in
        # which a file holding a database password is world-readable.
        descriptor = os.open(temporary, flags, mode)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                if owner is not None:
                    # Through the descriptor and before the rename: the
                    # directory may be another account's, and nothing after
                    # os.replace may touch the path by name.
                    os.fchown(handle.fileno(), owner[0], owner[1])
                    os.fchmod(handle.fileno(), mode & _PERMISSION_BITS)
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        _changed("write", path)

    def make_dir(
        self, path: Path, *, mode: int = 0o755, parents: bool = True, exist_ok: bool = True
    ) -> None:
        if not parents:
            path.mkdir(mode=mode, exist_ok=exist_ok)
            _changed("mkdir", path)
            return
        # pathlib applies the mode only to the leaf and creates the parents
        # with the process umask, which is how a 0700 secrets directory ends up
        # under a 0755 one.
        missing = []
        current = path
        while not current.exists() and current != current.parent:
            missing.append(current)
            current = current.parent
        if not missing and not exist_ok:
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(path))
        # The leaf is created last and without exist_ok, so a racer that
        # creates it after the walk above makes this call fail rather than
        # both callers believing the directory is theirs.
        for directory in reversed(missing):
            directory.mkdir(mode=mode)
        if missing:
            _changed("mkdir", path)

    def remove(self, path: Path, *, missing_ok: bool = True) -> None:
        path.unlink(missing_ok=missing_ok)
        _changed("remove", path)

    def remove_tree(self, path: Path) -> None:
        shutil.rmtree(path)
        _changed("remove_tree", path)

    def move(self, source: Path, destination: Path) -> None:
        shutil.move(str(source), str(destination))
        _changed("move", source, destination)

    def rename(self, source: Path, destination: Path) -> None:
        os.rename(source, destination)
        _changed("rename", source, destination)

    def copy_tree(self, source: Path, destination: Path) -> None:
        # symlinks=True: a link in the source tree is copied as a link rather
        # than followed, so a source containing a link to /etc/passwd does not
        # deposit its contents inside the deployment.
        shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True)
        _changed("copy_tree", source, destination)

    def chmod(self, path: Path, mode: int, *, follow_symlinks: bool = True) -> None:
        if follow_symlinks:
            path.chmod(mode)
        else:
            _chmod_entry(path, mode)
        _changed("chmod", path)

    def set_owner(self, path: Path, *, user: str, group: str, mode: int) -> None:
        _set_owner_entry(path, pwd.getpwnam(user).pw_uid, grp.getgrnam(group).gr_gid, mode)
        _changed("chown", path)

    def symlink(self, target: Path, link: Path) -> None:
        # rename(2) replaces the destination in one step, so the new link is
        # built beside the old one and renamed over it. The random suffix is
        # not for uniqueness alone: symlink(2) never follows or reuses an
        # existing entry, so a name planted in advance makes this fail instead
        # of redirecting it, and an unpredictable name makes planting useless.
        temporary = link.with_name(f"{link.name}.tmp-{os.urandom(6).hex()}")
        os.symlink(target, temporary)
        try:
            # os.replace, not shutil.move: when the destination is a link to a
            # directory, move() puts the new link *inside* that directory.
            os.replace(temporary, link)
        finally:
            # After a successful rename the temporary name is gone, so this
            # only ever removes a link that failed to take its place.
            if os.path.lexists(temporary):
                temporary.unlink()
        _changed("symlink", link, target)


class DryRunFileSystem(FileSystem):
    """
    Records what would have happened and does nothing.

    This is the other half of ``--dry-run``. Without it the flag is a claim the
    program cannot keep, which is worse than not offering it.
    """

    def __init__(self, on_skip: Callable[[str], None] | None = None):
        """
        Args:
            on_skip: Called with a description of each change not made.
        """
        self._on_skip = on_skip
        self.skipped: list[str] = []

    def _skip(self, description: str) -> None:
        """
        Record a change that a real run would have made.

        Args:
            description: What would have happened.
        """
        self.skipped.append(description)
        if self._on_skip is not None:
            self._on_skip(description)

    def write_text(
        self,
        path: Path,
        content: str,
        *,
        mode: int = 0o644,
        owner: tuple[int, int] | None = None,
    ) -> None:
        self._skip(f"would write {path} ({len(content)} bytes, mode {mode:o})")

    def make_dir(
        self, path: Path, *, mode: int = 0o755, parents: bool = True, exist_ok: bool = True
    ) -> None:
        self._skip(f"would create directory {path} (mode {mode:o})")

    def remove(self, path: Path, *, missing_ok: bool = True) -> None:
        self._skip(f"would delete {path}")

    def remove_tree(self, path: Path) -> None:
        self._skip(f"would delete directory {path} and everything under it")

    def move(self, source: Path, destination: Path) -> None:
        self._skip(f"would move {source} to {destination}")

    def rename(self, source: Path, destination: Path) -> None:
        self._skip(f"would rename {source} to {destination}")

    def copy_tree(self, source: Path, destination: Path) -> None:
        self._skip(f"would copy {source} to {destination}")

    def chmod(self, path: Path, mode: int, *, follow_symlinks: bool = True) -> None:
        self._skip(f"would set {path} to mode {mode:o}")

    def set_owner(self, path: Path, *, user: str, group: str, mode: int) -> None:
        self._skip(f"would give {path} to {user}:{group} (mode {mode:o})")

    def symlink(self, target: Path, link: Path) -> None:
        self._skip(f"would link {link} to {target}")


class RecordingFileSystem(RealFileSystem):
    """
    A real filesystem that also records what it did.

    Used in tests that want the changes to happen inside ``tmp_path`` and also
    want to assert on them.
    """

    def __init__(self) -> None:
        self.changes: list[tuple[str, Path]] = []

    def write_text(
        self,
        path: Path,
        content: str,
        *,
        mode: int = 0o644,
        owner: tuple[int, int] | None = None,
    ) -> None:
        self.changes.append(("write", path))
        super().write_text(path, content, mode=mode, owner=owner)

    def make_dir(
        self, path: Path, *, mode: int = 0o755, parents: bool = True, exist_ok: bool = True
    ) -> None:
        self.changes.append(("mkdir", path))
        super().make_dir(path, mode=mode, parents=parents, exist_ok=exist_ok)

    def remove(self, path: Path, *, missing_ok: bool = True) -> None:
        self.changes.append(("remove", path))
        super().remove(path, missing_ok=missing_ok)

    def remove_tree(self, path: Path) -> None:
        self.changes.append(("remove_tree", path))
        super().remove_tree(path)

    def move(self, source: Path, destination: Path) -> None:
        self.changes.append(("move", source))
        super().move(source, destination)

    def rename(self, source: Path, destination: Path) -> None:
        self.changes.append(("rename", source))
        super().rename(source, destination)

    def copy_tree(self, source: Path, destination: Path) -> None:
        self.changes.append(("copy_tree", source))
        super().copy_tree(source, destination)

    def chmod(self, path: Path, mode: int, *, follow_symlinks: bool = True) -> None:
        self.changes.append(("chmod", path))
        super().chmod(path, mode, follow_symlinks=follow_symlinks)

    def set_owner(self, path: Path, *, user: str, group: str, mode: int) -> None:
        self.changes.append(("chown", path))
        super().set_owner(path, user=user, group=group, mode=mode)

    def symlink(self, target: Path, link: Path) -> None:
        self.changes.append(("symlink", link))
        super().symlink(target, link)


_default_fs: FileSystem | None = None


def get_fs() -> FileSystem:
    """
    Return the process-wide filesystem, creating the real one on first use.

    Returns:
        The active filesystem.
    """
    global _default_fs
    if _default_fs is None:
        _default_fs = RealFileSystem()
    return _default_fs


def set_fs(filesystem: FileSystem | None) -> None:
    """
    Replace the process-wide filesystem.

    Args:
        filesystem: The filesystem to install, or None to reset to the real one.
    """
    global _default_fs
    _default_fs = filesystem


def is_rehearsal() -> bool:
    """
    Report whether this invocation is a dry run.

    Most code should not ask: routing a change through the filesystem or the
    command runner is enough, and both refuse on their own. This exists for
    the one kind of change neither seam covers, a write to the SQLite store,
    where the honest answer is to roll the transaction back rather than to
    pretend the seam owns it.

    Returns:
        True when a rehearsing filesystem is installed.
    """
    return isinstance(get_fs(), DryRunFileSystem)
