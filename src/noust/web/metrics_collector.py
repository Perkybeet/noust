# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The web process's handle on the metrics history.

The sampler itself lives in :mod:`noust.monitor.collector` and runs inside the
``noust-monitor`` daemon, so history is recorded whether or not a console is
open. This module is what the web application wires up around it:

- the process-wide :class:`~noust.monitor.timeseries.MetricsStore` the API
  reads;
- a collector of kind ``console`` that samples **only while no daemon does**: it
  takes the store's collector lease when nobody holds it (a container, a server
  that never enabled the monitor), and gives it up the moment the daemon starts.
  It is the same collector class, the same plans and the same code path, only
  started from here; the lease is what keeps it from ever running twice.

When the daemon is the one sampling, :meth:`MetricsCollector.latest` reads the
newest values from the store, so the console's live feed keeps working.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from typing import Any

from noust.monitor.collector import (
    MetricsCollector,
    RecordingStatus,
    monitor_service_probe,
    open_store,
    recording_status,
)
from noust.monitor.timeseries import MetricsStore

log = logging.getLogger(__name__)

#: How long what systemd said about the monitor unit is trusted: asking costs a
#: few processes, and it only matters while nothing is being recorded.
MONITOR_PROBE_TTL_SECONDS = 30.0

_lock = threading.RLock()
_store: MetricsStore | None = None
_collector: MetricsCollector | None = None
_probe: tuple[float, dict[str, Any] | None] | None = None


def get_metrics_store() -> MetricsStore:
    """
    Return the process-wide metrics store, creating it on first use.

    Returns:
        The store, on :func:`~noust.monitor.timeseries.default_metrics_db_path`.
    """
    global _store
    with _lock:
        if _store is None:
            _store = open_store()
        return _store


def get_metrics_collector() -> MetricsCollector | None:
    """
    Return the collector this process started, if it started one.

    Returns:
        The collector, or None outside the web application's lifespan.
    """
    return _collector


def start_metrics_collector() -> MetricsCollector | None:
    """
    Create and start the console's collector.

    Called from the web application's lifespan. A console that cannot write
    its metrics database must still serve, so failure to open the store is
    logged and reported as None rather than raised. The collector samples only
    while it holds the store's lease, which the monitor daemon takes from it.

    Returns:
        The collector, or None when the store could not be opened.
    """
    global _collector
    with _lock:
        if _collector is None:
            try:
                _collector = MetricsCollector(get_metrics_store(), kind="console")
            except (OSError, sqlite3.Error) as exc:
                log.warning("Metrics are disabled: the store could not be opened: %s", exc)
                return None
        _collector.start()
        return _collector


def stop_metrics_collector() -> None:
    """Stop and discard the console's collector, if one is running."""
    global _collector
    with _lock:
        collector = _collector
        _collector = None
    if collector is not None:
        collector.stop()


def cached_monitor_probe() -> dict[str, Any] | None:
    """
    Ask systemd about the monitor unit, at most once every thirty seconds.

    Returns:
        ``installed``, ``enabled`` and ``active``, or None when systemd cannot
        be asked.
    """
    global _probe
    with _lock:
        now = time.monotonic()
        if _probe is None or now - _probe[0] >= MONITOR_PROBE_TTL_SECONDS:
            _probe = (now, monitor_service_probe())
        return _probe[1]


def history_status() -> RecordingStatus:
    """
    Say whether the metrics history is being recorded, and if not, why.

    Returns:
        The status the API hands the console next to every history it serves.
    """
    return recording_status(get_metrics_store(), monitor=cached_monitor_probe)
