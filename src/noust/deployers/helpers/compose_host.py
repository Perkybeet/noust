# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a service of a compose file is given of the machine, apart from paths.

Everything here is root on the host, or near enough that the difference is a matter of effort:
``privileged``, a device, a capability that lets a container reconfigure the kernel, the host's
own namespaces, the confinement switched off, another cgroup or another runtime, the volumes or
the namespace of a container that is not the stack's, and a ``provider`` (a program the machine
runs when the stack starts). They are refused for a new stack unless an operator recorded an
exception, and warned about for one that already had them
(:mod:`noust.deployers.helpers.compose_verdict`). The list is not a list of bad paths or bad
names that something will always be missing from: each entry is a thing a container can be given
that a container with none of them cannot use.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from noust.core.server_text import say

if TYPE_CHECKING:
    from noust.deployers.helpers.compose_guard import ComposeFindings

#: ``security_opt`` values that switch the container's confinement off.
_UNCONFINED = (
    "apparmor:unconfined",
    "apparmor=unconfined",
    "seccomp:unconfined",
    "seccomp=unconfined",
    "label:disable",
    "label=disable",
    "systempaths=unconfined",
)

#: Namespaces a service can share with the host.
_HOST_NAMESPACES = ("pid", "network_mode", "ipc", "userns_mode", "uts", "cgroup")

#: Namespaces a service can join of another container, by ``container:<name>``.
_JOINABLE_NAMESPACES = ("pid", "network_mode", "ipc", "userns_mode")

#: The capabilities that give the machine, each for a reason a container without it cannot match:
#: ALL is every one of them; SYS_ADMIN mounts file systems, enters namespaces and writes the
#: cgroup files that run a program on the host; SYS_PTRACE reads and writes the memory of any
#: process it can see, which is every process of the host with ``pid: host``; SYS_MODULE loads
#: kernel modules; SYS_RAWIO opens raw memory and ports; SYS_BOOT reboots the machine and loads
#: another kernel; DAC_READ_SEARCH reads any file of the host by handle, past every permission;
#: NET_ADMIN rewrites the network the container shares, which with the host's network is the
#: host's firewall and routes; BPF and PERFMON load programs into the kernel and read what the
#: kernel and every process do. The rest (NET_BIND_SERVICE, CHOWN, NET_RAW...) change what a
#: container can do inside itself, and are only said.
DANGEROUS_CAPABILITIES = frozenset(
    {
        "ALL",
        "SYS_ADMIN",
        "SYS_PTRACE",
        "SYS_MODULE",
        "SYS_RAWIO",
        "SYS_BOOT",
        "DAC_READ_SEARCH",
        "NET_ADMIN",
        "BPF",
        "PERFMON",
    }
)

#: The runtimes that are Docker's own: runc and the shim that runs it.
DEFAULT_RUNTIMES = frozenset({"", "runc", "io.containerd.runc.v1", "io.containerd.runc.v2"})


def items(value: Any) -> list[Any]:
    """
    Read a compose list that a hostile file may have written as anything.

    Args:
        value: What the key held: a list, a scalar or nothing.

    Returns:
        Its entries; a scalar is the one entry, and a mapping (whose entries are not the list a
        compose file means) or nothing is none.
    """
    if value is None or isinstance(value, Mapping):
        return []
    if isinstance(value, list | tuple):
        return list(value)
    return [value]


def enabled(value: Any) -> bool:
    """
    Args:
        value: What a boolean key of a compose file holds.

    Returns:
        Whether it is on, as Compose reads it (``true``, ``yes`` and the like).
    """
    return value is True or str(value).strip().lower() in ("true", "yes", "on", "1")


def _capability(value: Any) -> str:
    """
    Args:
        value: A capability as ``cap_add`` spells it.

    Returns:
        Its name in the form the kernel's table uses: upper case, without ``CAP_``.
    """
    return str(value).strip().upper().removeprefix("CAP_")


def judge_settings(findings: ComposeFindings, who: str, service: Mapping[str, Any]) -> None:
    """
    Judge what one service or job is given of the machine.

    Args:
        findings: Where the findings go.
        who: ``service web`` or ``job migrate``.
        service: Its entry.
    """
    if enabled(service.get("privileged")):
        findings.add(
            (who, "privileged", ""),
            f"{who} is privileged: it has every capability and every device of this server",
            refused=True,
        )
    for borrowed in items(service.get("volumes_from")):
        kind, _, rest = str(borrowed).partition(":")
        if kind == "container":
            target = rest.split(":", 1)[0]
            findings.add(
                (who, "borrows", target),
                f"{who} takes the volumes of the container {target}, which is not part of this "
                "stack: whatever that container has mounted is then in this one",
                refused=True,
            )
    for namespace in _JOINABLE_NAMESPACES:
        value = str(service.get(namespace) or "")
        if value.startswith("container:"):
            target = value.partition(":")[2]
            findings.add(
                (who, "joins", f"{namespace}:{target}"),
                f"{who} joins the {namespace} of the container {target}, which is not part of "
                "this stack",
                refused=True,
            )
    for namespace in _HOST_NAMESPACES:
        if str(service.get(namespace) or "").strip().lower() == "host":
            findings.add(
                (who, "host", namespace), f"{who} shares the host's {namespace}", refused=True
            )
    _judge_capabilities(findings, who, service)
    for entry in (*items(service.get("devices")), *items(service.get("device_cgroup_rules"))):
        device = str(
            entry.get("source") or entry.get("path") or entry
            if isinstance(entry, Mapping)
            else entry
        )
        findings.add(
            (who, "devices", device),
            say("compose_guard.devices", who=who, device=device),
            refused=True,
        )
    for option in items(service.get("security_opt")):
        if str(option).replace(" ", "").lower() in _UNCONFINED:
            findings.add(
                (who, "unconfined", str(option)),
                f"{who} turns its confinement off ({option})",
                refused=True,
            )
    parent = str(service.get("cgroup_parent") or "")
    if parent:
        findings.add(
            (who, "cgroup_parent", parent),
            say("compose_guard.cgroup_parent", who=who, parent=parent),
            refused=True,
        )
    runtime = str(service.get("runtime") or "")
    if runtime.strip().lower() not in DEFAULT_RUNTIMES:
        findings.add(
            (who, "runtime", runtime),
            say("compose_guard.runtime", who=who, runtime=runtime),
            refused=True,
        )
    provider = service.get("provider")
    if provider:
        program = (
            str(provider.get("type") or "") if isinstance(provider, Mapping) else str(provider)
        )
        findings.add(
            (who, "provider", program),
            say("compose_guard.provider", who=who, provider=program),
            refused=True,
        )
    build = service.get("build")
    if isinstance(build, Mapping):
        if enabled(build.get("privileged")):
            findings.add(
                (who, "build_privileged", ""),
                say("compose_guard.build_privileged", who=who),
                refused=True,
            )
        if str(build.get("network") or "").strip().lower() == "host":
            findings.add(
                (who, "build_network", ""),
                say("compose_guard.build_host_network", who=who),
                refused=True,
            )


def _judge_capabilities(findings: ComposeFindings, who: str, service: Mapping[str, Any]) -> None:
    """
    Judge ``cap_add``: the capabilities that give the machine are refused, the rest are said.

    Args:
        findings: Where the findings go.
        who: ``service web`` or ``job migrate``.
        service: Its entry.
    """
    added = [str(cap) for cap in items(service.get("cap_add"))]
    for cap in added:
        if _capability(cap) in DANGEROUS_CAPABILITIES:
            findings.add(
                (who, "cap_machine", _capability(cap)),
                say("compose_guard.capabilities_machine", who=who, capability=cap),
                refused=True,
            )
    other = [cap for cap in added if _capability(cap) not in DANGEROUS_CAPABILITIES]
    if other:
        findings.add(
            (who, "cap_add", ""),
            f"{who} adds capabilities: {', '.join(other)}",
            refused=False,
        )
