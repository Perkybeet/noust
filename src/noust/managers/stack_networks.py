# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Docker networks a Compose stack is on, read and never changed.

Compose gives each stack a network of its own and attaches its containers to it, so a stack is by
default isolated from the others by network. That stops being true quietly: a service attached to a
network another stack also uses (an external proxy network, the default bridge, the host's own
stack) can reach everything on it, and nothing on the stack's page said so. This is the page's
answer, from Docker itself.

Read-only on purpose. Managing the host's Docker (creating, scanning and adopting networks) is not
what Noust is for, and a flat network shared by everything is the shape this exists to point out,
not to copy. What a stack's networks are, who else is on them, whether they reach the outside, and
what is worth knowing about each, is a ``docker network inspect`` away and is shown as Docker says it.

Which containers are the stack's is the one answer of
:func:`noust.deployers.compose_reclaim.stack_identity`; the containers are listed by
:func:`noust.deployers.compose_adopt.list_stack_containers`, the one place that asks Docker for them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any

from noust.core.exceptions import DeploymentError
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import is_compose_backed
from noust.deployers.compose_adopt import (
    PROJECT_LABEL,
    StackContainer,
    list_stack_containers,
)
from noust.deployers.compose_reclaim import stack_identity

#: Seconds Docker has to answer.
DOCKER_TIMEOUT = 30

#: The most networks looked at in one call.
MAX_NETWORKS = 300

#: The label Compose puts on a network with its name in the compose file.
NETWORK_LABEL = "com.docker.compose.network"

#: Networks Docker makes on its own, which every container without a choice lands on.
DOCKER_DEFAULTS = frozenset({"bridge", "host", "none"})

#: Read-only probes, declared by their shapes for ``--dry-run`` (see :mod:`noust.core.runner`).
READ_ONLY_PROBES: tuple[tuple[object, ...], ...] = (
    ("docker", "network", "ls", ...),
    ("docker", "network", "inspect", ...),
)


@dataclass(frozen=True)
class Member:
    """
    A container on a network.

    Attributes:
        name: Its name.
        address: Its address on the network, without the prefix length.
        own: It is one of the stack's own containers.
    """

    name: str
    address: str
    own: bool

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {"name": self.name, "address": self.address, "own": self.own}


@dataclass(frozen=True)
class StackNetwork:
    """
    One network a stack is on, or defines.

    Attributes:
        name: Its Docker name.
        id: Its id, shortened.
        logical: Its name in the compose file, when Compose made it.
        driver: ``bridge``, ``overlay``, ``host``, ``macvlan``...
        scope: ``local`` or ``swarm``.
        internal: Containers on it cannot reach the outside.
        ipv6: IPv6 is enabled on it.
        subnets: Its address ranges.
        owner: The Compose project that made it, empty when none did.
        members: The containers on it.
        findings: What is worth knowing about it, one sentence each.
    """

    name: str
    id: str
    logical: str
    driver: str
    scope: str
    internal: bool
    ipv6: bool
    subnets: tuple[str, ...]
    owner: str
    members: tuple[Member, ...]
    findings: tuple[str, ...] = ()

    @property
    def shared(self) -> bool:
        """Whether containers that are not the stack's are on it."""
        return any(not member.own for member in self.members)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "name": self.name,
            "id": self.id,
            "logical": self.logical,
            "driver": self.driver,
            "scope": self.scope,
            "internal": self.internal,
            "ipv6": self.ipv6,
            "subnets": list(self.subnets),
            "owner": self.owner,
            "shared": self.shared,
            "members": [member.to_dict() for member in self.members],
            "findings": list(self.findings),
        }


@dataclass(frozen=True)
class StackNetworks:
    """
    The networks of one application.

    Attributes:
        app: The application's domain.
        applicable: It is a stack Docker runs.
        reason: Why not, when it is not.
        containers: The names of the stack's containers, running or not.
        networks: The networks they are on, and the ones the stack made.
        isolated: Every network is the stack's own and nothing else is on it.
    """

    app: str
    applicable: bool = True
    reason: str = ""
    containers: tuple[str, ...] = ()
    networks: tuple[StackNetwork, ...] = field(default_factory=tuple)

    @property
    def isolated(self) -> bool:
        """Whether the stack shares no network with anything that is not its own."""
        return self.applicable and not any(
            network.shared or network.driver == "host" or network.name == "bridge"
            for network in self.networks
        )

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "app": self.app,
            "applicable": self.applicable,
            "reason": self.reason,
            "containers": list(self.containers),
            "isolated": self.isolated,
            "networks": [network.to_dict() for network in self.networks],
        }


def stack_networks(app: Any, *, runner: CommandRunner | None = None) -> StackNetworks:
    """
    Read the networks an application's stack is on.

    Args:
        app: The application's row.
        runner: What docker is asked through; the process-wide runner by default.

    Returns:
        The networks, with who else is on each. An application that is not a Compose stack
        (or a container) has none to show, and says so.

    Raises:
        DeploymentError: Docker cannot be asked.
    """
    if not is_compose_backed(getattr(app, "app_type", None)):
        return StackNetworks(
            app=app.domain,
            applicable=False,
            reason="This application does not run in Docker, so it has no Docker network.",
        )
    runner = runner if runner is not None else get_runner()
    identity = stack_identity(app)
    own = [c for c in list_stack_containers(runner=runner) if identity.matches(c)]
    projects = {p for p in (identity.pinned, identity.derived) if p} | {
        c.project for c in own if c.project
    }
    inspected = _inspect_all(runner)
    own_ids = {c.id for c in own}
    found = tuple(
        _network(raw, own_ids, projects, own)
        for raw in inspected
        if _is_the_stacks(raw, own_ids, projects)
    )
    return StackNetworks(
        app=app.domain,
        containers=tuple(sorted(c.name for c in own)),
        networks=tuple(sorted(found, key=lambda network: network.name)),
    )


def _inspect_all(runner: CommandRunner) -> list[dict[str, Any]]:
    """
    Args:
        runner: What docker is asked through.

    Returns:
        ``docker network inspect`` of every network on the host.

    Raises:
        DeploymentError: Docker cannot be asked, or answered what is not JSON.
    """
    listed = runner.run(["docker", "network", "ls", "-q", "--no-trunc"], timeout=DOCKER_TIMEOUT)
    if not listed.success:
        raise DeploymentError(
            "Docker could not list its networks",
            details="Is Docker running? Check: systemctl status docker",
            output=(listed.stderr or listed.stdout).strip() or None,
        )
    ids = [line.strip() for line in listed.stdout.splitlines() if line.strip()][:MAX_NETWORKS]
    if not ids:
        return []
    answer = runner.run(["docker", "network", "inspect", *ids], timeout=DOCKER_TIMEOUT)
    try:
        document = json.loads(answer.stdout or "[]")
    except json.JSONDecodeError:
        document = None
    if not answer.success or not isinstance(document, list):
        raise DeploymentError(
            "Docker did not describe its networks",
            output=(answer.stderr or answer.stdout).strip()[:500] or None,
        )
    return [item for item in document if isinstance(item, dict)]


def _mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    """
    Args:
        raw: One network as Docker describes it.
        key: A field that is a mapping when present (Docker writes null for an empty one).

    Returns:
        The field, or an empty mapping.
    """
    found = raw.get(key)
    return found if isinstance(found, dict) else {}


def _is_the_stacks(raw: dict[str, Any], own_ids: set[str], projects: set[str]) -> bool:
    """
    Args:
        raw: One network as Docker describes it.
        own_ids: The ids of the stack's containers.
        projects: The Compose projects that are the stack's.

    Returns:
        Whether a container of the stack is on it, or the stack made it.
    """
    labels = _mapping(raw, "Labels")
    containers = _mapping(raw, "Containers")
    return bool(set(containers) & own_ids) or labels.get(PROJECT_LABEL) in projects


def _network(
    raw: dict[str, Any], own_ids: set[str], projects: set[str], own: list[StackContainer]
) -> StackNetwork:
    """
    Args:
        raw: One network as Docker describes it.
        own_ids: The ids of the stack's containers.
        projects: The Compose projects that are the stack's.
        own: The stack's containers.

    Returns:
        The network, with what is worth knowing about it.
    """
    labels = _mapping(raw, "Labels")
    containers = _mapping(raw, "Containers")
    members = tuple(
        sorted(
            (
                Member(
                    name=str(item.get("Name") or identifier[:12]),
                    address=str(item.get("IPv4Address") or item.get("IPv6Address") or "").split(
                        "/"
                    )[0],
                    own=identifier in own_ids,
                )
                for identifier, item in containers.items()
                if isinstance(item, dict)
            ),
            key=lambda member: member.name,
        )
    )
    config = raw.get("IPAM", {}).get("Config") if isinstance(raw.get("IPAM"), dict) else None
    subnets = tuple(
        str(entry["Subnet"])
        for entry in config or []
        if isinstance(entry, dict) and entry.get("Subnet")
    )
    owner = str(labels.get(PROJECT_LABEL) or "")
    network = StackNetwork(
        name=str(raw.get("Name") or ""),
        id=str(raw.get("Id") or "")[:12],
        logical=str(labels.get(NETWORK_LABEL) or ""),
        driver=str(raw.get("Driver") or ""),
        scope=str(raw.get("Scope") or ""),
        internal=bool(raw.get("Internal")),
        ipv6=bool(raw.get("EnableIPv6")),
        subnets=subnets,
        owner=owner,
        members=members,
    )
    return replace(network, findings=_findings(network, projects))


def _findings(network: StackNetwork, projects: set[str]) -> tuple[str, ...]:
    """
    Args:
        network: A network of the stack.
        projects: The Compose projects that are the stack's.

    Returns:
        What is worth knowing about it, as facts Docker reported.
    """
    notes: list[str] = []
    if network.driver == "host":
        notes.append("It is the host's own network: the containers have no network of their own.")
    elif network.name == "bridge":
        notes.append(
            "It is Docker's default bridge: every container started without a network is on it, "
            "and they reach each other by address."
        )
    others = [member.name for member in network.members if not member.own]
    if others:
        listed = ", ".join(others[:5]) + (f" and {len(others) - 5} more" if len(others) > 5 else "")
        notes.append(f"It is shared: {listed} {'is' if len(others) == 1 else 'are'} on it too.")
    if network.owner and network.owner not in projects:
        notes.append(f"It belongs to the stack {network.owner}, which made it.")
    elif not network.owner and network.name not in DOCKER_DEFAULTS:
        notes.append("Compose did not make it: it was made by hand or by another tool.")
    if network.internal:
        notes.append("It is internal: containers on it cannot reach the outside.")
    return tuple(notes)
