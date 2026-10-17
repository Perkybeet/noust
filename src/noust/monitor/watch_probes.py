# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The probes, asked from outside the application (checklist G-17).

Every scan this watch applies the answers that came in since the last one and
asks the probes that are due, on a small pool, so a slow address never holds the
monitor's loop. A probe that goes down (``fail_after`` bad answers in a row) is
told once through the notification channels, and told again when it answers.

When the status page is on (``status_page.enabled``) the watch also rewrites its
files after an answer changed anything, and at least every few minutes so the
"updated" line is true. The raw answers older than a week are purged once a day.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from noust.core.probe_store import ProbeStore
from noust.managers.probes import MAX_WORKERS, ProbeEngine
from noust.managers.status_page import StatusPage
from noust.monitor.watches import Watch, WatchServices

log = logging.getLogger(__name__)

#: The scope of what this watch remembers.
SCOPE = "probes"

#: The page is rewritten at least this often (seconds), changed or not.
PAGE_EVERY_SECONDS = 300

#: Old answers are purged this often (seconds).
PURGE_EVERY_SECONDS = 86_400


class ProbeWatch(Watch):
    """Asks the probes and keeps the status page's files."""

    name = "probes"
    default_interval_s = 15.0

    def __init__(self, store: ProbeStore | None = None) -> None:
        """
        Args:
            store: The probes' tables; the process-wide store's by default.
        """
        self._store = store
        self._engine: ProbeEngine | None = None
        self._executor: ThreadPoolExecutor | None = None

    def _probes(self) -> ProbeStore:
        """Returns: The probes' tables."""
        if self._store is None:
            self._store = ProbeStore()
        return self._store

    def _engine_for(self, services: WatchServices) -> ProbeEngine:
        """
        Args:
            services: What the monitor lends.

        Returns:
            The engine, built the first time: it keeps the asks in flight
            between two scans, so the watch must keep it.
        """
        if self._engine is None:
            self._executor = ThreadPoolExecutor(
                max_workers=MAX_WORKERS, thread_name_prefix="noust-probe"
            )
            self._engine = ProbeEngine(
                self._probes(),
                services.publish,
                clock=services.clock,
                executor=self._executor,
            )
        return self._engine

    def check(self, services: WatchServices) -> None:
        """
        Apply what came in, ask what is due and keep the page.

        Args:
            services: What the monitor lends.
        """
        store = self._probes()
        if not store.probes():
            return
        engine = self._engine_for(services)
        checks = engine.tick()
        now = services.clock()
        state = services.state
        page = StatusPage(store, config=services.config, clock=services.clock)
        if page.enabled:
            written = float(state.get(SCOPE, "page_written") or 0)
            if checks or now - written >= PAGE_EVERY_SECONDS:
                page.write()
                state.put(SCOPE, "page_written", str(now))
        purged = float(state.get(SCOPE, "purged") or 0)
        if now - purged >= PURGE_EVERY_SECONDS:
            state.put(SCOPE, "purged", str(now))
            store.purge()
