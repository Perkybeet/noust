# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The state directory the console keeps its secrets, token and sessions in.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from pathlib import Path

from noust.core.exceptions import SecurityError
from noust.core.fs import get_fs, is_rehearsal
from noust.web.auth.constants import DIR_MODE, FILE_MODE, STATE_DIR_ENV


def ensure_state_dir(path: Path) -> None:
    """
    Create the state directory with owner-only permissions.

    Through the filesystem seam, like every other change Noust makes, so a
    ``--dry-run`` reports the directory it would create and creates nothing.

    Args:
        path: Directory that must exist and be private.

    Raises:
        SecurityError: When the directory cannot be created.
    """
    fs = get_fs()
    try:
        if not path.is_dir():
            fs.make_dir(path, mode=DIR_MODE, parents=True)
        if not path.is_dir():
            if is_rehearsal():
                # The seam declined to create it; there is nothing to tighten.
                return
            # make_dir leaves an existing entry alone, so a file squatting on
            # the name is only noticed here.
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), str(path))
        if stat.S_IMODE(path.stat().st_mode) != DIR_MODE:
            fs.chmod(path, DIR_MODE)
    except OSError as exc:
        raise SecurityError(
            f"Cannot create the Noust web state directory {path}",
            details=(
                "The web panel stores its signing key, sessions and audit log there. "
                f"Create it as root with 'install -d -m 700 {path}', or point the panel "
                f"somewhere writable with {STATE_DIR_ENV}=/path/to/dir."
            ),
        ) from exc


def state_file_exists(path: Path) -> bool:
    """
    Report whether a file of the state directory exists.

    ``Path.exists`` treats only "not found" as absence and raises
    ``PermissionError`` when the directory cannot be searched, which is what a
    user other than root meets in a root-owned ``/etc/noust``. That is not an
    answer to "is there a token"; it is the same refusal every other state
    file gives, so it ends the same way.

    Args:
        path: A file under the state directory.

    Returns:
        True when the file exists.

    Raises:
        SecurityError: When the directory cannot be searched.
    """
    try:
        return path.exists()
    except OSError as exc:
        raise SecurityError(
            f"Cannot look for {path}",
            details=(
                "The web panel keeps its credentials where only its owner can read. "
                f"Run as root, or set {STATE_DIR_ENV} to a directory you own."
            ),
            output=str(exc),
        ) from exc


def write_private_file(path: Path, content: str) -> None:
    """
    Write a file that only its owner can read.

    Through the filesystem seam, which writes beside the destination and
    renames over it with the mode set at creation: a running console
    re-reads the signing key and the token hash whenever they change (see
    :class:`TokenManager`), and a truncate-then-write would let it read the
    empty file in between. Under ``--dry-run`` nothing is written; the
    caller keeps what it meant to write in memory for the rest of the run.

    Args:
        path: Destination file.
        content: Text to write.

    Raises:
        SecurityError: When the file cannot be written.
    """
    ensure_state_dir(path.parent)
    try:
        get_fs().write_text(path, content, mode=FILE_MODE)
    except OSError as exc:
        raise SecurityError(
            f"Cannot write {path}",
            details=(
                "Run the web panel as root, or set "
                f"{STATE_DIR_ENV} to a directory the current user owns."
            ),
        ) from exc


def _generation(secret_material: str) -> str:
    """
    Digest a secret into a label that says which issue of it is in force.

    Args:
        secret_material: A stored hash or a signing key.

    Returns:
        The first 16 hex characters of its SHA-256: enough to tell two issues
        apart, and nothing a caller could turn back into the material.
    """
    return hashlib.sha256(secret_material.encode()).hexdigest()[:16]


def _file_stamp(path: Path) -> tuple[int, int, int, int] | None:
    """
    Identify one version of a state file without reading it.

    Args:
        path: The file to identify.

    Returns:
        Inode, size and modification and change times, which together change
        on every :func:`write_private_file` (it renames a new file into
        place), or None when the file cannot be stat'ed.
    """
    try:
        info = path.stat()
    except OSError:
        return None
    return (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
