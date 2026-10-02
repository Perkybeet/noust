# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The processes that used the machine most, minute by minute.

A chart says *that* the CPU peaked five hours ago; nothing said *what* did it.
Once a minute the collector keeps the five processes with the most CPU and the
five with the most memory - pid, name, who owns them, account, CPU, memory and
the command line - in the metrics database, for as long as the per-minute
metrics are kept (:data:`~noust.monitor.timeseries.MINUTE`), which is what the
timeline of an interval (:mod:`noust.managers.timeline`) reads back.

**Who owns a process** is read from its control group, the same way the
application charts are measured (:mod:`noust.monitor.plan`): a cgroup inside
one of an application's targets is that application (its unit, or the
container of its Compose stack), a PHP-FPM worker of an application's pool is
that application, and anything else is the systemd unit its cgroup names.

**CPU is measured, not asked for.** psutil's ``cpu_percent`` is "since the last
time anybody in this process asked", which the monitor's own scan also does;
here each process's CPU seconds are kept from one minute to the next and the
difference is divided by the time that passed. The first minute only primes
the counters, so history starts one minute after the collector does; a process
that started and ended between two minutes is never seen.

**A command line is a secret carrier.** ``--password=...``, ``-p secret``,
``DATABASE_URL=postgres://user:pass@...`` and a token pasted as an argument are
all routine, so every argument passes through :func:`redact_command` before it
is stored, and the stored line is still only shown to a caller allowed to read
command lines (:func:`noust.web.auth.sees_command_lines`).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from noust.core.config import REDACTED
from noust.core.secret_detection import classify, name_looks_secret, redact_url_credentials
from noust.monitor.plan import CGROUP_MOUNT, SamplingPlan

log = logging.getLogger(__name__)

#: How many processes each ranking keeps per minute.
TOP_N = 5

#: Seconds between two samples: one per minute, on the minute.
SAMPLE_SECONDS = 60

#: Longest command line stored, in characters.
MAX_COMMAND = 200

#: The rankings a minute is stored under.
RANK_CPU = "cpu"
RANK_MEMORY = "memory"

#: Kinds of owner.
OWNER_UNIT = "unit"
OWNER_CONTAINER = "container"
OWNER_POOL = "pool"

#: Programs whose ``-p<value>`` is a password glued to the flag.
_GLUED_PASSWORD_PROGRAMS = ("mysql", "mariadb", "mysqldump", "mariadb-dump", "mysqladmin")


@dataclass(frozen=True)
class ProcessReading:
    """
    One process as the reader saw it, before it is ranked.

    Attributes:
        pid: Process id.
        name: Its name.
        user: The account it runs as.
        cpu_seconds: User and system CPU time it has used since it started.
        memory_bytes: Resident memory.
        memory_percent: Share of RAM.
        create_time: When it started, epoch seconds; with the pid, what tells a
            process from a later one that reused its pid.
        cmdline: Its argv, unredacted: it never leaves this module that way.
    """

    pid: int
    name: str
    user: str
    cpu_seconds: float
    memory_bytes: int
    memory_percent: float
    create_time: float
    cmdline: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProcessSample:
    """
    One process in one minute's ranking, as it is stored.

    Attributes:
        ts: The minute, epoch seconds (a multiple of 60).
        rank: :data:`RANK_CPU` or :data:`RANK_MEMORY`.
        position: 1 for the biggest.
        pid: Process id.
        name: Its name.
        user: The account it runs as.
        cpu_percent: CPU over the minute; 100 is one core.
        memory_bytes: Resident memory at the sample.
        memory_percent: Share of RAM at the sample.
        app: The Noust application it belongs to, by domain, or None.
        owner_kind: ``unit``, ``container`` or ``pool``; None when it belongs to
            nothing (a login shell, a kernel thread).
        owner: The unit, container or pool name.
        command: The command line, redacted and cut at :data:`MAX_COMMAND`.
    """

    ts: int
    rank: str
    position: int
    pid: int
    name: str
    user: str
    cpu_percent: float
    memory_bytes: int
    memory_percent: float
    app: str | None = None
    owner_kind: str | None = None
    owner: str | None = None
    command: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Render the sample as JSON-serialisable data.

        Returns:
            Every field, by name.
        """
        return asdict(self)


@dataclass(frozen=True)
class Owner:
    """
    Who a process belongs to.

    Attributes:
        app: The Noust application, by domain, or None.
        kind: ``unit``, ``container`` or ``pool``, or None.
        name: The unit, container or pool.
    """

    app: str | None = None
    kind: str | None = None
    name: str | None = None


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def _flag_name(argument: str) -> str | None:
    """
    Read the name of an option.

    Args:
        argument: One argument.

    Returns:
        ``password`` for ``--password`` or ``-password``; None when the argument
        is not an option.
    """
    if not argument.startswith("-") or argument in ("-", "--"):
        return None
    return argument.lstrip("-")


def _redact_value(name: str, value: str) -> str:
    """
    Decide what a named value becomes.

    Args:
        name: The option or variable name.
        value: Its value.

    Returns:
        The redaction marker when the name or the value looks secret, the value
        with URL credentials replaced otherwise.
    """
    if value and name_looks_secret(name.replace("-", "_")):
        return REDACTED
    return _redact_argument(value)


def _redact_argument(argument: str) -> str:
    """
    Decide what an argument on its own becomes.

    Args:
        argument: One argument, or the value of a named one.

    Returns:
        The argument with a URL's password replaced, the redaction marker for
        a value that looks like a token or a key, the argument otherwise.
    """
    scrubbed = redact_url_credentials(argument)
    if scrubbed != argument:
        return scrubbed
    if argument and classify("", argument).secret:
        return REDACTED
    return argument


def redact_command(argv: Sequence[str], limit: int = MAX_COMMAND) -> str | None:
    """
    Turn an argv into a line safe to keep.

    Replaced, each by the redaction marker: the value of an option or a
    ``NAME=value`` whose name looks secret (``--password``, ``--api-key``,
    ``DB_PASS=``), both when glued with ``=`` and when it is the next argument;
    ``-p<password>`` of the MySQL clients; any argument that looks like a token
    or a key on its own (:func:`~noust.core.secret_detection.classify`); and
    the password inside every URL.

    Args:
        argv: The process's arguments.
        limit: Longest result; longer lines are cut with an ellipsis.

    Returns:
        The redacted line, or None for a process with no argv (a kernel thread).
    """
    if not argv:
        return None
    program = Path(argv[0]).name
    out: list[str] = []
    hide_next = False
    for index, argument in enumerate(argv):
        if hide_next and not argument.startswith("-"):
            out.append(REDACTED)
            hide_next = False
            continue
        hide_next = False
        flag = _flag_name(argument) if index else None
        if flag is not None:
            name, glued, value = flag.partition("=")
            if glued:
                out.append(f"{argument.split('=', 1)[0]}={_redact_value(name, value)}")
                continue
            if program.startswith(_GLUED_PASSWORD_PROGRAMS) and argument.startswith("-p"):
                out.append("-p" + (REDACTED if len(argument) > 2 else ""))
                continue
            hide_next = name_looks_secret(name.replace("-", "_"))
            out.append(argument)
            continue
        name, glued, value = argument.partition("=")
        if index and glued and name and name.replace("_", "").replace(".", "").isalnum():
            out.append(f"{name}={_redact_value(name, value)}")
            continue
        out.append(_redact_argument(argument) if index else argument)
    line = " ".join(out)
    if len(line) > limit:
        return line[: limit - 1] + "…"
    return line


# ---------------------------------------------------------------------------
# Owners
# ---------------------------------------------------------------------------


def cgroup_path(text: str) -> str | None:
    """
    Read the cgroup path of a process.

    Args:
        text: The contents of ``/proc/<pid>/cgroup``.

    Returns:
        The path of the unified hierarchy (``0::/system.slice/x.service``), or
        the last one listed on a v1-only machine; None when there is none.
    """
    paths = [line.split(":", 2)[2] for line in text.splitlines() if line.count(":") >= 2]
    unified = [line[3:] for line in text.splitlines() if line.startswith("0::")]
    if unified:
        return unified[0] or None
    return paths[-1] if paths else None


class OwnerIndex:
    """
    Answers "whose is this process" from the applications' sampling plans.

    The plans already say which cgroups each application is measured from, so
    asking them again is the one answer: a process whose cgroup is inside a
    plan's target belongs to that application.
    """

    def __init__(
        self, plans: Mapping[str, SamplingPlan], *, cgroup_mount: Path = CGROUP_MOUNT
    ) -> None:
        """
        Args:
            plans: The applications' sampling plans, by domain.
            cgroup_mount: Where the cgroup hierarchy is mounted.
        """
        self._cgroups: list[tuple[str, Owner]] = []
        self._pools: dict[str, Owner] = {}
        for domain, plan in plans.items():
            for target in plan.targets:
                owner = Owner(app=domain, kind=target.kind, name=target.name)
                for path in _target_paths(target.cgroup, target.control_group, cgroup_mount):
                    self._cgroups.append((path, owner))
            if plan.pool:
                self._pools[plan.pool] = Owner(app=domain, kind=OWNER_POOL, name=plan.pool)
        # The longest path first, so a nested cgroup wins over its parent.
        self._cgroups.sort(key=lambda entry: len(entry[0]), reverse=True)

    def owner_of(self, cgroup_text: str, name: str = "", cmdline: Sequence[str] = ()) -> Owner:
        """
        Name who a process belongs to.

        Args:
            cgroup_text: The contents of its ``/proc/<pid>/cgroup``.
            name: Its name.
            cmdline: Its argv, for a PHP-FPM worker's pool.

        Returns:
            The application and the unit, container or pool; the unit alone when
            no application claims it; an empty owner when it is in no unit.
        """
        path = cgroup_path(cgroup_text)
        if path:
            for prefix, owner in self._cgroups:
                if path == prefix or path.startswith(prefix + "/"):
                    return owner
        pool = _fpm_pool(name, cmdline)
        if pool is not None and pool in self._pools:
            return self._pools[pool]
        # Imported here: the managers package pulls in every manager, which the
        # collector's module import should not.
        from noust.managers.server.processes import unit_of_cgroup

        unit = unit_of_cgroup(cgroup_text)
        if unit is not None:
            return Owner(kind=OWNER_UNIT, name=unit)
        return Owner()


def _target_paths(cgroup: Path | None, control_group: str | None, mount: Path) -> list[str]:
    """
    Say where a plan's target lives, as ``/proc/<pid>/cgroup`` writes it.

    Args:
        cgroup: The target's cgroup directory.
        control_group: systemd's ``ControlGroup``, verbatim.
        mount: Where the hierarchy is mounted.

    Returns:
        The paths, ``/system.slice/x.service`` style.
    """
    paths: list[str] = []
    if control_group:
        paths.append("/" + control_group.strip("/"))
    if cgroup is not None:
        try:
            paths.append("/" + str(cgroup.relative_to(mount)).strip("/"))
        except ValueError:
            pass
    return [path for path in dict.fromkeys(paths) if path != "/"]


def _fpm_pool(name: str, cmdline: Sequence[str]) -> str | None:
    """
    Read the pool of a PHP-FPM worker.

    Args:
        name: The process name.
        cmdline: Its argv; FPM rewrites it to ``php-fpm: pool <name>``.

    Returns:
        The pool, or None for anything else.
    """
    title = " ".join(cmdline) if cmdline else name
    if "php-fpm" not in title or "pool " not in title:
        return None
    words = title.split("pool ", 1)[1].split()
    return words[0] if words else None


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def read_process_table() -> list[ProcessReading]:
    """
    Read every process with psutil.

    Returns:
        One reading per process; those that vanished or are not readable are
        left out. Empty when psutil is not installed.
    """
    try:
        import psutil
    except ImportError:
        return []
    fields = ["pid", "name", "username", "cpu_times", "memory_info", "memory_percent"]
    fields += ["create_time", "cmdline"]
    readings: list[ProcessReading] = []
    for process in psutil.process_iter(fields):
        try:
            info = process.info
            times = info.get("cpu_times")
            memory = info.get("memory_info")
            readings.append(
                ProcessReading(
                    pid=int(info["pid"]),
                    name=str(info.get("name") or ""),
                    user=str(info.get("username") or ""),
                    cpu_seconds=float(times.user + times.system) if times else 0.0,
                    memory_bytes=int(memory.rss) if memory else 0,
                    memory_percent=float(info.get("memory_percent") or 0.0),
                    create_time=float(info.get("create_time") or 0.0),
                    cmdline=tuple(info.get("cmdline") or ()),
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            # A snapshot of a moving target: one that vanished is normal.
            continue
    return readings


def read_cgroup(pid: int, proc_root: Path = Path("/proc")) -> str:
    """
    Read a process's ``cgroup`` file.

    Args:
        pid: The process.
        proc_root: Where ``/proc`` is; replaced in tests.

    Returns:
        Its contents, or an empty string when the process is gone.
    """
    try:
        return (proc_root / str(pid) / "cgroup").read_text(errors="replace")
    except OSError:
        return ""


class ProcessSampler:
    """
    Ranks the processes once a minute.

    The collector calls :meth:`sample` on every minute it sees; this keeps the
    CPU counters from one call to the next.
    """

    def __init__(
        self,
        *,
        reader: Callable[[], Iterable[ProcessReading]] | None = None,
        cgroup_reader: Callable[[int], str] | None = None,
        wall_clock: Callable[[], float] = time.time,
        top: int = TOP_N,
    ) -> None:
        """
        Args:
            reader: Lists the processes; :func:`read_process_table` by default.
            cgroup_reader: Reads a process's cgroup file; :func:`read_cgroup`.
            wall_clock: Epoch seconds, to tell a process that started since the
                last sample from one seen for the first time.
            top: How many processes each ranking keeps.
        """
        self._reader = reader
        self._cgroup_reader = cgroup_reader or read_cgroup
        self._wall = wall_clock
        self.top = top
        self._previous: dict[tuple[int, float], float] | None = None
        self._previous_at: float | None = None
        self._previous_wall: float | None = None

    def sample(
        self, ts: int, now: float, plans: Mapping[str, SamplingPlan] | None = None
    ) -> list[ProcessSample]:
        """
        Rank the processes for one minute.

        Args:
            ts: The minute being recorded, epoch seconds.
            now: The collector's monotonic clock, for the elapsed time.
            plans: The applications' sampling plans, to name owners.

        Returns:
            The top processes by CPU and by memory; empty on the first call,
            which only primes the CPU counters.
        """
        reader = self._reader or read_process_table
        readings = list(reader())
        wall = self._wall()
        counters = {(r.pid, r.create_time): r.cpu_seconds for r in readings}
        previous, previous_at, previous_wall = (
            self._previous,
            self._previous_at,
            self._previous_wall,
        )
        self._previous, self._previous_at, self._previous_wall = counters, now, wall
        if previous is None or previous_at is None or now - previous_at <= 0:
            return []
        elapsed = now - previous_at

        cpu: list[tuple[float, ProcessReading]] = []
        for reading in readings:
            before = previous.get((reading.pid, reading.create_time))
            if before is None:
                # New since the last minute: everything it used, it used in it.
                if previous_wall is None or reading.create_time < previous_wall:
                    continue
                before = 0.0
            used = max(0.0, reading.cpu_seconds - before)
            cpu.append((round(used / elapsed * 100.0, 1), reading))

        by_cpu = sorted(
            (pair for pair in cpu if pair[0] > 0), key=lambda pair: pair[0], reverse=True
        )[: self.top]
        cpu_of = {(r.pid, r.create_time): value for value, r in cpu}
        by_memory = sorted(readings, key=lambda r: r.memory_bytes, reverse=True)[: self.top]

        index = OwnerIndex(plans or {})
        owners: dict[int, Owner] = {}

        def owner(reading: ProcessReading) -> Owner:
            if reading.pid not in owners:
                owners[reading.pid] = index.owner_of(
                    self._cgroup_reader(reading.pid), reading.name, reading.cmdline
                )
            return owners[reading.pid]

        samples = [
            _sample(ts, RANK_CPU, position, reading, value, owner(reading))
            for position, (value, reading) in enumerate(by_cpu, start=1)
        ]
        samples += [
            _sample(
                ts,
                RANK_MEMORY,
                position,
                reading,
                cpu_of.get((reading.pid, reading.create_time), 0.0),
                owner(reading),
            )
            for position, reading in enumerate(by_memory, start=1)
        ]
        return samples


def _sample(
    ts: int, rank: str, position: int, reading: ProcessReading, cpu: float, owner: Owner
) -> ProcessSample:
    """
    Build one stored row.

    Args:
        ts: The minute.
        rank: The ranking.
        position: Its place in it.
        reading: The process.
        cpu: Its CPU over the minute.
        owner: Who it belongs to.

    Returns:
        The row, with the command line redacted.
    """
    return ProcessSample(
        ts=ts,
        rank=rank,
        position=position,
        pid=reading.pid,
        name=reading.name,
        user=reading.user,
        cpu_percent=cpu,
        memory_bytes=reading.memory_bytes,
        memory_percent=round(reading.memory_percent, 2),
        app=owner.app,
        owner_kind=owner.kind,
        owner=owner.name,
        command=redact_command(reading.cmdline),
    )


__all__ = [
    "MAX_COMMAND",
    "RANK_CPU",
    "RANK_MEMORY",
    "SAMPLE_SECONDS",
    "TOP_N",
    "Owner",
    "OwnerIndex",
    "ProcessReading",
    "ProcessSample",
    "ProcessSampler",
    "cgroup_path",
    "read_cgroup",
    "read_process_table",
    "redact_command",
]
