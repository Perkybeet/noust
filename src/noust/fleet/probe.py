# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The central's reachability probe: every node asked every 30 seconds.

A node that goes down while nobody has the fleet open used to be noticed at
the next look, which could be the next morning. The probe asks every node the
``servers`` view of the one aggregator (:mod:`noust.fleet.aggregate`) on a
timer, and the aggregator does the rest exactly as it does for a person
looking: it records the node's status, counts a silence towards an outage,
announces the outage once it lasts, and the recovery once it ends. Nothing
here decides what an outage is; it only makes sure someone keeps asking.

**When.** In the monitor daemon, always: it runs as long as the server does.
In the console, only while at least one console has the event stream open, so
an idle console does not dial every node every half minute for nobody.

**Once.** A lock file makes the probe single-flight across processes: the
daemon holds it for as long as it probes, and the console takes it for one
round at a time, so a console never probes beside a daemon that does and hands
over the moment one starts. Within a process one thread probes, and a round
still running when the next is due is not doubled.

**Not when it cannot.** A central whose secrets are sealed and locked cannot
open a tunnel; the probe skips its round without holding the lock (the unlocked
console can then take it), and so does a central with no node at all.
"""

from __future__ import annotations

import errno
import fcntl
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path

from noust.core.store import NoustStore, get_store

log = logging.getLogger(__name__)

#: Seconds between rounds.
PROBE_INTERVAL_SECONDS = 30.0

#: Seconds each node may take to answer a probe.
PROBE_NODE_TIMEOUT = 10.0

#: The lock file, beside the application locks. The leading dot keeps it apart
#: from them: an application's lock is named after its domain, never hidden.
LOCK_FILE = ".fleet-probe.lock"

#: Who the nodes' audit logs say asked.
PROBE_ACTOR = "noust (reachability probe)"

_LOCK_FILE_MODE = 0o600


def _lock_path() -> Path:
    """
    Return where the probe's lock file is.

    Returns:
        ``<state dir>/locks/.fleet-probe.lock``.
    """
    from noust.core.applock import locks_directory
    from noust.core.fs import get_fs

    directory = locks_directory()
    get_fs().make_dir(directory, mode=0o700)
    return directory / LOCK_FILE


def _can_reach(store: NoustStore) -> bool:
    """
    Tell whether a round could reach anything.

    Args:
        store: The central's store.

    Returns:
        True when at least one node is registered and this central's secrets
        can be read.
    """
    if not store.list_nodes():
        return False
    from noust.core import sealing
    from noust.core.secrets import secrets_dir

    root = secrets_dir()
    return not (sealing.is_sealed(root) and not sealing.is_unlocked(root))


def _gather_round() -> None:
    """Ask every node the ``servers`` view, waiting for all of them."""
    from noust.fleet.aggregate import Asker, gather

    gather(
        "servers",
        asker=Asker(actor=PROBE_ACTOR, scope="read"),
        refresh=True,
        node_timeout=PROBE_NODE_TIMEOUT,
        deadline=None,
    )


class ReachabilityProbe:
    """
    Keeps asking every node whether it answers.

    Args:
        daemon: This is the monitor daemon, which keeps the lock while it
            probes; the console takes it one round at a time.
        active: Whether a round is wanted now; always, by default.
        interval: Seconds between rounds.
        store: The store; the process-wide one by default.
        lock_path: The lock file; the one beside the application locks by default.
        gather_round: What a round does (tests).
    """

    def __init__(
        self,
        *,
        daemon: bool,
        active: Callable[[], bool] = lambda: True,
        interval: float = PROBE_INTERVAL_SECONDS,
        store: NoustStore | None = None,
        lock_path: Path | None = None,
        gather_round: Callable[[], None] = _gather_round,
    ) -> None:
        self.daemon = daemon
        self._active = active
        self.interval = interval
        self._store = store
        self._lock_path = lock_path
        self._gather = gather_round
        self._round = threading.Lock()
        self._fd: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def store(self) -> NoustStore:
        """The store nodes are read from."""
        return self._store or get_store()

    @property
    def holding(self) -> bool:
        """Whether this probe holds the lock now."""
        return self._fd is not None

    # ------------------------------------------------------------ the lock

    def _take(self) -> bool:
        """
        Take the lock, or report that another process holds it.

        Returns:
            True when this probe holds it.

        Raises:
            OSError: The lock file could not be opened.
        """
        if self._fd is not None:
            return True
        path = self._lock_path or _lock_path()
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, _LOCK_FILE_MODE)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            os.close(fd)
            if error.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                return False
            raise
        self._fd = fd
        return True

    def _give_up(self) -> None:
        """Release the lock, if held."""
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    # ------------------------------------------------------------ a round

    def run_once(self) -> bool:
        """
        Probe every node, if a round is wanted, possible and this process's to do.

        Returns:
            True when a round ran.
        """
        if not self._round.acquire(blocking=False):
            return False
        try:
            if not self._active() or not _can_reach(self.store):
                self._give_up()
                return False
            if not self._take():
                return False
            try:
                self._gather()
            finally:
                if not self.daemon:
                    self._give_up()
            return True
        finally:
            self._round.release()

    def _loop(self) -> None:
        """Run a round every interval until stopped. The thread's error boundary."""
        while not self._stop.wait(self.interval):
            try:
                self.run_once()
            # The boundary of a background thread: whatever one round hits,
            # the next one must still run, and the failure is logged with its
            # traceback instead of killing the thread silently.
            except Exception:
                log.exception("A fleet reachability round failed")

    def start(self) -> None:
        """Start probing in the background. Starting twice is a no-op."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="noust-fleet-probe", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop probing and release the lock."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=PROBE_NODE_TIMEOUT + 5.0)
        self._thread = None
        with self._round:
            self._give_up()


def start_probe(*, daemon: bool, active: Callable[[], bool] | None = None) -> ReachabilityProbe:
    """
    Start the probe of this process.

    Args:
        daemon: This is the monitor daemon.
        active: Whether a round is wanted now; always when None.

    Returns:
        The running probe, for stopping it.
    """
    probe = ReachabilityProbe(daemon=daemon, active=active or (lambda: True))
    probe.start()
    return probe


__all__ = [
    "PROBE_INTERVAL_SECONDS",
    "ReachabilityProbe",
    "start_probe",
]
