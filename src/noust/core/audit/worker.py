# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The console's audit housekeeping thread.

One daemon thread in ``noust-web`` does what must happen whether or not
anyone records anything: closes flood windows, ships to the destinations,
writes ``audit.checkpoint`` (the head a receiver compares its copy with),
watches the log's size, and once every few hours applies retention to the
audit log and to Noust's other records. On a server without the console,
``noust audit ship`` and ``noust audit prune`` do the same from a timer.

Nothing here runs inside a request; a destination that hangs delays the next
round, never an operator.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from typing import Any

from noust import __version__
from noust.core.audit.actor import Actor
from noust.core.audit.log import AuditLog
from noust.core.audit.shipper import Shipper
from noust.core.exceptions import NoustError

logger = logging.getLogger(__name__)

#: Seconds between rounds.
INTERVAL = 5.0

#: Seconds between re-reading the settings.
SETTINGS_EVERY = 60.0

#: Seconds between size checks.
SIZE_EVERY = 300.0

#: Seconds between retention passes, and before the first one.
RETENTION_EVERY = 6 * 3600.0
RETENTION_FIRST = 120.0


class AuditWorker:
    """Housekeeping for one audit log, one :meth:`tick` at a time."""

    def __init__(self, log: AuditLog, *, clock: Any = time.monotonic) -> None:
        """
        Args:
            log: The log to look after.
            clock: Monotonic time source, for tests.
        """
        self.log = log
        self.shipper = Shipper(log)
        self._clock = clock
        started = clock()
        self._settings_at = started
        self._size_at = started - SIZE_EVERY
        self._retention_at = started - RETENTION_EVERY + RETENTION_FIRST
        self._checkpoint_at = started
        self._checkpointed: tuple[int, str] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> None:
        """Run whatever is due."""
        now = self._clock()
        if now - self._settings_at >= SETTINGS_EVERY:
            self._settings_at = now
            self.log.reload_settings()
            self.shipper.close()
            self.shipper = Shipper(self.log)
        self.log.flush_flood()
        try:
            self.shipper.ship_once()
        except OSError as exc:
            logger.warning("Cannot save the audit shipping state: %s", exc)
        if now - self._checkpoint_at >= self.log.settings.checkpoint_minutes * 60:
            self._checkpoint_at = now
            self.checkpoint()
        if now - self._size_at >= SIZE_EVERY:
            self._size_at = now
            self.check_size()
        if now - self._retention_at >= RETENTION_EVERY:
            self._retention_at = now
            self.apply_retention()

    def checkpoint(self) -> None:
        """Record the head of the chain, when anything was written since the last one."""
        head = self.log.head()
        if head is None or head == self._checkpointed:
            return
        self.log.append(
            "audit.checkpoint",
            actor=Actor.system("audit"),
            details={"seq": head[0], "mac": head[1]},
        )
        self._checkpointed = self.log.head()

    def check_size(self) -> None:
        """Compare the log's size with ``audit.max_total_mb`` and say when it crosses it."""
        over = self.log.total_bytes() > self.log.settings.max_total_bytes
        if over and not self.log.over_limit:
            self.log.append(
                "audit.degraded",
                actor=Actor.system("audit"),
                outcome="failure",
                details={
                    "reason": "size",
                    "total_bytes": self.log.total_bytes(),
                    "max_total_mb": self.log.settings.max_total_mb,
                },
            )
        self.log.over_limit = over

    def apply_retention(self) -> None:
        """Purge the audit log and Noust's other records past their periods."""
        from noust.core.audit.retention import prune

        try:
            self.log.purge(
                retention_days=self.log.settings.retention_days,
                shipped_seq=self.shipper.lowest_cursor(),
            )
        except OSError as exc:
            logger.error("Audit retention could not delete old files: %s", exc)
        try:
            prune()
        except (NoustError, OSError) as exc:
            logger.error("Record retention failed: %s", exc)

    def start(self) -> None:
        """Record the console's start and run :meth:`tick` in a daemon thread."""
        self.log.append(
            "system.start", actor=Actor.system("console"), details={"version": __version__}
        )
        self._thread = threading.Thread(target=self._run, name="noust-audit", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(INTERVAL):
            try:
                self.tick()
            # What a round can meet: the store, the disk, a malformed line or
            # setting. Logged with its traceback and retried next round, so a
            # bad round never ends the thread silently.
            except (NoustError, OSError, sqlite3.Error, ValueError, KeyError, TypeError):
                logger.exception("Audit housekeeping failed; retrying in %s s", INTERVAL)

    def stop(self) -> None:
        """Close flood windows, record the stop and ship it before returning."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=INTERVAL * 2)
        self.log.flush_flood(force=True)
        self.log.append("system.stop", actor=Actor.system("console"))
        try:
            self.shipper.ship_once()
        except OSError as exc:
            logger.warning("Cannot save the audit shipping state: %s", exc)
        self.shipper.close()


_worker: AuditWorker | None = None


def start_worker(log: AuditLog | None = None) -> AuditWorker:
    """
    Start the console's audit housekeeping and host action ledger.

    Args:
        log: The log; the process's own by default.

    Returns:
        The running worker.
    """
    global _worker
    from noust.core.audit import get_log
    from noust.core.audit.ledger import install_ledger

    if _worker is None:
        install_ledger()
        _worker = AuditWorker(log or get_log())
        _worker.start()
    return _worker


def stop_worker() -> None:
    """Stop the housekeeping started by :func:`start_worker`."""
    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None
