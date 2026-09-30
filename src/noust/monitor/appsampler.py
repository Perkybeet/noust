"""
Reads CPU and memory of every application, one plan at a time.

The plans (:mod:`noust.monitor.plan`) say where each application's numbers
come from; this module reads them, tick after tick:

- **cgroup v2** (units and containers): ``memory.current`` less the reclaimable
  page cache (``inactive_file``, the working set Docker and Kubernetes report:
  ``memory.current`` alone makes an application that reads a lot of files look
  like it leaks), and CPU as the delta of ``cpu.stat``'s ``usage_usec`` over
  the elapsed time. CPU is a percentage of *one* CPU, so a busy multi-threaded
  application can pass 100.
- **PHP-FPM pools**: the worker processes of the application's pool. FPM puts
  every pool's workers in one service cgroup, so the pool is told from the
  title FPM gives each worker (``php-fpm: pool <name>``), which is the only
  process title this package ever reads: it names the pool, and nothing is
  decided from it. Memory is the workers' proportional set size where the
  kernel gives it (shared opcache is counted once, not once per worker).
- **traffic**: requests and 5xx per minute from the access log
  (:mod:`noust.monitor.traffic`).

A target that cannot be read this tick (a unit that stopped, a container that
was replaced) contributes nothing and costs nothing; the next plan refresh
names the reason.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from noust.monitor.plan import SOURCE_FPM, SamplingPlan
from noust.monitor.timeseries import MetricsStore
from noust.monitor.traffic import TrafficSampler

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is an optional extra
    psutil = None

log = logging.getLogger(__name__)

#: cpu.stat counts in microseconds.
USEC_PER_SECOND = 1_000_000

#: What FPM writes in front of the pool name in a worker's process title.
FPM_TITLE_PREFIX = "php-fpm: pool "

#: What reading a process can raise when it exits under our hands.
_PROCESS_ERRORS: tuple[type[Exception], ...] = (
    (psutil.Error, OSError) if psutil is not None else (OSError,)
)


def read_cpu_usec(cpu_stat: Path) -> int | None:
    """
    Read the cumulative CPU time out of a cgroup v2 ``cpu.stat`` file.

    Args:
        cpu_stat: Path to the file.

    Returns:
        The ``usage_usec`` counter, or None when the file is missing or does
        not carry one.
    """
    try:
        text = cpu_stat.read_text()
    except OSError:
        return None
    for line in text.splitlines():
        name, _, value = line.partition(" ")
        if name == "usage_usec":
            try:
                return int(value)
            except ValueError:
                return None
    return None


def read_working_set(cgroup: Path) -> int | None:
    """
    Read a cgroup's memory without its reclaimable page cache.

    Args:
        cgroup: The cgroup directory.

    Returns:
        ``memory.current`` minus ``inactive_file`` (never below zero), or
        ``memory.current`` alone when ``memory.stat`` is not readable; None
        when the memory controller is not counting for this cgroup.
    """
    try:
        current = int((cgroup / "memory.current").read_text())
    except (OSError, ValueError):
        return None
    try:
        for line in (cgroup / "memory.stat").read_text().splitlines():
            name, _, value = line.partition(" ")
            if name == "inactive_file":
                return max(0, current - int(value))
    except (OSError, ValueError):
        pass
    return current


class AppSampler:
    """
    Reads every planned application, keeping the counters a rate needs.
    """

    def __init__(self, store: MetricsStore) -> None:
        """
        Args:
            store: Where the access log cursors are kept.
        """
        self._last_cpu_usec: dict[str, int] = {}
        self._pid_cpu: dict[int, float] = {}
        self._pid_pool: dict[tuple[int, float], str | None] = {}
        self._fpm_primed = False
        self.traffic = TrafficSampler(store)

    def sample(
        self, plans: Mapping[str, SamplingPlan], now: float, elapsed: float | None
    ) -> list[tuple[str, float]]:
        """
        Read every application once.

        Args:
            plans: Domain to its plan.
            now: The current monotonic time.
            elapsed: Seconds since the previous tick, or None on the first.

        Returns:
            ``(metric, value)`` pairs: ``app.<domain>.mem.bytes`` and
            ``app.<domain>.cpu.percent`` for every plan that measures
            resources and whose targets could be read, plus the traffic series.
            A monorepo, a blue/green pair and a Compose stack are the sum of
            their targets.
        """
        pairs: list[tuple[str, float]] = []
        fpm_seen: set[str] = set()
        fpm_usage: dict[str, dict[str, tuple[int, float]]] = {}

        for plan in plans.values():
            if not plan.measures_resources:
                continue
            if plan.source == SOURCE_FPM:
                pairs.extend(self._sample_fpm(plan, elapsed, fpm_usage, fpm_seen))
                continue
            pairs.extend(self._sample_cgroups(plan, elapsed))

        self._forget_stale(plans, fpm_seen)
        self._fpm_primed = self._fpm_primed or bool(fpm_usage)
        pairs.extend(self.traffic.sample(plans, now))
        return pairs

    # ---------------------------------------------------------------- cgroups

    def _sample_cgroups(self, plan: SamplingPlan, elapsed: float | None) -> list[tuple[str, float]]:
        """
        Sum the working set and the CPU rate of a plan's cgroups.

        Args:
            plan: An application whose CPU and memory come from cgroups.
            elapsed: Seconds since the previous tick, or None.

        Returns:
            The application's two series, each present only when at least one
            of its cgroups could be read for it.
        """
        memory_total: int | None = None
        cpu_total: float | None = None
        for target in plan.targets:
            if target.cgroup is None:
                continue
            memory = read_working_set(target.cgroup)
            if memory is not None:
                memory_total = (memory_total or 0) + memory
            cpu = self._cpu_percent(target.cgroup, elapsed)
            if cpu is not None:
                cpu_total = (cpu_total or 0.0) + cpu

        pairs: list[tuple[str, float]] = []
        if memory_total is not None:
            pairs.append((f"app.{plan.domain}.mem.bytes", float(memory_total)))
        if cpu_total is not None:
            pairs.append((f"app.{plan.domain}.cpu.percent", cpu_total))
        return pairs

    def _cpu_percent(self, cgroup: Path, elapsed: float | None) -> float | None:
        """
        Turn a cgroup's cumulative CPU time into a percentage of one CPU.

        Args:
            cgroup: The cgroup directory.
            elapsed: Seconds since the previous tick, or None.

        Returns:
            The rate, or None on the first reading, when the counter went
            backwards (the unit restarted between ticks), or when the file is
            unreadable.
        """
        key = str(cgroup)
        usec = read_cpu_usec(cgroup / "cpu.stat")
        if usec is None:
            # Forget the counter so a unit that comes back does not have its
            # first delta measured against a life it no longer lives.
            self._last_cpu_usec.pop(key, None)
            return None
        previous = self._last_cpu_usec.get(key)
        self._last_cpu_usec[key] = usec
        if previous is None or elapsed is None or elapsed <= 0:
            return None
        delta = usec - previous
        if delta < 0:
            return None
        return delta / (elapsed * USEC_PER_SECOND) * 100

    # ----------------------------------------------------------------- PHP-FPM

    def _sample_fpm(
        self,
        plan: SamplingPlan,
        elapsed: float | None,
        usage: dict[str, dict[str, tuple[int, float]]],
        seen: set[str],
    ) -> list[tuple[str, float]]:
        """
        Sum the workers of an application's pool.

        Args:
            plan: A PHP application's plan.
            elapsed: Seconds since the previous tick, or None.
            usage: Per FPM service cgroup, per pool: memory and the CPU seconds
                its workers used since the last tick. Filled once per cgroup
                and tick, however many applications share it.
            seen: Every pid seen this tick, so the stale ones can be forgotten.

        Returns:
            The application's two series, or nothing when the pool has no
            worker right now.
        """
        if psutil is None or plan.pool is None or not plan.targets:
            return []
        cgroup = plan.targets[0].cgroup
        if cgroup is None:
            return []
        key = str(cgroup)
        if key not in usage:
            usage[key] = self._read_fpm(cgroup, seen)
        found = usage[key].get(plan.pool)
        if found is None:
            return []
        memory, cpu_seconds = found
        pairs = [(f"app.{plan.domain}.mem.bytes", float(memory))]
        if elapsed is not None and elapsed > 0 and self._fpm_primed:
            pairs.append((f"app.{plan.domain}.cpu.percent", cpu_seconds / elapsed * 100))
        return pairs

    def _read_fpm(self, cgroup: Path, seen: set[str]) -> dict[str, tuple[int, float]]:
        """
        Read every worker in an FPM service's cgroup, grouped by pool.

        Args:
            cgroup: The FPM master's cgroup directory.
            seen: Filled with the pids read, as ``str(pid)``.

        Returns:
            Pool name to ``(memory bytes, CPU seconds used since the last
            tick)``. A worker first seen now was born since the last tick, so
            all its CPU time is new; the very first pass only records the
            counters, or every worker already running would be a burst.
        """
        if psutil is None:
            return {}
        try:
            pids = [int(line) for line in (cgroup / "cgroup.procs").read_text().split()]
        except (OSError, ValueError):
            return {}

        pools: dict[str, tuple[int, float]] = {}
        for pid in pids:
            try:
                process = psutil.Process(pid)
                created = process.create_time()
                pool = self._pool_of(process, pid, created)
                seen.add(str(pid))
                if pool is None:
                    continue
                times = process.cpu_times()
                cpu_now = float(times.user + times.system)
                try:
                    memory = int(process.memory_full_info().pss)
                except (AttributeError, NotImplementedError):
                    memory = int(process.memory_info().rss)
            except _PROCESS_ERRORS:
                continue
            previous = self._pid_cpu.get(pid)
            self._pid_cpu[pid] = cpu_now
            if previous is not None:
                used = max(0.0, cpu_now - previous)
            else:
                used = cpu_now if self._fpm_primed else 0.0
            held_memory, held_cpu = pools.get(pool, (0, 0.0))
            pools[pool] = (held_memory + memory, held_cpu + used)
        return pools

    def _pool_of(self, process: Any, pid: int, created: float) -> str | None:
        """
        Name the pool a worker belongs to, remembering the answer.

        Args:
            process: The psutil process.
            pid: Its pid.
            created: Its start time: with the pid it identifies the process,
                so a recycled pid is read again.

        Returns:
            The pool name, or None for the FPM master (whose title names no
            pool) and anything else in the cgroup.
        """
        key = (pid, created)
        if key not in self._pid_pool:
            title = ""
            cmdline = process.cmdline()
            if cmdline:
                title = " ".join(cmdline)
            self._pid_pool[key] = (
                title[len(FPM_TITLE_PREFIX) :].strip()
                if title.startswith(FPM_TITLE_PREFIX)
                else None
            )
        return self._pid_pool[key]

    def _forget_stale(self, plans: Mapping[str, SamplingPlan], fpm_seen: set[str]) -> None:
        """
        Drop the counters of things that no longer exist.

        Args:
            plans: This tick's plans.
            fpm_seen: The FPM worker pids read this tick.
        """
        live = {
            str(target.cgroup)
            for plan in plans.values()
            for target in plan.targets
            if target.cgroup is not None
        }
        for key in set(self._last_cpu_usec) - live:
            del self._last_cpu_usec[key]
        if self._pid_cpu:
            stale = {pid for pid in self._pid_cpu if str(pid) not in fpm_seen}
            for pid in stale:
                del self._pid_cpu[pid]
            self._pid_pool = {
                key: pool for key, pool in self._pid_pool.items() if str(key[0]) in fpm_seen
            }
