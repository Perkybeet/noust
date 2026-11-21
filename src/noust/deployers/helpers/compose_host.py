# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a service of a compose file is given of the machine, apart from paths.

Everything here is root on the host, or near enough that the difference is a matter of effort:
``privileged``, a device, a capability that lets a container reconfigure the kernel, the host's
own namespaces, the confinement switched off, another cgroup or another runtime, the volumes or
the namespace of a container that is not the stack's, and a ``provider`` (a program the machine
runs when the stack starts), and what Compose has added that gives it without saying so
(``use_api_socket``, a hook that runs ``privileged``, a confinement profile of the stack's own).
They are refused for a new stack unless an operator recorded an exception, and warned about for one
that already had them (:mod:`noust.deployers.helpers.compose_verdict`). The list is not a list of
bad paths or bad names that something will always be missing from: each entry is a thing a
container can be given that a container with none of them cannot use, and a key that is not in
the list of keys the guard knows (:mod:`noust.deployers.helpers.compose_keys`) is refused too.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from noust.core.server_text import say
from noust.deployers.helpers import compose_keys

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
#: kernel and every process do; SYS_TIME sets the clock of the machine (a container has no clock of
#: its own), which moves what a certificate, a token or a log says; AUDIT_CONTROL turns off or
#: rewrites the kernel's audit; MAC_ADMIN and MAC_OVERRIDE change or bypass the security module
#: (AppArmor, SELinux) that confines every container. The rest (NET_BIND_SERVICE, CHOWN,
#: NET_RAW...) change what a container can do inside itself, and are only said.
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
        "SYS_TIME",
        "AUDIT_CONTROL",
        "MAC_ADMIN",
        "MAC_OVERRIDE",
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


def device_identity(entry: str) -> str:
    """
    Name the device an entry of ``devices`` or ``device_cgroup_rules`` gives, as the same device
    whichever way it is spelled.

    Args:
        entry: ``/dev/kvm``, ``/dev/kvm:/dev/kvm:rwm`` (the short form, which names the device on the
            host first) or a cgroup rule (``c 13:* rwm``).

    Returns:
        The device's path on the host, or the rule with its spaces collapsed.
    """
    text = entry.strip()
    if text.startswith("/"):
        return text.split(":", 1)[0]
    return " ".join(text.split())


def option_identity(option: Any) -> str:
    """
    Args:
        option: One ``security_opt`` as a compose file spells it (``seccomp:unconfined``) or Docker
            records it (``seccomp=unconfined``).

    Returns:
        It in one spelling, so that the file and the container that was made from it are one fact.
    """
    return str(option).replace(" ", "").lower().replace(":", "=")


def _capability(value: Any) -> str:
    """
    Args:
        value: A capability as ``cap_add`` spells it.

    Returns:
        Its name in the form the kernel's table uses: upper case, without ``CAP_``.
    """
    return str(value).strip().upper().removeprefix("CAP_")


def judge_settings(
    findings: ComposeFindings,
    who: str,
    service: Mapping[str, Any],
    *,
    check_keys: bool = True,
    section: str = "service",
) -> None:
    """
    Judge what one service or job is given of the machine.

    Args:
        findings: Where the findings go.
        who: ``service web`` or ``job migrate``.
        service: Its entry.
        check_keys: Refuse the keys the guard does not know
            (:mod:`~noust.deployers.helpers.compose_keys`). Off for the model Compose prints, which
            has no key that the files it came from do not, and whose own spellings are Compose's.
        section: What the entry is in the table of keys: ``service`` or ``pre_start``.
    """
    if check_keys:
        compose_keys.judge_keys(findings, who, section, service)
    if enabled(service.get("privileged")):
        findings.add(
            (who, "privileged", ""),
            f"{who} is privileged: it has every capability and every device of this server",
            refused=True,
        )
    if enabled(service.get("use_api_socket")):
        findings.add(
            (who, "api_socket", ""), say("compose_guard.api_socket", who=who), refused=True
        )
    _judge_hooks(findings, who, service, check_keys=check_keys)
    _judge_extras(findings, who, service, check_keys=check_keys)
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
            (who, "devices", device_identity(device)),
            say("compose_guard.devices", who=who, device=device),
            refused=True,
        )
    for option in items(service.get("security_opt")):
        if str(option).replace(" ", "").lower() in _UNCONFINED:
            findings.add(
                (who, "unconfined", option_identity(option)),
                f"{who} turns its confinement off ({option})",
                refused=True,
            )
        elif _replaces_confinement(option):
            findings.add(
                (who, "unconfined", option_identity(option)),
                say("compose_guard.confinement_replaced", who=who, option=option),
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
        if check_keys:
            compose_keys.judge_keys(findings, who, "build", build, path="build")
        for entitlement in items(build.get("entitlements")):
            findings.add(
                (who, "build_entitlement", str(entitlement)),
                say("compose_guard.build_entitlement", who=who, entitlement=entitlement),
                refused=True,
            )
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


def _replaces_confinement(option: Any) -> bool:
    """
    Tell whether a ``security_opt`` swaps the container's confinement for another that is not Docker's.

    Switching it off is :data:`_UNCONFINED`. A profile the repository carries (``seccomp=./profile.json``)
    can allow every system call, a name of an AppArmor profile is whatever the machine has under that
    name, an SELinux type or role other than the container's own (``label=type:spc_t``) is the
    unconfined one, and ``writable-cgroups`` opens the cgroup files that run a program on the host:
    each is the confinement off by another name.

    Args:
        option: One ``security_opt`` as a compose file spells it.

    Returns:
        Whether it replaces the default confinement.
    """
    name, _, value = option_identity(option).partition("=")
    if name == "seccomp":
        return value not in ("builtin", "unconfined")
    if name == "apparmor":
        return value not in ("docker-default", "unconfined")
    if name == "label":
        return value.startswith(("type=", "role=", "user="))
    if name == "writable-cgroups":
        return enabled(value)
    return False


def _judge_hooks(
    findings: ComposeFindings, who: str, service: Mapping[str, Any], *, check_keys: bool
) -> None:
    """
    Judge the commands a service runs in its container after it starts and before it stops.

    ``privileged`` on one of them runs it with more than the container has, which the service's own
    ``privileged`` does not say.

    Args:
        findings: Where the findings go.
        who: ``service web`` or ``job migrate``.
        service: Its entry.
        check_keys: Refuse the keys of a hook the guard does not know.
    """
    for name in ("post_start", "pre_stop"):
        for hook in items(service.get(name)):
            if not isinstance(hook, Mapping):
                continue
            if check_keys:
                compose_keys.judge_keys(findings, who, "hook", hook, path=name)
            if enabled(hook.get("privileged")):
                findings.add(
                    (who, "hook_privileged", name),
                    say("compose_guard.hook_privileged", who=who, hook=name),
                    refused=True,
                )


def _reserved_devices(service: Mapping[str, Any]) -> list[Any]:
    """
    Args:
        service: A service or job entry.

    Returns:
        What ``deploy.resources.reservations.devices`` asks the driver for, as the file wrote it.
    """
    node: Any = service
    for key in ("deploy", "resources", "reservations", "devices"):
        node = node.get(key) if isinstance(node, Mapping) else None
    return items(node)


def _judge_extras(
    findings: ComposeFindings, who: str, service: Mapping[str, Any], *, check_keys: bool
) -> None:
    """
    Judge what a service asks of the drivers and runners of this server beyond its own container.

    Graphics cards and the like (``gpus``, and the devices ``deploy`` reserves) and the models the
    model runner serves are said, not refused: they are what the container is given through a
    driver the operator installed, not the machine.

    Args:
        findings: Where the findings go.
        who: ``service web`` or ``job migrate``.
        service: Its entry.
        check_keys: Refuse the keys of ``deploy`` the guard does not know.
    """
    deploy = service.get("deploy")
    if check_keys and isinstance(deploy, Mapping):
        compose_keys.judge_keys(findings, who, "deploy", deploy, path="deploy")
    if service.get("gpus") or _reserved_devices(service):
        findings.add((who, "gpus", ""), say("compose_guard.gpus", who=who), refused=False)
    models = service.get("models")
    if models:
        named = list(models) if isinstance(models, Mapping | list | tuple) else [models]
        findings.add(
            (who, "models", ""),
            say("compose_guard.models", who=who, models=", ".join(str(name) for name in named)),
            refused=False,
        )
