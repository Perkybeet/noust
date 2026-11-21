# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a stack that already runs has of the server, read from its containers and said in the guard's words.

The guard refuses in a new stack what gives the server to a container, warns about it in a stack that
already had it, and refuses what an update adds. "Already had" cannot be read from the compose file as it
is written: a mount made of a variable (``${DATA_DIR}:/data``), a service an ``include`` brings in or a
definition an ``extends`` takes from another file shows nothing until Compose resolves the file, and
resolving it with the ``.env`` of today would let anyone who can change a variable say that the stack always
had ``/``. A container is the one record that neither the repository nor the ``.env`` can write: it was made
with what it has, and it is not changed without a deployment, which goes through the guard.

Each container is written as the compose service it would have been made from, as far as what it was given
of the server goes, and judged by :func:`~noust.deployers.helpers.compose_guard.inspect_compose`, the same
function that judges the file: what comes out has the keys of the findings of a file, so a finding of the
file that **is** one of a container (same service, same kind, same thing: the real path of a mount, the
device, the normalised capability, the namespace) is a finding the stack already had. There is no second
description of "what gives the machine" to keep in step with the first.

What is read from a container is only ever a reason to warn instead of refuse for what the guard added in
4.3 (:data:`INHERITED_FROM_CONTAINERS`). ``privileged``, the sockets of container runtimes, Noust's own
state and another application's files are what 4.2 already refused with no exception: a stack that ran with
one of them was only let through when the file that served said so in plain words, and a container does not
change that, so a stack that has one without the file saying it asks for the recorded exception like a new
stack. A path that reaches a place through a link of the application's tree is inherited only when git
says the link was not changed by the commits since (:mod:`noust.deployers.helpers.compose_verdict`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from noust.core.docker.grants import Grants
from noust.deployers.helpers.compose_guard import (
    ComposeFindings,
    FindingKey,
    GuardContext,
    inspect_compose,
    merge_findings,
)
from noust.deployers.helpers.compose_paths import followed

__all__ = [
    "INHERITED_FROM_CONTAINERS",
    "Running",
    "facts_of",
    "has",
    "read",
    "with_real_paths",
]

#: The kinds of finding (the second element of a finding's key) that a stack may have inherited from its
#: containers: what 4.3's guard looks at and 4.2's did not, a directory of the server outside the
#: application, devices, the capabilities that give the machine, the namespaces of the host, confinement
#: switched off, another cgroup or runtime, and the volumes or namespace of a container that is not the
#: stack's. Everything else is judged against the file that served alone, as it always was.
INHERITED_FROM_CONTAINERS = frozenset(
    {
        "outside",
        "devices",
        "cap_machine",
        "host",
        "joins",
        "borrows",
        "unconfined",
        "cgroup_parent",
        "runtime",
    }
)


@dataclass(frozen=True)
class Running:
    """
    What the containers of a stack have of the server.

    Attributes:
        findings: All of it, in the words and with the keys of the guard: what the file would be
            refused for if it were written so.
        keys: The part a stack may be said to have had already, with the real path of each mount beside
            the path as Docker recorded it.
    """

    findings: ComposeFindings
    keys: frozenset[FindingKey]


def _is_own(reference: str, ids: frozenset[str], names: frozenset[str]) -> bool:
    """
    Args:
        reference: What a container was told to join: a name or an id, whole or its first digits.
        ids: The ids of the stack's containers.
        names: Their names.

    Returns:
        Whether it is one of the stack's own containers.
    """
    return reference in names or (
        len(reference) >= 12 and any(ident.startswith(reference) for ident in ids)
    )


def _document(grants: Grants, ids: frozenset[str], names: frozenset[str]) -> dict[str, Any]:
    """
    Write one container as the compose service it was made from.

    Args:
        grants: What it was given.
        ids: The ids of every container of the stack.
        names: Their names: a container that joins one of them (``network_mode: service:vpn`` is
            ``container:<id of vpn>``) joins the stack, not the server.

    Returns:
        A compose document with one service, which has the keys the guard reads.
    """

    def joined(value: str) -> str:
        if value.startswith("container:") and _is_own(value.partition(":")[2], ids, names):
            return ""
        return value

    service: dict[str, Any] = {}
    if grants.privileged:
        service["privileged"] = True
    if grants.cap_add:
        service["cap_add"] = list(grants.cap_add)
    if grants.devices:
        service["devices"] = [{"source": path} for path in grants.devices]
    if grants.device_rules:
        service["device_cgroup_rules"] = list(grants.device_rules)
    for key, value in (
        ("pid", grants.pid),
        ("ipc", grants.ipc),
        ("uts", grants.uts),
        ("userns_mode", grants.userns),
        ("cgroup", grants.cgroupns),
        ("network_mode", grants.network),
    ):
        if value:
            service[key] = joined(value)
    # A privileged container has its labelling disabled by Docker itself: it is not a second thing the
    # file asked for.
    options = [o for o in grants.security_opt if not (grants.privileged and o == "label=disable")]
    if options:
        service["security_opt"] = options
    if grants.cgroup_parent:
        service["cgroup_parent"] = grants.cgroup_parent
    if grants.runtime:
        service["runtime"] = grants.runtime
    borrowed = [ref for ref in grants.volumes_from if not _is_own(ref, ids, names)]
    if borrowed:
        service["volumes_from"] = [f"container:{ref}" for ref in borrowed]
    if grants.binds:
        service["volumes"] = [
            {"type": "bind", "source": bind.source, "target": bind.destination}
            for bind in grants.binds
        ]
    return {"services": {grants.service or grants.name: service}}


def facts_of(grants: Sequence[Grants], context: GuardContext) -> ComposeFindings:
    """
    Judge what containers were given as the guard judges a compose file.

    Args:
        grants: What each container of the stack was given.
        context: What the guard knows of the application: where its tree is, so that a mount inside it is
            not a finding.

    Returns:
        The findings, with the keys of a file's: ``service web`` is the service the container belongs to.
    """
    ids = frozenset(g.id for g in grants)
    names = frozenset(g.name for g in grants)
    found = ComposeFindings()
    for one in grants:
        found = merge_findings(
            found, inspect_compose(_document(one, ids, names), context.app_path, context=context)
        )
    return found


def with_real_paths(keys: set[FindingKey]) -> frozenset[FindingKey]:
    """
    Add to the keys of the mounts the real path each is: two spellings of one directory are one fact.

    Args:
        keys: The keys of what the containers have.

    Returns:
        The same, and for each mount the key of the place its path leads to.
    """
    for key in list(keys):
        if key[1] == "outside":
            real = followed(key[2])
            if real is not None and str(real) != key[2]:
                keys.add((key[0], key[1], str(real)))
    return frozenset(keys)


def read(read_grants: Callable[[], Sequence[Grants]], context: GuardContext) -> Running:
    """
    Say what the containers of a stack have of the server.

    Args:
        read_grants: Asks Docker for what each container of the stack was given.
        context: What the guard knows of the application.

    Returns:
        What they have.

    Raises:
        DockerUnavailableError: Docker could not be asked: nothing is guessed.
    """
    findings = facts_of(read_grants(), context)
    keys = {
        key
        for sentence in findings.refused
        if (key := findings.keys.get(sentence)) is not None and key[1] in INHERITED_FROM_CONTAINERS
    }
    return Running(findings, with_real_paths(keys))


def has(running: frozenset[FindingKey], key: FindingKey, *, linked: bool) -> bool:
    """
    Tell whether what a finding names is something a container already has.

    Args:
        running: The keys of what the containers have (:attr:`Running.keys`).
        key: The finding's key.
        linked: The path goes through a link of the application's tree, so only the path as Docker
            recorded it counts: where the link leads now is what a link added since would lead to.

    Returns:
        Whether a container of the same service has it.
    """
    if key[1] not in INHERITED_FROM_CONTAINERS:
        return False
    if key in running:
        return True
    if key[1] == "outside" and not linked:
        real = followed(key[2])
        return real is not None and (key[0], key[1], str(real)) in running
    return False
