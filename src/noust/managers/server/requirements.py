# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a server needs to run Noust, stated once (checklist N-04).

Memory, processors and free disk used to be a sentence in a document and a feeling in the
installer's author. They are numbers here, and four things read them:

- ``packaging/install.sh`` carries a generated copy between ``BEGIN REQUIREMENTS`` and
  ``END REQUIREMENTS``, because a script that installs Noust cannot import Noust
  (``scripts/sync_install_targets.py`` writes it, and ``tests/test_requirements.py`` fails
  when it is behind);
- ``noust setup doctor`` and ``GET /api/system/doctor`` judge the machine they run on against
  them (:func:`noust.managers.setup_doctor.resources_section`);
- ``docs/requirements.md`` shows the same table to a person, between its own markers;
- the idle measurement the same document publishes is of a machine of the *minimum* size.

Three levels per resource, because "does it fit" has three answers:

- the **floor** is where Noust or its own installation stops working: the installer refuses
  below it unless it is told ``--force``, and the doctor reports a failure;
- the **minimum** is what a small server with a few applications needs: below it the installer
  says so and asks, and the doctor warns;
- the **recommended** size is what the numbers in ``docs/requirements.md`` are comfortable at;
  it is documentation, nothing is judged against it.

Free disk is read where Noust writes (the applications' directory and its own data), not
"the root", so a server whose ``/var`` is a volume of its own is judged by that volume.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

__all__ = [
    "CPU",
    "DISK",
    "MEMORY",
    "REQUIREMENTS",
    "WEB_SERVER_PROCESSES",
    "DiskFree",
    "PortHolder",
    "Requirement",
    "Resources",
    "Verdict",
    "classify_port_holders",
    "human_mb",
    "installer_block",
    "markdown_table",
    "read_resources",
    "replace_document_table",
    "replace_installer_block",
    "size_text",
    "verdict",
]

Verdict = Literal["ok", "below-minimum", "below-floor"]

#: Programs that may hold ports 80 and 443 without being in the way: they are the web server
#: Noust drives (``nginx``) or the other one it supports.
WEB_SERVER_PROCESSES = frozenset({"nginx", "apache2", "httpd"})

#: Programs that publish a container's port: a Compose stack or another platform's proxy is
#: behind them, which is a different answer to "what do I do" than a program on the host.
CONTAINER_PROXIES = frozenset({"docker-proxy", "rootlesskit", "podman", "conmon"})

_MIB = 1024 * 1024


@dataclass(frozen=True)
class Requirement:
    """
    One resource and the three sizes Noust has a position on.

    Attributes:
        key: ``memory``, ``cpu`` or ``disk``.
        label: What to call it in a sentence.
        unit: The unit the three sizes are in (``MB``, ``processors``, ``MB`` of free disk).
        floor: Below this the installer refuses and the doctor fails.
        minimum: Below this the installer asks and the doctor warns.
        recommended: What the published numbers are comfortable at (documentation only).
        why: One sentence on what the number is for, shown in the document.
    """

    key: str
    label: str
    unit: str
    floor: int
    minimum: int
    recommended: int
    why: str


#: Total memory the kernel offers, or the container's limit when that is lower. Noust's two
#: processes idle at about 200 MB (docs/requirements.md); the web server, one application and
#: its build need the rest. A JavaScript framework's build is the heaviest thing a small server
#: does, which is why the minimum is a gigabyte and a swap file is the advice below it.
MEMORY = Requirement(
    key="memory",
    label="Memory",
    unit="MB",
    floor=512,
    minimum=1024,
    recommended=2048,
    why="Noust's own two processes idle at about 200 MB; builds and applications need the rest.",
)

#: Processors the process may run on (the affinity mask, so a container's CPU set counts).
CPU = Requirement(
    key="cpu",
    label="Processors",
    unit="processors",
    floor=1,
    minimum=1,
    recommended=2,
    why="Noust idles under one percent of a core; a build is what uses more than one.",
)

#: Free space on the filesystem that holds the applications and Noust's data. Each deploy is
#: a release on disk, and the system packages the installer adds (the web server, Node.js,
#: certbot) take about a gigabyte before the first application.
DISK = Requirement(
    key="disk",
    label="Free disk",
    unit="MB",
    floor=3 * 1024,
    minimum=10 * 1024,
    recommended=20 * 1024,
    why="The system packages take about a gigabyte; every release keeps its code and dependencies.",
)

REQUIREMENTS: tuple[Requirement, ...] = (MEMORY, CPU, DISK)


def verdict(requirement: Requirement, value: int | None) -> Verdict:
    """
    Judge one measured value.

    Args:
        requirement: The resource.
        value: What the machine has, in the requirement's unit; None when it could not be read.

    Returns:
        ``ok`` when it is at the minimum or above, or when it is unknown (a machine that cannot
        be measured is not refused for it); ``below-minimum`` between the floor and the minimum;
        ``below-floor`` under the floor.
    """
    if value is None:
        return "ok"
    if value < requirement.floor:
        return "below-floor"
    if value < requirement.minimum:
        return "below-minimum"
    return "ok"


@dataclass(frozen=True)
class DiskFree:
    """
    The free space of the filesystem a path is on.

    Attributes:
        path: The directory asked about.
        free_mb: What a process that is not root may still write, in megabytes.
        total_mb: The filesystem's size, in megabytes.
    """

    path: str
    free_mb: int
    total_mb: int


@dataclass(frozen=True)
class Resources:
    """
    What the machine has.

    Attributes:
        memory_mb: Memory in megabytes, the lower of the kernel's total and the container's
            limit; None when neither could be read.
        swap_mb: Swap in megabytes; None when it could not be read.
        cpus: Processors the process may run on; None when unknown.
        disks: One entry per distinct filesystem among the directories asked about.
    """

    memory_mb: int | None
    swap_mb: int | None
    cpus: int | None
    disks: tuple[DiskFree, ...]


@dataclass(frozen=True)
class PortHolder:
    """
    What holds a port the web server wants.

    Attributes:
        port: 80 or 443.
        kind: ``free``, ``web-server``, ``container`` or ``other``.
        processes: The program names that hold it.
    """

    port: int
    kind: Literal["free", "web-server", "container", "other"]
    processes: tuple[str, ...] = ()


def classify_port_holders(
    listeners: Iterable[tuple[int, tuple[str, ...]]], ports: Iterable[int] = (80, 443)
) -> list[PortHolder]:
    """
    Say who holds each of the web ports.

    Args:
        listeners: ``(port, processes)`` of every listening TCP socket.
        ports: The ports to judge.

    Returns:
        One entry per port. A port the web server shares with nothing else is ``web-server``; one a
        container's proxy holds is ``container``; anything else (Caddy, Traefik on the host, a
        development server) is ``other``. A port held by the web server *and* something else is
        ``other``, naming the something.
    """
    held: dict[int, list[str]] = {}
    for port, processes in listeners:
        held.setdefault(port, [])
        for name in processes or ("another process",):
            if name not in held[port]:
                held[port].append(name)
    found: list[PortHolder] = []
    for port in ports:
        names = tuple(held.get(port, ()))
        if not names:
            found.append(PortHolder(port, "free"))
        elif all(name in WEB_SERVER_PROCESSES for name in names):
            found.append(PortHolder(port, "web-server", names))
        elif all(name in CONTAINER_PROXIES for name in names):
            found.append(PortHolder(port, "container", names))
        else:
            others = tuple(name for name in names if name not in WEB_SERVER_PROCESSES)
            found.append(PortHolder(port, "other", others))
    return found


_MEMINFO = re.compile(r"^(MemTotal|SwapTotal):\s+(\d+)\s+kB", re.MULTILINE)


def _meminfo(path: Path) -> tuple[int | None, int | None]:
    """
    Args:
        path: ``/proc/meminfo``.

    Returns:
        Total memory and swap, in megabytes.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None, None
    values = {name: int(amount) // 1024 for name, amount in _MEMINFO.findall(text)}
    return values.get("MemTotal"), values.get("SwapTotal")


def _cgroup_limit_mb(candidates: Iterable[Path]) -> int | None:
    """
    Read the memory limit of the control group this process is in.

    A container or an LXC guest shows the host's memory in ``/proc/meminfo`` and is killed at
    its own limit, which is what the machine "has" for Noust.

    Args:
        candidates: Files to try: cgroup v2's ``memory.max``, then v1's.

    Returns:
        The limit in megabytes, or None when there is none (``max``) or none could be read.
    """
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text.isdigit():
            limit = int(text) // _MIB
            # cgroup v1 spells "no limit" as a number near 2**63.
            return limit if limit < 2**40 else None
    return None


def _processors() -> int | None:
    """
    Returns:
        The processors this process may run on.
    """
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return os.cpu_count()


def _nearest_existing(path: Path) -> Path | None:
    """
    Args:
        path: A directory that may not exist yet (a fresh server has no ``/var/www/apps``).

    Returns:
        The path or its closest ancestor that exists.
    """
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return None


def read_resources(
    directories: Iterable[Path],
    *,
    meminfo: Path = Path("/proc/meminfo"),
    cgroup_limits: Iterable[Path] = (
        Path("/sys/fs/cgroup/memory.max"),
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    ),
) -> Resources:
    """
    Measure the machine Noust runs on.

    Args:
        directories: Where Noust writes (the applications' directory, its data directory). Each
            one is judged by the filesystem it is on, once.
        meminfo: The kernel's memory table.
        cgroup_limits: Where a container's memory limit is.

    Returns:
        What it found; a value that cannot be read is None, never a guess.
    """
    total, swap = _meminfo(meminfo)
    limit = _cgroup_limit_mb(cgroup_limits)
    memory = (
        min(value for value in (total, limit) if value is not None) if (total or limit) else None
    )
    disks: list[DiskFree] = []
    seen: set[int] = set()
    for directory in directories:
        where = _nearest_existing(directory)
        if where is None:
            continue
        try:
            device = where.stat().st_dev
            usage = shutil.disk_usage(where)
        except OSError:
            continue
        if device in seen:
            continue
        seen.add(device)
        disks.append(DiskFree(str(directory), usage.free // _MIB, usage.total // _MIB))
    return Resources(memory, swap, _processors(), tuple(disks))


def human_mb(megabytes: int) -> str:
    """
    Args:
        megabytes: A size in megabytes.

    Returns:
        ``512 MB`` or ``1 GB`` or ``2.5 GB``: the way the documents and the installer spell it.
    """
    if megabytes < 1024:
        return f"{megabytes} MB"
    gigabytes = megabytes / 1024
    return f"{gigabytes:g} GB" if gigabytes == int(gigabytes) else f"{gigabytes:.1f} GB"


def size_text(requirement: Requirement, amount: int) -> str:
    """
    Args:
        requirement: The resource.
        amount: One of its three sizes.

    Returns:
        The size as the table shows it.
    """
    if requirement.key == "cpu":
        return f"{amount} processor" + ("" if amount == 1 else "s")
    return human_mb(amount)


def markdown_table() -> str:
    """
    Render the three sizes as the Markdown table ``docs/requirements.md`` shows.

    Returns:
        One row per resource, with a trailing newline.
    """
    rows = [
        "| | Floor: below it Noust is not installed | Minimum: below it the installer asks "
        "and `noust setup doctor` warns | Recommended |",
        "|---|---|---|---|",
    ]
    for requirement in REQUIREMENTS:
        rows.append(
            f"| **{requirement.label}** | {size_text(requirement, requirement.floor)} | "
            f"{size_text(requirement, requirement.minimum)} | "
            f"{size_text(requirement, requirement.recommended)} |"
        )
    return "\n".join(rows) + "\n"


def installer_block() -> str:
    """
    Render the sizes the installer carries, as POSIX ``sh`` assignments.

    Returns:
        Six lines in megabytes and processors, with a trailing newline.
    """
    lines: list[str] = []
    for requirement in REQUIREMENTS:
        name = {"memory": "MEMORY_MB", "cpu": "CPUS", "disk": "DISK_MB"}[requirement.key]
        lines.append(f"REQ_{name}_FLOOR={requirement.floor}")
        lines.append(f"REQ_{name}_MIN={requirement.minimum}")
    return "\n".join(lines) + "\n"


#: The markers the generated blocks sit between.
BLOCK_BEGIN = (
    "# BEGIN REQUIREMENTS (generated: scripts/sync_install_targets.py; do not edit by hand)"
)
BLOCK_END = "# END REQUIREMENTS"
DOC_BEGIN = "<!-- BEGIN REQUIREMENTS (generated: scripts/sync_install_targets.py) -->"
DOC_END = "<!-- END REQUIREMENTS -->"


def replace_installer_block(script: str) -> str:
    """
    Put the current sizes into an installer's text.

    Args:
        script: The text of ``packaging/install.sh``.

    Returns:
        The same text with the block between the markers regenerated.

    Raises:
        ValueError: When the markers are missing or out of order.
    """
    begin = script.find(BLOCK_BEGIN)
    end = script.find(BLOCK_END)
    if begin < 0 or end < begin:
        raise ValueError("install.sh has no BEGIN REQUIREMENTS / END REQUIREMENTS markers")
    return f"{script[:begin]}{BLOCK_BEGIN}\n{installer_block()}{script[end:]}"


def replace_document_table(text: str) -> str:
    """
    Put the current table into the requirements document.

    Args:
        text: The document.

    Returns:
        The same text with the table between the markers regenerated.

    Raises:
        ValueError: When the markers are missing or out of order.
    """
    begin = text.find(DOC_BEGIN)
    end = text.find(DOC_END)
    if begin < 0 or end < begin:
        raise ValueError(
            "docs/requirements.md has no BEGIN REQUIREMENTS / END REQUIREMENTS markers"
        )
    return f"{text[:begin]}{DOC_BEGIN}\n\n{markdown_table()}\n{text[end:]}"
