# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Whether memory ran out in a container, and what limit it had (item 175).

The kernel counts the processes it kills for lack of memory by cgroup, and the diagnosis of an application
that runs as a unit reads that count in the unit's cgroup (:mod:`noust.core.cgroup_events`). An
application that runs in containers (a Compose stack, a container application) has a unit that only
started them and stays: its cgroup holds no process, so what the kernel killed is in the containers' own.
Which containers are the application's, and how each one ended, is what Docker says, and this is the
reading of it that the diagnosis asks of the door (:meth:`noust.core.docker.gate.DockerGate.app_memory`):
``State.OOMKilled`` of its last run and ``HostConfig.Memory`` of its limit, nothing of its environment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from noust.core.docker.inspect import Inspected
    from noust.core.docker.models import Container

__all__ = ["ContainerMemory", "memory_of"]


@dataclass(frozen=True)
class ContainerMemory:
    """
    What one container of an application says about its memory.

    Attributes:
        id: The full id, which says where its cgroup is while it runs.
        name: Its name.
        oom_killed: Memory ran out in its last run (the kernel killed it).
        running: It runs now (restarting and paused count: a process of it is there or will be).
        restarting: Docker is restarting it now.
        limit_bytes: Its memory limit; None for none.
        started_at: When it last started, to the second.
    """

    id: str
    name: str
    oom_killed: bool
    running: bool
    restarting: bool
    limit_bytes: int | None
    started_at: datetime | None

    @property
    def is_down(self) -> bool:
        """Whether it is not serving: it ended, or Docker is only waiting out a restart."""
        return not self.running or self.restarting


def memory_of(container: Container, seen: Inspected) -> ContainerMemory:
    """
    Args:
        container: A container of the listing.
        seen: What ``inspect`` said of it.

    Returns:
        What it says about its memory.
    """
    return ContainerMemory(
        id=container.id,
        name=container.name,
        oom_killed=seen.oom_killed,
        running=seen.running,
        restarting=seen.restarting,
        limit_bytes=seen.memory_limit_bytes,
        started_at=seen.started_at,
    )
