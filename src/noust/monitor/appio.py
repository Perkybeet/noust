# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Disk and network traffic of an application, from the counters the kernel keeps.

CPU and memory were the whole of an application's chart; what a process reads
from and writes to disk, and what it sends and receives, are the next questions
when a server is slow (checklist G-04, and the most voted request of the
platform Noust is compared with). Three readers, none of which starts a process
but the one that asks systemd:

``io.stat``
    A cgroup v2 file with one line per block device: ``8:0 rbytes=... wbytes=...``.
    Summed over the devices, it is the bytes the application's processes read and
    wrote, cumulative since the cgroup began. It exists while the ``io``
    controller is on for the cgroup, which ``IOAccounting=yes`` in the unit
    (written since 3.4) and Docker's own containers both arrange.

``/proc/<pid>/net/dev``
    The interface counters of a *network namespace*. A container has its own, so
    reading it through one of the container's processes is exactly what the
    container sent and received. A process in the host's namespace (a unit, a
    container with ``network_mode: host``) would read the whole machine, so this
    reader refuses it.

systemd's IP accounting
    A unit is in the host's namespace, but with ``IPAccounting=yes`` systemd counts
    the bytes its sockets move (``IPIngressBytes`` and ``IPEgressBytes``). Asking
    for them is one ``systemctl show`` for every unit at once, at most every
    :data:`UNIT_NETWORK_SECONDS`.

Every counter is cumulative; a rate is the difference of two readings over the
time between them (:mod:`noust.monitor.appsampler` does that), and a counter that
went backwards (the unit restarted) is not a rate.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from noust.core.runner import CommandRunner

#: Seconds between two questions to systemd about the IP counters of the units.
UNIT_NETWORK_SECONDS = 15.0

#: Deadline of that question.
SHOW_TIMEOUT = 15

#: systemd prints this (or "[not set]") for a counter it is not keeping.
_NOT_COUNTED = 2**64 - 1

#: Where a process's network namespace is named, and the host's own.
PROC = Path("/proc")


def read_io_bytes(cgroup: Path) -> tuple[int, int] | None:
    """
    Read what a cgroup's processes have read from and written to block devices.

    Args:
        cgroup: The cgroup directory.

    Returns:
        ``(bytes read, bytes written)`` summed over the devices; None when the
        cgroup has no ``io.stat`` (the controller is off) or it cannot be read.
        A cgroup that has done no I/O yet has an empty file and reads ``(0, 0)``.
    """
    try:
        text = (cgroup / "io.stat").read_text()
    except OSError:
        return None
    read = written = 0
    for line in text.splitlines():
        for field in line.split()[1:]:
            name, _, value = field.partition("=")
            if name not in ("rbytes", "wbytes"):
                continue
            try:
                number = int(value)
            except ValueError:
                continue
            if name == "rbytes":
                read += number
            else:
                written += number
    return read, written


def _first_pid(cgroup: Path) -> int | None:
    """
    Args:
        cgroup: A cgroup directory.

    Returns:
        A process in it, or None when it has none or the list cannot be read.
    """
    try:
        for token in (cgroup / "cgroup.procs").read_text().split():
            return int(token)
    except (OSError, ValueError):
        return None
    return None


def parse_net_dev(text: str) -> tuple[int, int]:
    """
    Sum the counters of a ``/proc/<pid>/net/dev`` table.

    Args:
        text: The file.

    Returns:
        ``(bytes received, bytes sent)`` over every interface but the loopback:
        traffic between a container and itself is not traffic.
    """
    received = sent = 0
    for line in text.splitlines():
        name, separator, rest = line.partition(":")
        if not separator or name.strip() in ("lo", "Inter-|", "face"):
            continue
        columns = rest.split()
        if len(columns) < 9:
            continue
        try:
            received += int(columns[0])
            sent += int(columns[8])
        except ValueError:
            continue
    return received, sent


def read_container_network(cgroup: Path, proc: Path = PROC) -> tuple[int, int] | None:
    """
    Read what a container has received and sent, through one of its processes.

    Args:
        cgroup: The container's cgroup directory.
        proc: Where ``/proc`` is. Injected so a test can use a fake tree.

    Returns:
        ``(bytes received, bytes sent)``, or None when the container has no
        process to ask, the process is in the host's network namespace (it would
        report the whole machine) or its table cannot be read.
    """
    pid = _first_pid(cgroup)
    if pid is None:
        return None
    try:
        mine = os.readlink(proc / "self" / "ns" / "net")
        theirs = os.readlink(proc / str(pid) / "ns" / "net")
        if mine == theirs:
            return None
        return parse_net_dev((proc / str(pid) / "net" / "dev").read_text())
    except OSError:
        return None


def _counter(value: str) -> int | None:
    """
    Args:
        value: What ``systemctl show`` printed for an IP counter.

    Returns:
        The count, or None for ``[not set]`` and the all-ones value systemd uses
        for a counter it is not keeping.
    """
    try:
        number = int(value)
    except ValueError:
        return None
    return None if number == _NOT_COUNTED else number


class UnitNetwork:
    """
    The IP counters of systemd units, asked for in one command and not too often.

    Between two questions the last answer is returned unchanged: the caller turns
    a counter into a rate by the time *it* measured between readings, so a stale
    answer is a rate of zero and not a wrong one. The caller therefore asks
    :meth:`fresh` before using a reading.
    """

    def __init__(
        self,
        runner: Callable[[], CommandRunner],
        *,
        interval_s: float = UNIT_NETWORK_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            runner: Returns the command runner; called when a question is asked,
                so the process-wide one in force then is the one used.
            interval_s: The least seconds between two questions.
            clock: Monotonic seconds. Tests move it.
        """
        self._runner = runner
        self._interval = interval_s
        self._clock = clock
        self._asked_at: float | None = None
        self._counters: dict[str, tuple[int, int]] = {}
        self._fresh = False

    @property
    def fresh(self) -> bool:
        """Whether the last call to :meth:`counters` asked systemd, rather than reused."""
        return self._fresh

    def counters(self, units: Sequence[str]) -> dict[str, tuple[int, int]]:
        """
        Read the IP counters of some units.

        Args:
            units: Unit names, with or without ``.service``.

        Returns:
            Unit to ``(bytes received, bytes sent)``. A unit that is stopped, is
            not counting (it has no ``IPAccounting=yes``) or is unknown is
            absent. The previous answer, when it is too soon to ask again.
        """
        now = self._clock()
        if self._asked_at is not None and now - self._asked_at < self._interval:
            self._fresh = False
            return self._counters
        self._asked_at = now
        self._fresh = True
        names = sorted({unit if unit.endswith(".service") else f"{unit}.service" for unit in units})
        if not names:
            self._counters = {}
            return self._counters
        result = self._runner().run(
            ["systemctl", "show", *names, "--property=Id,IPIngressBytes,IPEgressBytes"],
            timeout=SHOW_TIMEOUT,
        )
        found: dict[str, tuple[int, int]] = {}
        if result.success:
            for block in result.stdout.split("\n\n"):
                properties = dict(
                    line.partition("=")[::2] for line in block.splitlines() if "=" in line
                )
                unit = properties.get("Id", "").removesuffix(".service")
                received = _counter(properties.get("IPIngressBytes", ""))
                sent = _counter(properties.get("IPEgressBytes", ""))
                if unit and received is not None and sent is not None:
                    found[unit] = (received, sent)
        self._counters = found
        return found
