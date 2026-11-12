# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a container uses now: one reading of ``docker stats``, or the reason there is none.

``docker stats`` streams, and a screen that held a stream open for every container would hold as
many processes open as there are rows. One reading (``--no-stream``) is asked, for one container, when
a person looks at it. A reading is only worth showing when it can be stable: a container that is not
running has nothing to read, one that Docker keeps restarting is a new process every few seconds, and
one that started a moment ago has not used anything yet. For those Docker is not asked and the answer
says why, instead of showing zeros that look like a reading.

Docker prints sizes in two systems in one line: ``MemUsage`` in binary (``212MiB / 512MiB``) and the
network and the disk in decimal (``38kB / 91kB``), and when a container has no memory limit it prints
the machine's memory where the limit would be. Both are read as they mean.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from noust.core.runner import CommandRunner

__all__ = ["MIN_UPTIME_SECONDS", "Usage", "parse_size", "read_usage"]

#: A container that started less than this long ago has not used anything worth showing.
MIN_UPTIME_SECONDS = 10

#: Seconds a reading gets: Docker samples twice, a second apart, to know a processor's use.
STATS_TIMEOUT = 30

_SIZE = re.compile(r"\s*([\d.]+)\s*([kKMGTP]?)(i?)B\s*")
_POWERS = {"": 0, "k": 1, "K": 1, "M": 2, "G": 3, "T": 4, "P": 5}


def parse_size(text: str) -> int | None:
    """
    Read a size the way ``docker stats`` prints one.

    Args:
        text: ``212MiB`` (binary), ``38kB`` (decimal), ``0B`` or ``--``.

    Returns:
        The size in bytes, or None when it is not one.
    """
    found = _SIZE.fullmatch(str(text))
    if found is None:
        return None
    number, prefix, binary = found.groups()
    base = 1024 if binary else 1000
    try:
        return int(float(number) * base ** _POWERS[prefix])
    except (ValueError, OverflowError):
        return None


def _percent(text: str) -> float | None:
    """
    Args:
        text: ``14.50%`` or ``--``.

    Returns:
        The number, or None when Docker had none.
    """
    try:
        return float(str(text).strip().removesuffix("%"))
    except ValueError:
        return None


def _pair(text: str) -> tuple[int | None, int | None]:
    """
    Args:
        text: ``212MiB / 512MiB``.

    Returns:
        The two sizes.
    """
    left, _, right = str(text).partition("/")
    return parse_size(left), parse_size(right)


@dataclass(frozen=True)
class Usage:
    """
    What a container uses, or why that is not known.

    Attributes:
        available: There is a reading worth showing.
        reason: Why there is none: ``not_running``, ``restarting``, ``too_young`` or ``no_reading``
            (Docker did not give one); empty when there is.
        cpu_percent: Processor use, as Docker counts it: 100 is one whole core.
        memory_bytes: Memory in use.
        memory_limit_bytes: Its memory limit; None when it has none.
        memory_percent: Use over the limit; None when it has none.
        net_rx_bytes: Received since it started.
        net_tx_bytes: Sent since it started.
        block_read_bytes: Read from disk since it started.
        block_write_bytes: Written to disk since it started.
        pids: Processes in it.
    """

    available: bool
    reason: str = ""
    cpu_percent: float | None = None
    memory_bytes: int | None = None
    memory_limit_bytes: int | None = None
    memory_percent: float | None = None
    net_rx_bytes: int | None = None
    net_tx_bytes: int | None = None
    block_read_bytes: int | None = None
    block_write_bytes: int | None = None
    pids: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field.
        """
        return {
            "available": self.available,
            "reason": self.reason,
            "cpu_percent": self.cpu_percent,
            "memory_bytes": self.memory_bytes,
            "memory_limit_bytes": self.memory_limit_bytes,
            "memory_percent": self.memory_percent,
            "net_rx_bytes": self.net_rx_bytes,
            "net_tx_bytes": self.net_tx_bytes,
            "block_read_bytes": self.block_read_bytes,
            "block_write_bytes": self.block_write_bytes,
            "pids": self.pids,
        }


def read_usage(
    runner: CommandRunner,
    container_id: str,
    *,
    state: str,
    uptime_seconds: float | None,
    memory_limited: bool | None = None,
) -> Usage:
    """
    Read what a container uses, once.

    Args:
        runner: Runs it.
        container_id: The id of a container Docker listed.
        state: Docker's word for its state.
        uptime_seconds: How long it has been up; None when not known.
        memory_limited: Whether the container has a memory limit of its own, when known: Docker
            prints the machine's memory as the limit of one that has none.

    Returns:
        The reading, or the reason there is none. Docker is not asked about a container that is not
        running, that is restarting or that started a moment ago.
    """
    if state == "restarting":
        return Usage(False, "restarting")
    if state != "running":
        return Usage(False, "not_running")
    if uptime_seconds is not None and uptime_seconds < MIN_UPTIME_SECONDS:
        return Usage(False, "too_young")
    result = runner.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", container_id],
        timeout=STATS_TIMEOUT,
    )
    try:
        row = json.loads(result.stdout.strip().splitlines()[0]) if result.success else None
    except (ValueError, IndexError):
        row = None
    if not isinstance(row, dict):
        return Usage(False, "no_reading")
    cpu = _percent(row.get("CPUPerc", ""))
    memory, limit = _pair(row.get("MemUsage", ""))
    if cpu is None or memory is None:
        return Usage(False, "no_reading")
    received, sent = _pair(row.get("NetIO", ""))
    read, written = _pair(row.get("BlockIO", ""))
    pids = str(row.get("PIDs", "")).strip()
    limited = memory_limited is not False
    return Usage(
        available=True,
        cpu_percent=cpu,
        memory_bytes=memory,
        memory_limit_bytes=limit if limited else None,
        memory_percent=_percent(row.get("MemPerc", "")) if limited else None,
        net_rx_bytes=received,
        net_tx_bytes=sent,
        block_read_bytes=read,
        block_write_bytes=written,
        pids=int(pids) if pids.isdigit() else None,
    )
