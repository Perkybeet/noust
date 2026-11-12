# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the kernel counted for a cgroup: the processes it killed for running out of memory (item 175).

The kernel's log says that it killed a process for lack of memory, and not reliably of which unit: the
line names a process and a pid, not the cgroup. In cgroup v2 the kernel keeps the count by cgroup:
``memory.events`` carries ``oom_kill``, "the number of processes belonging to this cgroup killed by
any kind of OOM killer", so the diagnosis can say which application it killed something of, and the
monitor can notice the count go up. ``memory.max`` is the limit and ``memory.peak`` (kernel 5.19 and
later) the highest use, which is what says whether the limit was too low.

Each is a small file under ``/sys/fs/cgroup``, read as it is: nothing here runs a process, and a file
that is not there (cgroup v1, no memory controller, an older kernel) is no answer, which is not the
same as the answer zero. The directory of an application's cgroup is the monitor's plan's to say
(:mod:`noust.monitor.plan`), asked of systemd; this module reads what is in it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OomFacts:
    """
    What the kernel counted for one application, or for the cgroups of one.

    Attributes:
        kills: Processes the kernel killed for running out of memory, since the cgroup was made
            (a unit that is restarted gets a new cgroup, and starts counting again).
        limit: ``memory.max`` in bytes; None when nothing limits it but the machine, or the file
            was not readable.
        peak: The highest memory use, in bytes; None when the kernel does not keep it.
        readable: Whether the kill counter could be read at all. False means this machine cannot
            say, which is not the same as nothing having been killed.
    """

    kills: int = 0
    limit: int | None = None
    peak: int | None = None
    readable: bool = False


def _read_text(path: Path) -> str | None:
    """
    Args:
        path: A file of a cgroup.

    Returns:
        Its text, or None when it is not there or cannot be read.
    """
    try:
        return path.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        return None


def _number(text: str | None) -> int | None:
    """
    Args:
        text: What a file holds, a single whole number.

    Returns:
        The number, or None when there is none or it is negative.
    """
    if text is None:
        return None
    try:
        value = int(text.strip())
    except ValueError:
        return None
    return value if value >= 0 else None


def read_oom_kills(cgroup: Path) -> int | None:
    """
    Read how many processes the kernel killed for lack of memory in a cgroup.

    Args:
        cgroup: The cgroup's directory.

    Returns:
        The ``oom_kill`` counter of its ``memory.events``; None when the file is not there or
        carries no such counter (an old kernel counts ``oom`` only), so a machine that cannot say
        is never taken for one that was not killed.
    """
    text = _read_text(cgroup / "memory.events")
    if text is None:
        return None
    for line in text.splitlines():
        name, _, value = line.partition(" ")
        if name == "oom_kill":
            return _number(value)
    return None


def read_memory_limit(cgroup: Path) -> int | None:
    """
    Args:
        cgroup: The cgroup's directory.

    Returns:
        ``memory.max`` in bytes; None when it is ``max`` (nothing limits the cgroup but the
        machine) or cannot be read.
    """
    return _number(_read_text(cgroup / "memory.max"))


def read_memory_peak(cgroup: Path) -> int | None:
    """
    Args:
        cgroup: The cgroup's directory.

    Returns:
        ``memory.peak`` in bytes, the highest use since the cgroup was made; None when the kernel
        does not keep it (before 5.19) or it cannot be read.
    """
    return _number(_read_text(cgroup / "memory.peak"))


def read_facts(cgroup: Path) -> OomFacts:
    """
    Read what the kernel counted for one cgroup.

    Args:
        cgroup: The cgroup's directory.

    Returns:
        The kill counter, the limit and the peak; ``readable`` is False when the counter could
        not be read.
    """
    kills = read_oom_kills(cgroup)
    return OomFacts(
        kills=kills or 0,
        limit=read_memory_limit(cgroup),
        peak=read_memory_peak(cgroup),
        readable=kills is not None,
    )


def combine(parts: Iterable[OomFacts]) -> OomFacts:
    """
    Put together what is known of every cgroup an application runs in.

    A blue/green pair, a monorepo and a Compose stack are several cgroups and one application.

    Args:
        parts: The facts of each.

    Returns:
        The kills summed, the highest peak, the lowest limit that is set (it is the one that
        bites); ``readable`` when any of them could be read.
    """
    kills = 0
    limits: list[int] = []
    peaks: list[int] = []
    readable = False
    for part in parts:
        if not part.readable:
            continue
        readable = True
        kills += part.kills
        if part.limit is not None:
            limits.append(part.limit)
        if part.peak is not None:
            peaks.append(part.peak)
    return OomFacts(
        kills=kills,
        limit=min(limits) if limits else None,
        peak=max(peaks) if peaks else None,
        readable=readable,
    )
