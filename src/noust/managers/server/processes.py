# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The processes that use the machine, listed, and grouped by the unit they belong to.

Observation only, and on purpose: there is no way to signal a process from here.
The console once offered a kill button, and a SIGKILL to sshd or to pid 1 took a
server away from the operator who pressed it. What can be done about a process
that misbehaves is done to its unit, through the services it belongs to.

A process belongs to a unit through its control group, which is why grouping
reads ``/proc/<pid>/cgroup`` and not the process name: nginx's twelve workers and
its master are one row, and PHP-FPM's pools are told apart from each other.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from noust.core.exceptions import DependencyError

#: Sort keys the listing accepts.
SORT_KEYS = frozenset({"cpu", "memory", "pid", "name"})

#: ``0::/system.slice/nginx.service`` and the ones with several controllers.
_CGROUP_UNIT = re.compile(r"/([^/]+\.(?:service|scope|socket))(?:/|$)")


@dataclass(frozen=True)
class ProcessRow:
    """
    One process.

    Attributes:
        pid: Process id.
        name: Its name.
        cpu_percent: CPU use since the last sample.
        memory_percent: Share of RAM.
        memory_mb: Resident memory.
        status: Its state.
        user: The account it runs as.
        unit: The unit it belongs to, or None (a login shell, a kernel thread).
        command: Its command line, ``None`` when the caller may not see it.
    """

    pid: int
    name: str
    cpu_percent: float
    memory_percent: float
    memory_mb: float
    status: str
    user: str
    unit: str | None = None
    command: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Render the row as JSON-serialisable data.

        Returns:
            Every field, by name.
        """
        return asdict(self)


@dataclass(frozen=True)
class UnitRow:
    """
    The processes of one unit, added up.

    Attributes:
        unit: The unit.
        processes: How many there are.
        cpu_percent: Their CPU use together.
        memory_mb: Their resident memory together.
        memory_percent: Their share of RAM together.
    """

    unit: str
    processes: int
    cpu_percent: float
    memory_mb: float
    memory_percent: float

    def to_dict(self) -> dict[str, Any]:
        """
        Render the row as JSON-serialisable data.

        Returns:
            Every field, by name.
        """
        return asdict(self)


def unit_of_cgroup(text: str) -> str | None:
    """
    Name the unit a process belongs to.

    Args:
        text: The contents of ``/proc/<pid>/cgroup``.

    Returns:
        The innermost ``.service``, ``.scope`` or ``.socket`` in its cgroup path,
        or None when it is in none (a kernel thread, the init scope).
    """
    for line in text.splitlines():
        path = line.rsplit(":", 1)[-1]
        found = _CGROUP_UNIT.findall(path)
        if found:
            return str(found[-1])
    return None


def _psutil() -> Any:
    """
    Import psutil, turning its absence into an actionable error.

    Returns:
        The module.

    Raises:
        DependencyError: psutil is not installed.
    """
    try:
        import psutil
    except ImportError as exc:
        raise DependencyError(
            "psutil is not installed, so processes cannot be listed",
            details="Install the web extra: pip install 'noust[web]'.",
        ) from exc
    return psutil


def list_processes(
    *,
    sort_by: str = "cpu",
    limit: int = 50,
    show_commands: bool = False,
    proc_root: Path = Path("/proc"),
) -> tuple[list[ProcessRow], int]:
    """
    List the processes, busiest first.

    Args:
        sort_by: One of :data:`SORT_KEYS`; an unknown key sorts by CPU.
        limit: How many rows to return.
        show_commands: Include each command line. Only for a caller allowed to
            see them: argv routinely carries another process's secrets.
        proc_root: Where ``/proc`` is; replaced in tests.

    Returns:
        The rows and how many processes there are in all.
    """
    psutil = _psutil()
    rows: list[ProcessRow] = []
    fields = ["pid", "name", "cpu_percent", "memory_percent", "memory_info", "status", "username"]
    if show_commands:
        fields.append("cmdline")
    for process in psutil.process_iter(fields):
        try:
            info = process.info
            memory = info.get("memory_info")
            cgroup = ""
            try:
                cgroup = (proc_root / str(info["pid"]) / "cgroup").read_text(errors="replace")
            except OSError:
                pass
            rows.append(
                ProcessRow(
                    pid=info["pid"],
                    name=info.get("name") or "",
                    cpu_percent=info.get("cpu_percent") or 0.0,
                    memory_percent=round(info.get("memory_percent") or 0.0, 2),
                    memory_mb=round((memory.rss if memory else 0) / 1024**2, 2),
                    status=info.get("status") or "unknown",
                    user=info.get("username") or "unknown",
                    unit=unit_of_cgroup(cgroup),
                    command=" ".join((info.get("cmdline") or [])[:5]) or None
                    if show_commands
                    else None,
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            # A snapshot of a moving target: one that vanished is normal.
            continue

    key = sort_by if sort_by in SORT_KEYS else "cpu"
    if key == "pid":
        rows.sort(key=lambda row: row.pid)
    elif key == "name":
        rows.sort(key=lambda row: row.name)
    elif key == "memory":
        rows.sort(key=lambda row: row.memory_percent, reverse=True)
    else:
        rows.sort(key=lambda row: row.cpu_percent, reverse=True)
    return rows[:limit], len(rows)


def group_by_unit(rows: list[ProcessRow]) -> list[UnitRow]:
    """
    Add up the processes of each unit.

    Args:
        rows: Processes, as :func:`list_processes` returns them.

    Returns:
        One row per unit, the biggest memory user first. Processes that belong
        to no unit are left out: they have nothing to be stopped through.
    """
    grouped: dict[str, list[ProcessRow]] = defaultdict(list)
    for row in rows:
        if row.unit:
            grouped[row.unit].append(row)
    units = [
        UnitRow(
            unit=name,
            processes=len(members),
            cpu_percent=round(sum(m.cpu_percent for m in members), 1),
            memory_mb=round(sum(m.memory_mb for m in members), 2),
            memory_percent=round(sum(m.memory_percent for m in members), 2),
        )
        for name, members in grouped.items()
    ]
    return sorted(units, key=lambda unit: unit.memory_mb, reverse=True)
