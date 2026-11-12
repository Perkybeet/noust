# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The thread that keeps the central's copies up to date, in the console's process (item 195).

Every ``poll_seconds`` (a minute by default) each server that is followed is asked, cheaply, whether
its list of copies changed (its fingerprint): that is how a copy a server has just finished reaches
the central within a minute and not at the next pass, and how a central that was off collects what it
missed when it comes back, because a fingerprint it never saw is a list it reads in full. Every
``pass_hours`` the whole list is read whether or not the server says it changed.

A pass is a thread of its own, a few at a time, so a server of gigabytes does not keep another's
fresh copy waiting; two passes over one server never run together
(:meth:`~noust.fleet.collect.collector.NodeCollector.collect`). A server that fails is asked less
and less often (up to every quarter of an hour), and one that is down costs the others nothing.

Nothing runs while the central's secrets are sealed and locked (there is no tunnel to open), while
no server is followed, or when the central has none. The thread is a daemon: the console stopping
ends it, a transfer in the middle stops between two pieces and resumes from the part it left.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from noust.core.store import NoustStore, get_store
from noust.fleet.collect.collector import NodeCollector, PassResult
from noust.fleet.collect.settings import Follows, SettingsStore

__all__ = ["CollectWorker", "get_worker", "start_worker", "stop_worker"]

_log = logging.getLogger(__name__)

#: Passes that may run at once, over different servers.
MAX_PASSES = 3

#: The longest a server that keeps failing waits between two looks, in seconds.
MAX_BACKOFF = 900.0

#: Seconds the loop sleeps when nothing is due and collecting is off.
IDLE_SECONDS = 30.0


class CollectWorker:
    """
    Make passes over the followed servers as they come due.

    Args:
        collector: The collector; one with the process's store by default.
        store: The store; the process-wide one by default.
        clock: Monotonic seconds (tests move it).
        reachable: Whether a tunnel can be opened at all (a locked central cannot).
    """

    def __init__(
        self,
        collector: NodeCollector | None = None,
        *,
        store: NoustStore | None = None,
        clock: Callable[[], float] = time.monotonic,
        reachable: Callable[[NoustStore], bool] | None = None,
    ) -> None:
        self._store = store
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.collector = collector or NodeCollector(should_stop=self._stop.is_set)
        self._clock = clock
        self._reachable = reachable or _can_reach
        self._next: dict[str, float] = {}
        self._running: dict[str, Future[PassResult]] = {}
        self._forced: set[str] = set()
        self._lock = threading.Lock()
        self._pool: ThreadPoolExecutor | None = None
        self._thread: threading.Thread | None = None

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def wake(self, node: str | None = None, *, force: bool = False) -> None:
        """
        Look now instead of at the next turn.

        Args:
            node: Only this server; every followed one when None.
            force: Read the whole list even when the server says nothing changed.
        """
        with self._lock:
            names = [node] if node else [n.name for n in self.store.list_nodes()]
            for name in names:
                self._next[name] = 0.0
                if force:
                    self._forced.add(name)
        self._wake.set()

    def tick(self) -> int:
        """
        Start a pass over every followed server that is due.

        Returns:
            How many were started.
        """
        if not self._reachable(self.store):
            return 0
        settings = SettingsStore(self.store).get()
        follows = Follows(self.store)
        now = self._clock()
        started = 0
        for node in self.store.list_nodes():
            if not follows.follows(node.name, settings):
                continue
            with self._lock:
                running = self._running.get(node.name)
                if (running is not None and not running.done()) or self._next.get(
                    node.name, 0.0
                ) > now:
                    continue
                force = node.name in self._forced
                self._forced.discard(node.name)
                if self._pool is None:
                    self._pool = ThreadPoolExecutor(MAX_PASSES, thread_name_prefix="noust-collect")
                self._running[node.name] = self._pool.submit(self._pass, node.name, force)
                started += 1
        return started

    def _pass(self, node: str, force: bool) -> PassResult:
        """
        Make one pass, and decide when the server is looked at next.

        Args:
            node: The server.
            force: Read the whole list.

        Returns:
            How it went.
        """
        settings = SettingsStore(self.store).get()
        try:
            result = self.collector.collect(node, force=force)
        except Exception:  # noqa: BLE001 - the boundary of a background thread: log and go on
            _log.exception("A pass of collecting over %s failed", node)
            result = PassResult(node, "failed", code="error")
        failing = result.state == "failed"
        wait = float(settings.poll_seconds)
        if failing:
            from noust.fleet.collect.records import NodeStates

            wait = min(wait * (2 ** min(NodeStates(self.store).get(node).failures, 6)), MAX_BACKOFF)
        with self._lock:
            self._next[node] = max(self._next.get(node, 0.0), self._clock() + wait)
        return result

    def busy(self) -> bool:
        """
        Returns:
            Whether a pass is running.
        """
        with self._lock:
            return any(not future.done() for future in self._running.values())

    def start(self) -> None:
        """Start in the background. Starting twice is a no-op."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="noust-collect", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop; a pass in the middle of a piece stops after it and resumes next time."""
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=10.0)
        self._thread = None
        pool = self._pool
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    def _loop(self) -> None:
        """Look, wait, look again, until stopped."""
        while not self._stop.is_set():
            try:
                self.tick()
                settings = SettingsStore(self.store).get()
                pause = float(settings.poll_seconds) if settings.enabled else IDLE_SECONDS
            except Exception:  # noqa: BLE001 - the boundary of a background thread: log and go on
                _log.exception("Collecting could not look at the servers")
                pause = IDLE_SECONDS
            self._wake.wait(min(pause, 30.0))
            self._wake.clear()


def _can_reach(store: NoustStore) -> bool:
    """
    Tell whether a tunnel could be opened at all.

    Args:
        store: The central's store.

    Returns:
        True when at least one server is registered and this central's secrets can be read.
    """
    from noust.fleet.probe import can_reach

    return can_reach(store)


_worker: CollectWorker | None = None
_lock = threading.Lock()


def get_worker() -> CollectWorker:
    """
    Returns:
        The process-wide worker, made on first use and not started.
    """
    global _worker
    with _lock:
        if _worker is None:
            _worker = CollectWorker()
        return _worker


def start_worker() -> CollectWorker:
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
