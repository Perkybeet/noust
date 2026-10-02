"""
Process and resource observability for Noust.

**What it measures.** CPU, memory, swap, load average, per-filesystem capacity,
network counters and uptime for the machine; the process table, of which it
writes down only processes over a resource threshold or carrying a known
malware executable name; and the state of every unit Noust manages plus the
units listed in ``monitor.watch_units``, read with one ``systemctl show`` per
scan. A unit that fails or crash-loops is announced as ``unit_failed``; one
stopped on purpose is not. An application whose unit runs but which stops
answering its health check is announced as ``app_unreachable`` (and
``app_recovered`` when it answers again): see :mod:`noust.monitor.reachability`.

**How often.** Once every ``monitor.scan_interval`` seconds, at least
:data:`MIN_SCAN_INTERVAL`, 60 by default, for the scan of processes and units.
Separately, every 5 seconds, the daemon samples the machine and every
application for the console's charts (:mod:`noust.monitor.collector`), and keeps
that history in ``metrics.db``: four tiers, from 5-second samples for two hours to
hourly means and maxima for 400 days. Observations are persisted as before.

**Where it keeps it.** Observations in one SQLite file,
``/var/lib/noust/observations.db`` (``~/.local/share/noust/observations.db`` for
an unprivileged run); the metrics history beside it in ``metrics.db``. The
observations are bounded three ways: repeats inside an hour collapse into one row, rows past
``monitor.retention_days`` are deleted, and the row count is capped at
:data:`DEFAULT_MAX_OBSERVATIONS`.

**What it does not do.** Listed in :data:`MONITOR_SCOPE`, and enforced by tests
that read the package source: no process is signalled or terminated, no file
outside its own systemd unit is written or deleted, nothing about the machine
is sent to a third party, and no check is driven by a process command line.
"""

from noust.monitor.email_notifier import DEFAULT_SMTP_TIMEOUT, EmailNotifier, SMTPConfig
from noust.monitor.metrics import (
    DEFAULT_CPU_SAMPLE_INTERVAL,
    MAX_COMMAND_LENGTH,
    collect_resource_metrics,
    collect_service_health,
    list_processes,
)
from noust.monitor.models import (
    SEVERITY_NOTICE,
    SEVERITY_WARNING,
    SIGNAL_NAME_PATTERN,
    SIGNAL_RESOURCE_USAGE,
    DiskUsage,
    ProcessInfo,
    ProcessObservation,
    ResourceMetrics,
    ServiceHealth,
)
from noust.monitor.observation_store import (
    DEFAULT_DEDUPE_WINDOW_SECONDS,
    DEFAULT_MAX_OBSERVATIONS,
    ObservationStore,
    default_db_path,
)
from noust.monitor.process_monitor import (
    DEFAULT_CPU_THRESHOLD,
    DEFAULT_MEMORY_THRESHOLD,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MONITOR_SCOPE,
    SCAN_INTERVAL_WARNING_SECONDS,
    MonitorConfig,
    ProcessMonitor,
    UnitFailure,
    scan_interval_warning,
    unit_failure,
)
from noust.monitor.signals import is_known_safe, observe_process, observe_processes

__all__ = [
    "DEFAULT_CPU_SAMPLE_INTERVAL",
    "DEFAULT_CPU_THRESHOLD",
    "DEFAULT_DEDUPE_WINDOW_SECONDS",
    "DEFAULT_MAX_OBSERVATIONS",
    "DEFAULT_MEMORY_THRESHOLD",
    "DEFAULT_RETENTION_DAYS",
    "DEFAULT_SCAN_INTERVAL",
    "DEFAULT_SMTP_TIMEOUT",
    "MAX_COMMAND_LENGTH",
    "MIN_SCAN_INTERVAL",
    "MONITOR_SCOPE",
    "SCAN_INTERVAL_WARNING_SECONDS",
    "SEVERITY_NOTICE",
    "SEVERITY_WARNING",
    "SIGNAL_NAME_PATTERN",
    "SIGNAL_RESOURCE_USAGE",
    "DiskUsage",
    "EmailNotifier",
    "MonitorConfig",
    "ObservationStore",
    "ProcessInfo",
    "ProcessMonitor",
    "ProcessObservation",
    "ResourceMetrics",
    "SMTPConfig",
    "ServiceHealth",
    "UnitFailure",
    "collect_resource_metrics",
    "collect_service_health",
    "default_db_path",
    "is_known_safe",
    "list_processes",
    "observe_process",
    "observe_processes",
    "scan_interval_warning",
    "unit_failure",
]
