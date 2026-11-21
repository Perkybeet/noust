# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Compose stacks of a server, brought under the gate of their start by the console after an update.

A stack that existed before the gate has no record of what it was accepted with and a unit that starts it
without asking. The console takes each one over a little after it starts (the package restarts it onto
the new version, as it does for the console's own unit): the record first, then the unit with the gate and
a ``daemon-reload``, and nothing else (:mod:`noust.deployers.compose_upgrade`). A stack that cannot be
resolved when it comes (Docker not up yet after a reboot, a file being replaced) is left as it was and
tried again at the next pass, which is why this runs on a clock of its own and not once.

It is the console and not the monitor that does it: the monitor runs with the system read-only and promises
to modify no file, and a unit is a file of ``/etc/systemd/system``. A server without the console is taken
over by the package's own step (``noust setup compose-gate``, which the update runs) or by hand.

The thread is a daemon: the console stopping ends it, and a pass is idempotent.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable

from noust.core.exceptions import NoustError
from noust.deployers import compose_upgrade

__all__ = ["ComposeGateWorker", "start_worker", "stop_worker"]

_log = logging.getLogger(__name__)

#: Seconds after the console starts before the first pass, so that it answers first, and between two
#: passes after that.
FIRST_AFTER = 20.0
EVERY = 600.0


class ComposeGateWorker:
    """
    Make a pass over the Compose stacks now and then.

    Args:
        bring: What a pass is: :func:`noust.deployers.compose_upgrade.bring_up_to_date` by default.
        first_after: Seconds before the first pass.
        every: Seconds between two.
    """

    def __init__(
        self,
        bring: Callable[[], list[compose_upgrade.Outcome]] = compose_upgrade.bring_up_to_date,
        *,
        first_after: float = FIRST_AFTER,
        every: float = EVERY,
    ) -> None:
        self._bring = bring
        self._first_after = first_after
        self._every = every
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> None:
        """Make one pass, and say in the log what came of it; a failure is a line, never an exit."""
        try:
            outcomes = self._bring()
        except (NoustError, OSError, sqlite3.Error) as exc:
            _log.warning(
                "The Compose stacks could not be brought under the check of their start: %s", exc
            )
            return
        for outcome in outcomes:
            if outcome.state == "done":
                _log.info(
                    "%s is under the check of its start (record %s, unit %s)",
                    outcome.domain,
                    "written" if outcome.recorded else "kept",
                    "written" if outcome.unit else "as it was",
                )
            elif outcome.state == "pending":
                _log.warning("%s was left as it was: %s", outcome.domain, outcome.detail)

    def _run(self) -> None:
        """Wait, make a pass, and again, until stopped."""
        wait = self._first_after
        while not self._stop.wait(wait):
            self.tick()
            wait = self._every

    def start(self) -> None:
        """Run passes in a daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="compose-gate", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the thread and wait for it a moment."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


_worker: ComposeGateWorker | None = None


def start_worker() -> ComposeGateWorker:
    """
    Start the process's worker.

    Returns:
        The worker, already running.
    """
    global _worker
    if _worker is None:
        _worker = ComposeGateWorker()
    _worker.start()
    return _worker


def stop_worker() -> None:
    """Stop the process's worker, if it was started."""
    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None
