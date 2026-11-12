# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What asks for the operator on a machine with Docker, in the order of how much it hurts.

The summary page of the Docker zone is not a table of everything: it is the few things worth stopping
for, worst first, and the counters that say how big the machine is. The rules live here, once (rule 3),
so the console, the command line and an alert that wants the same facts say the same thing.

What is worth stopping for, in this order:

1. **A loop**: a container Docker restarted three times or more in the last ten minutes, or one that
   is restarting now. It says how its last run ended, whether memory ran out and how often it fell.
2. **A stop**: a container that is not running and should be (its restart policy says it should, or it
   failed), that nobody of Noust started for itself, and that is not a job that did its work.
3. **The disk**: a fifth or less of the disk Docker lives on is free.

4. **A new version** (``update``) and **a new major version** (``major``) of an image a container
   runs, as the last look at the registries found them (:func:`version_attention`). The summary never
   asks a registry: asking is something the operator does on purpose, and what is said here is what was
   found then, for the images that are still in use. A major version is a decision and not an update, so
   it is a kind of its own.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from noust.core.docker import states
from noust.core.docker.engine import EngineReport
from noust.core.docker.events import LOOP_MINUTES
from noust.core.docker.inspect import Inspected
from noust.core.docker.models import Container, Owner, OwnerKind
from noust.core.docker_image_checks import VersionRecord

__all__ = [
    "LOOP_AT",
    "LOW_DISK_RATIO",
    "ORDER",
    "Attention",
    "DiskInfo",
    "Summary",
    "attention",
    "counts",
    "version_attention",
]

#: Restarts in the window that make a container a loop.
LOOP_AT = 3

#: Free over total at or below which the disk is something to look at.
LOW_DISK_RATIO = 0.2

#: The kinds of attention, worst first. The ones the registries decide (a version that is new, a new
#: major version) go between the stop and the disk.
ORDER = ("loop", "stopped", "update", "major", "disk")


@dataclass(frozen=True)
class Attention:
    """
    One thing that asks for the operator.

    Attributes:
        kind: One of :data:`ORDER`.
        subject: The container it is about; None for a group of things or the disk.
        owner: Whose the container is.
        count: How many things it stands for.
        facts: What the screen says it with, as data (never a sentence): the console turns them
            into words.
    """

    kind: str
    subject: str | None = None
    owner: Owner | None = None
    count: int = 1
    facts: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            ``kind``, ``subject``, ``owner``, ``count`` and ``facts``.
        """
        return {
            "kind": self.kind,
            "subject": self.subject,
            "owner": self.owner.to_dict() if self.owner is not None else None,
            "count": self.count,
            "facts": dict(self.facts),
        }


@dataclass(frozen=True)
class DiskInfo:
    """
    The disk Docker lives on.

    Attributes:
        docker_bytes: What Docker holds (images, containers, volumes and cache).
        reclaimable_bytes: What a cleanup could give back without touching data.
        free_bytes: Free where Docker keeps its data; None when it cannot be measured from here
            (a Noust in a container does not see the host's disk).
        total_bytes: The size of that disk; None likewise.
        path: Where Docker keeps its data.
    """

    docker_bytes: int | None = None
    reclaimable_bytes: int | None = None
    free_bytes: int | None = None
    total_bytes: int | None = None
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field.
        """
        return {
            "docker_bytes": self.docker_bytes,
            "reclaimable_bytes": self.reclaimable_bytes,
            "free_bytes": self.free_bytes,
            "total_bytes": self.total_bytes,
            "path": self.path,
        }


@dataclass(frozen=True)
class Summary:
    """
    The machine, in short.

    Attributes:
        engine: Which Docker there is and what does not fit in it.
        counts: How many of each thing there are, by kind.
        attention: What asks for the operator, worst first.
        disk: The disk Docker lives on.
    """

    engine: EngineReport
    counts: Mapping[str, Mapping[str, int]]
    attention: tuple[Attention, ...]
    disk: DiskInfo | None


def _state_of(container: Container, inspected: Mapping[str, Inspected]) -> states.StateInfo:
    """
    Args:
        container: A listed container.
        inspected: What ``inspect`` said, by id.

    Returns:
        Its state, with how it ended from ``inspect`` when that is there.
    """
    found = inspected.get(container.id)
    if found is None:
        return states.classify_container(container)
    return states.classify_container(container, exit_code=found.exit_code, oom=found.oom_killed)


def counts(
    containers: Sequence[Container],
    inspected: Mapping[str, Inspected],
    *,
    stacks: int,
    unowned_stacks: int,
    images: int,
    volumes: int,
    networks: int,
) -> dict[str, dict[str, int]]:
    """
    Count what the machine has.

    Args:
        containers: The containers the principal sees.
        inspected: What ``inspect`` said of them, by id.
        stacks: How many Compose projects.
        unowned_stacks: How many of them Noust did not make.
        images: How many images.
        volumes: How many volumes.
        networks: How many networks.

    Returns:
        The counters by kind. A container is running, failing (a problem, running or not) or
        stopped, and each is one of them.
    """
    running = failing = stopped = 0
    for container in containers:
        state = _state_of(container, inspected)
        if state.problem:
            failing += 1
        elif state.kind == "running":
            running += 1
        else:
            stopped += 1
    return {
        "containers": {
            "total": len(containers),
            "running": running,
            "failing": failing,
            "stopped": stopped,
        },
        "stacks": {"total": stacks, "unowned": unowned_stacks},
        "images": {"total": images},
        "volumes": {"total": volumes},
        "networks": {"total": networks},
    }


def version_attention(
    records: Iterable[VersionRecord], in_use: Collection[str]
) -> tuple[Attention, ...]:
    """
    Say how many images in use have a new version, and how many a new major one.

    Args:
        records: What the last look at the registries found, for any images.
        in_use: The references containers run now; what is not among them is not counted.

    Returns:
        An ``update`` for the images with a new version that is not a major one and a ``major`` for the
        ones whose new version is (each only when there is one), with the references and when the
        look was as ``facts``.
    """
    newer = [item for item in records if item.state == "newer" and item.reference in in_use]
    found: list[Attention] = []
    for kind, group in (
        ("update", [item for item in newer if item.change != "major"]),
        ("major", [item for item in newer if item.change == "major"]),
    ):
        if group:
            found.append(
                Attention(
                    kind,
                    count=len(group),
                    facts={
                        "images": sorted(item.reference for item in group),
                        "checked_at": max(item.checked_at for item in group),
                    },
                )
            )
    return tuple(found)


def attention(
    containers: Sequence[Container],
    inspected: Mapping[str, Inspected],
    restarts: Mapping[str, int],
    disk: DiskInfo | None,
    versions: Sequence[Attention] = (),
) -> tuple[Attention, ...]:
    """
    Find what asks for the operator.

    Args:
        containers: The containers the principal sees, with their owners.
        inspected: What ``inspect`` said of them, by id.
        restarts: How many times Docker restarted each in the last :data:`~noust.core.docker.events.LOOP_MINUTES`.
        disk: The disk Docker lives on.
        versions: The new versions of images (:func:`version_attention`).

    Returns:
        The items, worst first (:data:`ORDER`); within a kind, the one that hurts most first.
    """
    loops: list[tuple[int, Attention]] = []
    stops: list[tuple[str, Attention]] = []
    for container in containers:
        if container.owner.kind is OwnerKind.NOUST:
            continue
        seen = inspected.get(container.id)
        state = _state_of(container, inspected)
        recent = restarts.get(container.id, 0)
        if state.kind == "loop" or recent >= LOOP_AT:
            loops.append(
                (
                    recent,
                    Attention(
                        "loop",
                        container.name,
                        container.owner,
                        facts={
                            "exit_code": seen.exit_code if seen else state.exit_code,
                            "oom": bool(seen and seen.oom_killed),
                            "memory_limit_bytes": seen.memory_limit_bytes if seen else None,
                            "restarts_recent": recent,
                            "window_minutes": LOOP_MINUTES,
                            "restart_count": seen.restart_count if seen else 0,
                        },
                    ),
                )
            )
            continue
        if container.running or state.kind == "created":
            continue
        policy = seen.restart_policy.name if seen else "no"
        if states.is_job(kind=state.kind, policy=policy):
            continue
        if not (state.problem or policy != "no"):
            continue
        finished = seen.finished_at.isoformat() if seen and seen.finished_at else None
        stops.append(
            (
                finished or "",
                Attention(
                    "stopped",
                    container.name,
                    container.owner,
                    facts={
                        "exit_code": state.exit_code,
                        "kind": state.kind,
                        "policy": policy,
                        "finished_at": finished,
                    },
                ),
            )
        )
    found: list[Attention] = [item for _, item in sorted(loops, key=lambda pair: -pair[0])]
    found += [item for _, item in sorted(stops, key=lambda pair: pair[0], reverse=True)]
    found += versions
    if (
        disk is not None
        and disk.free_bytes is not None
        and disk.total_bytes
        and disk.free_bytes / disk.total_bytes <= LOW_DISK_RATIO
    ):
        found.append(
            Attention(
                "disk",
                facts={
                    "free_bytes": disk.free_bytes,
                    "total_bytes": disk.total_bytes,
                    "reclaimable_bytes": disk.reclaimable_bytes,
                },
            )
        )
    return tuple(found)
