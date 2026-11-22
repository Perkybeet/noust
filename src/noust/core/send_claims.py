# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Who is sending an owed copy to a destination right now (item 201).

An owed copy is sent again by whoever gets to it: the timer that runs ``noust backup resend``, the
central's own scheduler, the next scheduled run of the same thing and a person who has just mended the
network. Two of them that meet (a timer and a pass by hand, or two timers after a cut) read the same owed
row, uploaded the copy twice and told the operator twice. The one place that decides "this copy is
in hand" is here, and :class:`~noust.managers.backup_resend.Resender` asks it for every copy.

The claim is ``flock`` on a file per copy and destination, beside the store and never in an application's
tree, for the reasons the application locks are (:mod:`noust.core.applock`):

- The kernel releases it when the process holding it ends, however it ends, so a pass that was killed
  holds nothing and a copy is never stuck for want of someone to remove a file: this is the expiry of a
  claim, and nothing in it is a clock to be wrong. A pass that is alive and stuck is bounded by the
  timeouts of what it uploads through, not by this.
- A second claimant is refused at once, never made to wait: what a pass does not take now it takes at its
  next try, which is minutes away, and a pass that waited would only send what the first had just sent.
- The file is removed when the claim ends, under the lock, and a claimant that opened it just before
  checks that the file it locked is still the one at the path; otherwise a copy that is owed for a year to
  a destination that is down would leave a file for every copy it ever was.
- Under ``--dry-run`` nothing is created: a rehearsal changes nothing, and a lock file is a change.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from noust.core.applock import LOCKS_DIR
from noust.core.fs import get_fs, is_rehearsal

if TYPE_CHECKING:
    from noust.core.store import NoustStore

__all__ = ["claim", "claim_directory"]

_DIR_MODE = 0o700
_FILE_MODE = 0o600

#: How many times a claim opens the file again when the one it locked was removed under it. Each of those
#: is another claimant that took and released the copy in between, so more than a few is not a thing.
_ATTEMPTS = 4

_BUSY = (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES)


def claim_directory(store: NoustStore) -> Path:
    """
    Args:
        store: The store the owed copies are kept in.

    Returns:
        Where the claims of the passes over that store are, beside it with the application locks: the
        passes of one server share the store, so they share this.
    """
    return store.db_path.parent / LOCKS_DIR


def _file_name(kind: str, item_id: str, destination: str) -> str:
    """
    Args:
        kind: The kind of copy.
        item_id: Its identifier as its manager gives it (a dump is ``engine:file``).
        destination: Where it goes, a name the operator chose.

    Returns:
        A name that is one file in one directory whatever the three hold: a digest, never the text.
    """
    digest = hashlib.sha256("\0".join((kind, item_id, destination)).encode("utf-8"))
    return f"send-{digest.hexdigest()[:32]}.lock"


def _is_the_file_at(path: Path, descriptor: int) -> bool:
    """
    Args:
        path: Where the claim's file is.
        descriptor: The descriptor that holds the lock.

    Returns:
        Whether the file that is locked is the one at the path, and not one a claimant before removed.
    """
    try:
        at_path = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return False
    held = os.fstat(descriptor)
    return (at_path.st_dev, at_path.st_ino) == (held.st_dev, held.st_ino)


def _take(path: Path, kind: str, item_id: str, destination: str) -> int | None:
    """
    Lock the copy's file.

    Args:
        path: The file.
        kind: The kind of copy.
        item_id: Its identifier.
        destination: Where it goes.

    Returns:
        The descriptor that holds the lock, or None when another holds it.

    Raises:
        OSError: The file could not be opened (a link where it goes, a directory that cannot be written).
    """
    for _ in range(_ATTEMPTS):
        # O_NOFOLLOW: a link planted where the file goes must not turn recording who holds the claim into
        # a write somewhere else.
        descriptor = os.open(
            path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, _FILE_MODE
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            os.close(descriptor)
            if error.errno in _BUSY:
                return None
            raise
        if _is_the_file_at(path, descriptor):
            record = json.dumps(
                {
                    "kind": kind,
                    "item": item_id,
                    "destination": destination,
                    "pid": os.getpid(),
                    "since": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
            ).encode("utf-8")
            try:
                os.ftruncate(descriptor, 0)
                os.pwrite(descriptor, record, 0)
            except OSError:
                # Only for whoever looks at the file; the claim is held either way.
                pass
            return descriptor
        os.close(descriptor)
    return None


@contextmanager
def claim(directory: Path, kind: str, item_id: str, destination: str) -> Iterator[bool]:
    """
    Hold the right to send one copy to one destination, for as long as the block runs.

    Args:
        directory: Where the claims are (:func:`claim_directory`).
        kind: ``app``, ``database``, ``volume`` or ``noust``.
        item_id: The copy's identifier.
        destination: The destination.

    Yields:
        True when the copy is this pass's to send; False when another pass has it in hand, which is
        for the caller to leave alone, not to wait for.

    Raises:
        OSError: The claim's file could not be opened.
    """
    if is_rehearsal():
        yield True
        return
    get_fs().make_dir(directory, mode=_DIR_MODE)
    path = directory / _file_name(kind, item_id, destination)
    descriptor = _take(path, kind, item_id, destination)
    if descriptor is None:
        yield False
        return
    try:
        yield True
    finally:
        # Under the lock, so a claimant that opened the file a moment ago finds another at the path.
        get_fs().remove(path, missing_ok=True)
        os.close(descriptor)
