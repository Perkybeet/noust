# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
One edit of Noust DNS at a time on a server.

An edit changes the store, writes a zone file and asks Knot to read it, and two of them
interleaved (the console and the command line, or two requests) would publish a zone file that
holds neither's change. The lock is ``flock`` on a file beside the store, so it is released by the
kernel when the process holding it ends however it ends, and a second edit waits for the first
instead of failing: an edit takes a few seconds at most. Under ``--dry-run`` nothing is taken.
"""

from __future__ import annotations

import errno
import fcntl
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from noust.core.applock import locks_directory
from noust.core.exceptions import ServiceError
from noust.core.fs import get_fs, is_rehearsal

__all__ = ["LOCK_NAME", "editing"]

#: The lock's file, in the directory the application locks are in.
LOCK_NAME = "noust-dns.lock"

_threads = threading.RLock()


@contextmanager
def editing(
    timeout: float = 60.0,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Iterator[None]:
    """
    Hold the right to change Noust DNS on this server.

    Args:
        timeout: Seconds to wait for another edit to finish.
        sleep: Waits (tests pass one that does not).
        clock: The clock the wait is counted on.

    Yields:
        Nothing: the edit runs inside the block.

    Raises:
        ServiceError: Another edit did not finish in time.
    """
    if is_rehearsal():
        yield
        return
    with _threads:
        path = locks_directory() / LOCK_NAME
        get_fs().make_dir(path.parent, mode=0o700)
        # O_NOFOLLOW: a link planted in the directory must not turn taking the lock into a
        # write somewhere else.
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        deadline = clock() + timeout
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if error.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                        raise
                    if clock() > deadline:
                        raise ServiceError(
                            "Another change to Noust DNS has not finished",
                            details="Only one change to the zones is made at a time. Wait for "
                            "it, then retry; the lock is released when its process ends.",
                        ) from None
                    sleep(0.1)
            yield
        finally:
            os.close(fd)
