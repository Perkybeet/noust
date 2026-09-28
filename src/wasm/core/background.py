# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
One worker thread per purpose, in order, drained at process exit.

Deploy notifications and GitHub deployment statuses both talk to a remote
service about a deployment that must not wait for them, and both used to
start threads of their own: one per event for notifications, which let a fast
"failed" overtake the "Deploying" sent a moment earlier, and a daemon worker
for statuses that a CLI process abandoned mid-request when it exited.

A :class:`BackgroundQueue` is the one implementation of that: tasks run on a
single daemon thread, first in first out, and the first submission registers
one :mod:`atexit` hook for the whole process that waits for every queue to
empty, under one shared cap (:data:`DRAIN_TIMEOUT`) - enough for one slow
channel's own timeout to be felt, never enough to hang a `wasm` command that
has already told the operator what happened.
"""

from __future__ import annotations

import atexit
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Final

logger = logging.getLogger(__name__)

#: Longest a process waits at exit, across every queue together, for work
#: already submitted. Shared rather than per queue or per task, so a process
#: that queued several things just before exiting cannot multiply the delay.
DRAIN_TIMEOUT: Final[float] = 15.0

_registry_lock = threading.Lock()
_queues: list[BackgroundQueue] = []
_atexit_registered = False


class BackgroundQueue:
    """
    Run submitted tasks on one daemon thread, in submission order.

    A task handles its own expected failures; one that raises anyway ends the
    worker with a traceback (the thread excepthook logs it, so a bug is seen)
    and a new worker takes over what is left.

    Args:
        name: The worker thread's name, as it shows in a traceback.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._tasks: deque[Callable[[], None]] = deque()
        self._unfinished = 0
        self._condition = threading.Condition()
        self._worker: threading.Thread | None = None

    def submit(self, task: Callable[[], None]) -> None:
        """
        Queue a task and return at once.

        Args:
            task: What to run; its return value is ignored.
        """
        with self._condition:
            self._tasks.append(task)
            self._unfinished += 1
            self._condition.notify_all()
            self._ensure_worker()
        _register(self)

    def drain(self, timeout: float | None = None) -> bool:
        """
        Wait until every task submitted so far has run.

        Args:
            timeout: Seconds to wait at most; None waits as long as it takes.

        Returns:
            True when the queue emptied, False when the timeout won.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            if self._unfinished:
                self._ensure_worker()
            while self._unfinished:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    @property
    def pending(self) -> int:
        """
        Tasks submitted and not yet finished, the running one included.

        Returns:
            The count.
        """
        with self._condition:
            return self._unfinished

    def _ensure_worker(self) -> None:
        """Start the worker when there is none alive. Called with the condition held."""
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._run, name=self.name, daemon=True)
            self._worker.start()

    def _run(self) -> None:
        """Run tasks until the process ends."""
        while True:
            with self._condition:
                while not self._tasks:
                    self._condition.wait()
                task = self._tasks.popleft()
            completed = False
            try:
                task()
                completed = True
            finally:
                with self._condition:
                    self._unfinished -= 1
                    self._condition.notify_all()
                    if not completed:
                        # The exception propagates and ends this thread, so
                        # the traceback is seen; what was queued behind the
                        # task still runs, on a new worker.
                        self._worker = None
                        if self._tasks:
                            self._ensure_worker()


def _register(queue: BackgroundQueue) -> None:
    """
    Remember a queue for the exit drain, registering that drain once per process.

    Args:
        queue: The queue that just received work.
    """
    global _atexit_registered
    with _registry_lock:
        if queue not in _queues:
            _queues.append(queue)
        if not _atexit_registered:
            atexit.register(drain_all)
            _atexit_registered = True


def drain_all(timeout: float = DRAIN_TIMEOUT) -> bool:
    """
    Wait for every queue that ever received work, under one shared budget.

    Registered with :mod:`atexit`, so a CLI process - which can exit within
    milliseconds of a deployment finishing - still sends what it queued. A
    long-running process only reaches this at its own shutdown, when its
    queues have ordinarily long since emptied.

    Args:
        timeout: Total seconds across every queue, not per queue.

    Returns:
        True when every queue emptied in time.
    """
    deadline = time.monotonic() + timeout
    with _registry_lock:
        queues = list(_queues)
    for queue in queues:
        if not queue.drain(max(deadline - time.monotonic(), 0.0)):
            logger.warning(
                "Gave up after %.0fs waiting for %d background task(s) on %s",
                timeout,
                queue.pending,
                queue.name,
            )
            return False
    return True
