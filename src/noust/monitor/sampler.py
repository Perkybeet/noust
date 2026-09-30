"""
The one place that reads the machine's own numbers.

Three implementations used to answer "how loaded is this machine": the header
strip (``web/machine.py``), the chart collector (``web/metrics_collector.py``) and
the monitor's resource scan (``monitor/metrics.py``). They read the disk of two
different filesystems (the strip measured where the applications live, the chart
measured ``/``), so the strip and the chart could disagree by tens of percent on
the same screen. Everything that needs CPU, memory, swap, disk, network or load
now reads it here, and the disk is always the filesystem holding the
applications: the space that runs out first.

:class:`MachineSampler` is the stateful half: network counters become rates, and
the constants that never change (total memory, disk size) are written when they
change and on a slow timer instead of every five seconds.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from noust.core.exceptions import NoustError

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is an optional extra
    psutil = None

log = logging.getLogger(__name__)

#: What a psutil reading can raise in practice: its own error family for
#: processes and platforms, and OSError for the /proc and statvfs reads
#: underneath. Named specifically rather than catching Exception, so a bug in
#: this module stays loud instead of becoming a debug line.
SAMPLING_ERRORS: tuple[type[Exception], ...] = (
    (psutil.Error, OSError, ValueError) if psutil is not None else (OSError, ValueError)
)

#: Where the disk meter reports on when nothing more specific is asked for.
DEFAULT_APPS_ROOT = "/var/www/apps"

#: Seconds between rewrites of a value that does not change (total memory,
#: disk size), so each tier keeps a reading to draw a ceiling from.
TOTALS_EVERY_SECONDS = 300.0

#: Seconds the configured ``apps_directory`` is trusted before the
#: configuration is read again.
APPS_ROOT_REFRESH_SECONDS = 60.0


@dataclass(frozen=True)
class Usage:
    """
    A quantity in use out of a total, in bytes.

    Attributes:
        used: Bytes in use.
        total: Bytes available in all.
        percent: ``used`` as a percentage of ``total``.
    """

    used: int
    total: int
    percent: float
    #: Memory that can be given to a program without swapping (memory only).
    available: int = 0


def resolve_apps_root(apps_root: str | None = None) -> str:
    """
    Work out which filesystem the disk meter reports on.

    Args:
        apps_root: An explicit override, or None to read the configured
            ``apps_directory``: the same flat key every deployer reads, so the
            meter reports on the filesystem applications are deployed to.

    Returns:
        A directory path. Falls back to :data:`DEFAULT_APPS_ROOT` when no
        override was given and the configuration cannot be read - the same
        default the applications directory itself has.
    """
    if apps_root is not None:
        return apps_root

    from noust.core.config import Config

    try:
        return str(Config().get("apps_directory", DEFAULT_APPS_ROOT))
    except (NoustError, OSError) as exc:
        log.warning(
            "Could not read apps_directory from the configuration, using the default: %s", exc
        )
        return DEFAULT_APPS_ROOT


def available() -> bool:
    """
    Say whether the machine can be read at all.

    Returns:
        False when psutil is not installed.
    """
    return psutil is not None


def read_cpu_percent(interval: float | None = None) -> float:
    """
    Read CPU utilisation.

    Args:
        interval: Seconds to block for a real sample. None compares with the
            previous call (the collector's own tick is the window), so its
            very first reading is 0.

    Returns:
        Percent of all CPUs, 0-100.
    """
    if psutil is None:
        return 0.0
    return float(psutil.cpu_percent(interval=interval))


def read_memory() -> Usage:
    """
    Read physical memory.

    Returns:
        Bytes used and total, and psutil's percentage.
    """
    if psutil is None:
        return Usage(0, 0, 0.0)
    memory = psutil.virtual_memory()
    percent = getattr(memory, "percent", None)
    if percent is None:
        percent = memory.used / memory.total * 100 if memory.total else 0.0
    return Usage(
        int(memory.used),
        int(memory.total),
        float(percent),
        available=int(getattr(memory, "available", 0) or 0),
    )


def read_swap() -> Usage:
    """
    Read swap.

    Returns:
        Bytes used and total; a machine without swap reads all zero.
    """
    if psutil is None:
        return Usage(0, 0, 0.0)
    swap = psutil.swap_memory()
    used = int(swap.used)
    total = int(getattr(swap, "total", 0) or 0)
    percent = getattr(swap, "percent", None)
    if percent is None:
        percent = used / total * 100 if total else 0.0
    return Usage(used, total, float(percent))


def read_disk(apps_root: str) -> Usage:
    """
    Read the filesystem the applications live on.

    Args:
        apps_root: The applications directory. When it does not exist yet
            (a machine that has deployed nothing) the root filesystem is read.

    Returns:
        Bytes used and total, and the percentage.
    """
    if psutil is None:
        return Usage(0, 0, 0.0)
    target = apps_root if os.path.isdir(apps_root) else "/"
    disk = psutil.disk_usage(target)
    percent = getattr(disk, "percent", None)
    if percent is None:
        percent = disk.used / disk.total * 100 if disk.total else 0.0
    return Usage(int(disk.used), int(disk.total), float(percent))


def read_net_counters() -> tuple[int, int] | None:
    """
    Read the network's running byte totals.

    Returns:
        ``(received, sent)``, or None when the platform reports none.
    """
    if psutil is None:
        return None
    net = psutil.net_io_counters()
    if net is None:
        return None
    return int(net.bytes_recv), int(net.bytes_sent)


def read_load() -> tuple[float, float, float]:
    """
    Read the load average.

    Returns:
        One, five and fifteen minutes; zeros where the platform has none.
    """
    try:
        load = os.getloadavg()
    except OSError:  # pragma: no cover - not available on every platform
        return (0.0, 0.0, 0.0)
    return (float(load[0]), float(load[1]), float(load[2]))


def read_uptime() -> float:
    """
    Read seconds since boot.

    Returns:
        Seconds, or 0 without psutil.
    """
    if psutil is None:
        return 0.0
    return max(0.0, time.time() - float(psutil.boot_time()))


class MachineSampler:
    """
    Turns the machine's readings into ``(metric, value)`` pairs, tick after tick.

    Stateful because a network rate is a delta between two ticks and because a
    total that never changes should not be written every five seconds.
    """

    def __init__(self, apps_root: str | None = None, *, wall_clock=time.monotonic) -> None:
        """
        Args:
            apps_root: Directory whose filesystem the disk series measures;
                the configured ``apps_directory`` when None (re-read once a
                minute, so a changed setting is honoured without a restart).
            wall_clock: Monotonic time source for the slow timers. Injected so
                tests never wait.
        """
        self._apps_root_override = apps_root
        self._apps_root: str | None = apps_root
        self._apps_root_read_at: float | None = None
        self._clock = wall_clock
        self._last_net: tuple[int, int] | None = None
        self._totals: dict[str, int] = {}
        self._totals_written_at: float | None = None

    def _resolved_apps_root(self, now: float) -> str:
        """
        Return the applications directory, re-reading the configuration slowly.

        Args:
            now: The current monotonic time.

        Returns:
            The directory the disk series measures.
        """
        if self._apps_root_override is not None:
            return self._apps_root_override
        if (
            self._apps_root is None
            or self._apps_root_read_at is None
            or now - self._apps_root_read_at >= APPS_ROOT_REFRESH_SECONDS
        ):
            self._apps_root = resolve_apps_root(None)
            self._apps_root_read_at = now
        return self._apps_root

    def sample(self, elapsed: float | None) -> list[tuple[str, float]]:
        """
        Read the machine once.

        Args:
            elapsed: Seconds since the previous tick, or None on the first.

        Returns:
            ``(metric, value)`` pairs. Empty when psutil is not installed.
            Rates are absent on the first tick, and after a counter reset: a
            rate invented from a negative delta would be a spike that never
            happened.
        """
        if psutil is None:
            return []
        now = self._clock()

        pairs: list[tuple[str, float]] = [("cpu.percent", read_cpu_percent())]
        memory = read_memory()
        swap = read_swap()
        disk = read_disk(self._resolved_apps_root(now))
        pairs.append(("mem.used_bytes", float(memory.used)))
        pairs.append(("swap.used_bytes", float(swap.used)))
        pairs.append(("disk.used_bytes", float(disk.used)))

        counters = read_net_counters()
        if counters is not None:
            previous = self._last_net
            self._last_net = counters
            if previous is not None and elapsed is not None and elapsed > 0:
                rx = (counters[0] - previous[0]) / elapsed
                tx = (counters[1] - previous[1]) / elapsed
                if rx >= 0 and tx >= 0:
                    pairs.append(("net.rx_bytes_s", rx))
                    pairs.append(("net.tx_bytes_s", tx))

        pairs.append(("load.1m", read_load()[0]))
        pairs.extend(
            self._totals_due(
                now,
                {
                    "mem.total_bytes": memory.total,
                    "swap.total_bytes": swap.total,
                    "disk.total_bytes": disk.total,
                },
            )
        )
        return pairs

    def _totals_due(self, now: float, totals: dict[str, int]) -> list[tuple[str, float]]:
        """
        Pick the constant readings worth writing this tick.

        Args:
            now: The current monotonic time.
            totals: Metric name to its current value.

        Returns:
            All of them on the first tick, when any changed, or when
            :data:`TOTALS_EVERY_SECONDS` has passed; otherwise none.
        """
        due = (
            self._totals_written_at is None
            or totals != self._totals
            or now - self._totals_written_at >= TOTALS_EVERY_SECONDS
        )
        if not due:
            return []
        self._totals = dict(totals)
        self._totals_written_at = now
        return [(name, float(value)) for name, value in totals.items()]
