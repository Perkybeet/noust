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

The list of containers draws the consumption of every row, and a process for each row is not a list. The
same ``docker stats`` without a stream prints every container that runs when it is given no id, so
:func:`read_rows` asks once for all of them and :func:`usage_of` gives each container its reading or the
reason it has none. That reading is kept for :data:`KEEP_SECONDS` seconds, behind the request, so an open
page and the people looking at the same server ask Docker once.
"""

from __future__ import annotations

import json
import re
import threading
import weakref
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from noust.core.docker.models import Container
from noust.core.runner import CommandRunner

__all__ = [
    "KEEP_SECONDS",
    "MIN_UPTIME_SECONDS",
    "ContainerUsage",
    "Usage",
    "UsageList",
    "forget_readings",
    "parse_size",
    "read_rows",
    "read_usage",
    "usage_of",
]

#: A container that started less than this long ago has not used anything worth showing.
MIN_UPTIME_SECONDS = 10

#: Seconds a reading gets: Docker samples twice, a second apart, to know a processor's use.
STATS_TIMEOUT = 30

#: Seconds the reading of every container gets: Docker measures them at the same time, and a machine with
#: hundreds of them takes longer than one.
STATS_ALL_TIMEOUT = 60

#: Seconds the reading of every container is kept: what the list of containers draws is a picture of the
#: last few seconds, and asking Docker for each look at it would make the page cost what it shows.
KEEP_SECONDS = 5.0

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


def _unreadable(state: str, uptime_seconds: float | None) -> Usage | None:
    """
    Args:
        state: Docker's word for a container's state.
        uptime_seconds: How long it has been up; None when not known.

    Returns:
        The reason there is nothing to read, as an answer, for a container that is not worth asking about;
        None for one that is.
    """
    if state == "restarting":
        return Usage(False, "restarting")
    if state != "running":
        return Usage(False, "not_running")
    if uptime_seconds is not None and uptime_seconds < MIN_UPTIME_SECONDS:
        return Usage(False, "too_young")
    return None


def _usage_of_row(row: Mapping[str, Any], memory_limited: bool | None) -> Usage:
    """
    Args:
        row: One object of ``docker stats --format '{{json .}}'``.
        memory_limited: Whether the container has a memory limit of its own, when known.

    Returns:
        What it says, or ``no_reading`` when Docker printed something that is not a reading.
    """
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
    unreadable = _unreadable(state, uptime_seconds)
    if unreadable is not None:
        return unreadable
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
    return _usage_of_row(row, memory_limited)


@dataclass(frozen=True)
class ContainerUsage:
    """
    A container and what it uses, or why that is not known.

    Attributes:
        container: The container, with its owner.
        usage: Its reading, or the reason there is none.
    """

    container: Container
    usage: Usage


@dataclass(frozen=True)
class UsageList:
    """
    What every container the principal sees uses.

    Attributes:
        items: One for each container, by name.
        read_at: When Docker was asked, in epoch seconds: a reading kept behind the request is older than
            the request.
    """

    items: tuple[ContainerUsage, ...]
    read_at: float


#: What the last reading of every container said, by runner: the moment and the rows.
_KEPT: weakref.WeakKeyDictionary[CommandRunner, tuple[float, list[Mapping[str, Any]]]] = (
    weakref.WeakKeyDictionary()
)
_LOCK = threading.Lock()


def forget_readings() -> None:
    """Forget what the last reading said, so the next look asks Docker again."""
    with _LOCK:
        _KEPT.clear()


def read_rows(
    runner: CommandRunner, *, clock: Callable[[], float]
) -> tuple[float, list[Mapping[str, Any]]]:
    """
    Read what every container that runs uses, in one process, or what that said a few seconds ago.

    ``docker stats`` without an id prints them all. Docker is asked once at a time, whoever asks: the
    people who look while one reading is being taken are given it when it ends.

    Args:
        runner: Runs it.
        clock: The time in epoch seconds.

    Returns:
        When it was read and what Docker printed, one object for each container it measured. A Docker
        that did not answer is a reading with no rows, kept like any other so that a daemon in trouble is
        not asked for each look.
    """
    with _LOCK:
        kept = _KEPT.get(runner)
        now = clock()
        if kept is not None and 0 <= now - kept[0] < KEEP_SECONDS:
            return kept
        result = runner.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}"], timeout=STATS_ALL_TIMEOUT
        )
        rows: list[Mapping[str, Any]] = []
        for line in result.stdout.splitlines() if result.success else []:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        taken = (now, rows)
        _KEPT[runner] = taken
        return taken


def usage_of(
    container: Container,
    rows: list[Mapping[str, Any]],
    *,
    memory_limited: bool | None,
    uptime_seconds: float | None,
) -> Usage:
    """
    Say what one container uses out of what :func:`read_rows` found.

    Args:
        container: The container.
        rows: What Docker printed for every container.
        memory_limited: Whether the container has a memory limit of its own, when known.
        uptime_seconds: How long it has been up; None when not known.

    Returns:
        Its reading, or the reason there is none: it is not running, it is restarting, it started a
        moment ago, or Docker printed nothing for it.
    """
    unreadable = _unreadable(container.state, uptime_seconds)
    if unreadable is not None:
        return unreadable
    # Docker prints the container's name and its short id, which is the start of the full one.
    row = next(
        (
            found
            for found in rows
            if found.get("Name") == container.name
            or (str(found.get("ID", "")) and container.id.startswith(str(found["ID"])))
        ),
        None,
    )
    if row is None:
        return Usage(False, "no_reading")
    return _usage_of_row(row, memory_limited)
