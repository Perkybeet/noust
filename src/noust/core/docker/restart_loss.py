# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What would not come back after a restart of the machine.

A server is restarted for updates, and what was running when it went down is what Docker brings back
by each container's restart policy. A container whose policy does not bring it back stays down until a
person notices, and nobody notices until a customer does. This lists them before the restart, with how
each is fixed.

Two things are lost, and the difference is in the policy and not in what was typed:

- **``policy``**: a container that runs now and whose policy is ``no`` or ``on-failure``. After a
  restart it is not started again.
- **``stopped``**: a container somebody stopped under ``unless-stopped``. That policy is exactly "bring
  it back unless it was stopped on purpose", so a restart does not bring it back, and the person who
  stopped it a month ago has forgotten. (Under ``always`` a stopped container does come back with the
  daemon, so it is not lost.)

Left out: a job that did its work, a container that ended and is not meant to run again, and what
Noust itself starts when it has to.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from noust.core.docker import states
from noust.core.docker.inspect import Inspected
from noust.core.docker.models import Container, Owner, OwnerKind

__all__ = ["Lost", "find_lost"]


@dataclass(frozen=True)
class Lost:
    """
    A container that would not come back.

    Attributes:
        name: Its name.
        owner: Whose it is.
        why: ``policy`` (it runs and its policy does not bring it back) or ``stopped`` (somebody
            stopped it under a policy that would not bring it back).
        policy: Its restart policy.
        fix: ``via_owner`` (an application or an engine owns it: change it there), ``bring_first``
            (it is part of a stack Noust did not make: bring the stack to Noust, then set its
            policy) or ``set_policy`` (a loose container: give it a restart policy).
        project: The Compose project it is part of, if any.
    """

    name: str
    owner: Owner
    why: str
    policy: str
    fix: str
    project: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field.
        """
        return {
            "name": self.name,
            "owner": self.owner.to_dict(),
            "why": self.why,
            "policy": self.policy,
            "fix": self.fix,
            "project": self.project,
        }


def find_lost(
    containers: Sequence[Container], inspected: Mapping[str, Inspected]
) -> tuple[Lost, ...]:
    """
    Find what a restart would leave down.

    Args:
        containers: The containers the principal sees, with their owners.
        inspected: What ``inspect`` said of them, by id; a container it did not say anything of is
            left out, because without its policy nothing can be told.

    Returns:
        The containers that would not come back, by name.
    """
    found = []
    for container in containers:
        seen = inspected.get(container.id)
        if seen is None or container.owner.kind is OwnerKind.NOUST:
            continue
        policy = seen.restart_policy.name
        state = states.classify_container(container, exit_code=seen.exit_code, oom=seen.oom_killed)
        if states.is_job(kind=state.kind, policy=policy):
            continue
        if container.state == "running" and policy in ("no", "on-failure"):
            why = "policy"
        elif container.state == "exited" and policy == "unless-stopped":
            why = "stopped"
        else:
            continue
        if container.owner.kind in (OwnerKind.APP, OwnerKind.DATABASE):
            fix = "via_owner"
        elif container.project:
            fix = "bring_first"
        else:
            fix = "set_policy"
        found.append(Lost(container.name, container.owner, why, policy, fix, container.project))
    return tuple(sorted(found, key=lambda item: item.name))
