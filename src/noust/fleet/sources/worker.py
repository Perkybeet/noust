# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one background thread of the central's GitHub sources, in the console's process.

Three things keep time here, and a thread is the right shape for them because GitHub waits seconds
for an answer and a server minutes:

- **The leases** (:class:`~noust.fleet.sources.keeper.LeaseKeeper`) are looked at every minute, so a
  server always holds access with at least twenty-five minutes left.
- **The deliveries** (:class:`~noust.fleet.sources.forward.Forwarder`) are handed to the servers
  as they come and tried again when a server is down. The webhook only writes them down and wakes
  this thread, so GitHub is answered at once.
- **The follows** (:class:`~noust.fleet.sources.follow.Follower`) are looked at every few seconds
  while there is something to follow, and not at all otherwise.

A look that fails is logged and never ends the thread, and one of the three failing never keeps
the others from being done. The thread is a daemon: the console's stopping ends it, and what it
was in the middle of is in the store, so the next console picks it up.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable

from noust.core.exceptions import NoustError
from noust.fleet.aggregate import Failure
from noust.fleet.sources.follow import Follower
from noust.fleet.sources.forward import Forwarder
from noust.fleet.sources.keeper import LOOK_EVERY, LeaseKeeper

logger = logging.getLogger(__name__)

__all__ = ["SourcesWorker", "get_worker", "start_worker", "stop_worker"]

#: Seconds between two looks while there are deliveries waiting or deployments followed.
BUSY_EVERY = 5.0

#: Seconds between two looks when there is nothing to do but keep the leases.
IDLE_EVERY = 30.0

#: Seconds between two housekeepings of what ended long ago.
PURGE_EVERY = 3600.0


class SourcesWorker:
    """
    Keeps the leases, hands over the events and follows what the servers started.

    Args:
        keeper: The keeper of leases.
        forwarder: The deliveries.
        follower: The follows.
        clock: Monotonic seconds (tests move it).
    """

    def __init__(
        self,
        keeper: LeaseKeeper | None = None,
        forwarder: Forwarder | None = None,
        follower: Follower | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.keeper = keeper or LeaseKeeper()
        self.forwarder = forwarder or Forwarder(keeper=self.keeper)
        self.follower = follower or Follower()
        self._clock = clock
        self._next_sweep = 0.0
        self._next_purge = 0.0
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> None:
        """
        Do what is due: renew leases, hand over events, look at what is followed.

        Each part is done on its own: one that fails is logged and the others are still done.
        """
        now = self._clock()
        if now >= self._next_sweep:
            self._next_sweep = now + LOOK_EVERY
            self._guard("leases", self.keeper.sweep)
        self._guard("deliveries", self.forwarder.deliver_due)
        self._guard("follows", self.follower.step)
        if now >= self._next_purge:
            self._next_purge = now + PURGE_EVERY
            self._guard("housekeeping", self._purge)

    def _purge(self) -> None:
        """Forget what was settled long ago."""
        self.forwarder.forwards.purge()
        self.follower.follows.purge()

    @staticmethod
    def _guard(what: str, step: Callable[[], object]) -> None:
        """
        Run one part, logging what it fails with.

        Args:
            what: Which part, for the log.
            step: The part.
        """
        try:
            step()
        # The boundary of a background thread: what a part can fail with (the store, the registry
        # of servers, a sealed central, a server's answer) must not end it, and is logged.
        except (NoustError, Failure, OSError, sqlite3.Error, RuntimeError):
            logger.exception("The GitHub sources' %s failed", what)

    def busy(self) -> bool:
        """
        Returns:
            Whether a delivery waits or a deployment is followed.
        """
        try:
            return bool(self.forwarder.forwards.pending() or self.follower.follows.active())
        except (NoustError, sqlite3.Error):
            return False

    def wake(self) -> None:
        """Look now instead of at the next turn (an event was written down)."""
        self._wake.set()

    def start(self) -> None:
        """Start in the background. Starting twice is a no-op."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="noust-fleet-sources", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop."""
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self._thread = None

    def _loop(self) -> None:
        """Look, wait, look again, until stopped."""
        while not self._stop.is_set():
            self.tick()
            self._wake.wait(BUSY_EVERY if self.busy() else IDLE_EVERY)
            self._wake.clear()


_worker: SourcesWorker | None = None
_lock = threading.Lock()


def get_worker() -> SourcesWorker:
    """
    Returns:
        The process-wide worker, made on first use and not started.
    """
    global _worker
    with _lock:
        if _worker is None:
            _worker = SourcesWorker()
        return _worker


def set_worker(worker: SourcesWorker | None) -> None:
    """
    Replace the process-wide worker (tests).

    Args:
        worker: The worker, or None for a fresh one on next use.
    """
    global _worker
    with _lock:
        _worker = worker


def start_worker() -> SourcesWorker:
    """
    Start the console's worker.

    Returns:
        The running worker, for stopping it.
    """
    worker = get_worker()
    worker.start()
    return worker


def stop_worker() -> None:
    """Stop the console's worker, if one runs."""
    with _lock:
        worker = _worker
    if worker is not None:
        worker.stop()
