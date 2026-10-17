# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application's unit can be tuned with beyond memory, CPU and tasks (checklist B-57, B-60).

``noust app limits`` sets the hard limits. Docker offers more knobs than that
(``--memory-reservation``, ``--memory-swap``, ``--cpu-shares``, ``--cpuset-cpus``,
``--ulimit``, ``--cap-add``, ``--device``) and systemd has a native unit for each, with the
advantage that they act on the application's own cgroup and not on a container around it:

==========================  ===========================================  ====================
option                      unit directive                               Docker's counterpart
==========================  ===========================================  ====================
``memory_high_mb``          ``MemoryHigh=``: throttle and reclaim above  memory-reservation
``memory_swap_max_mb``      ``MemorySwapMax=``: ``0`` forbids swap       memory-swap
``cpu_weight``              ``CPUWeight=``: share when CPUs are busy     cpu-shares
``allowed_cpus``            ``AllowedCPUs=``: pin to these CPUs          cpuset-cpus
``io_weight``               ``IOWeight=``: share of the disk             blkio-weight
``limit_nofile``            ``LimitNOFILE=``                             ulimit nofile
``limit_nproc``             ``LimitNPROC=``                              ulimit nproc
``limit_memlock_mb``        ``LimitMEMLOCK=``                            ulimit memlock
``core_dumps``              ``LimitCORE=``                               ulimit core
``capabilities``            ``CapabilityBoundingSet=``, ``Ambient...``   cap-add
``devices``                 ``DeviceAllow=`` (GPUs included)             device, gpus
==========================  ===========================================  ====================

**One list, in one place.** The directives are built here (:func:`directives`); the unit template
renders them when a unit is created and :func:`with_tuning` writes them into one that exists, so
the two cannot disagree. They come after the sandbox's (:mod:`noust.managers.unit_hardening`) so
that a capability or a device the operator asks for wins over the sandbox's empty set and its
private ``/dev``.

**Capabilities are a short list.** A web application has no business with root-equivalent
capabilities, so only the ones that let a process bind a low port, open a raw socket, lock memory
or raise its scheduling priority are offered. A unit that needs ``SYS_ADMIN`` is not a process
Noust runs as an unprivileged account: that is a custom unit, written by hand.

**What is not here.** ``shm``, DNS and the hostname of Docker's options have no counterpart in a
native process: it shares the host's. A Compose stack or a container application keeps them in
its compose file.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from noust.core.exceptions import ServiceError, ValidationError
from noust.core.store import App
from noust.core.unit_tuning_store import TuningRecord
from noust.managers import unit_hardening

#: Memory below this cannot start a runtime; the same floor as the hard limit.
MIN_MEMORY_MB = 64

#: The widest ``CPUWeight=`` and ``IOWeight=`` systemd takes.
MAX_WEIGHT = 10000

#: How many files, processes and locked megabytes a unit can ask for: the kernel's own ceilings.
MAX_NOFILE = 1_048_576
MIN_NOFILE = 64
MAX_NPROC = 4_194_304
MIN_NPROC = 16
MAX_MEMLOCK_MB = 1_048_576

#: The capabilities a unit may keep, and what each is for.
CAPABILITIES: dict[str, str] = {
    "NET_BIND_SERVICE": "bind a port below 1024 without being root",
    "NET_RAW": "open raw and packet sockets (ping, packet capture)",
    "NET_BROADCAST": "send broadcast and listen to multicast",
    "IPC_LOCK": "lock memory (mlock, huge pages)",
    "SYS_NICE": "raise its scheduling priority and set CPU affinity",
    "SYS_RESOURCE": "raise resource limits past the hard ones, use reserved space",
    "SYS_CHROOT": "use chroot",
}

#: Device groups and nodes a GPU needs, by vendor.
GPU_PRESETS: dict[str, tuple[str, ...]] = {
    "nvidia": ("char-nvidia", "char-nvidia-uvm", "char-nvidia-caps"),
    "dri": ("char-drm",),
}

#: ``/dev/...`` without a way up, or a ``char-``/``block-`` group of ``/proc/devices``.
_DEVICE_NODE = re.compile(r"/dev/[A-Za-z0-9][A-Za-z0-9._/-]{0,127}")
_DEVICE_GROUP = re.compile(r"(?:char|block)-[a-z0-9][a-z0-9_-]{0,63}")

#: ``0-3``, ``0,2`` or ``0-1,4``: the syntax of ``AllowedCPUs=``.
_CPU_SET = re.compile(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*")

#: What a device may be opened for.
DEVICE_MODES = ("r", "w", "rw")

#: The options, in the order their directives are written.
OPTION_NAMES = (
    "memory_high_mb",
    "memory_swap_max_mb",
    "cpu_weight",
    "allowed_cpus",
    "io_weight",
    "limit_nofile",
    "limit_nproc",
    "limit_memlock_mb",
    "core_dumps",
    "capabilities",
    "devices",
)

#: The comment that heads the block, so a unit explains the lines to whoever reads it.
BLOCK_COMMENT = (
    "# Tuning: limits and options beyond memory, CPU and tasks. Change it with:",
    "# noust app tuning",
)


@dataclass(frozen=True)
class Device:
    """
    A device node or a device group the process may open.

    Attributes:
        path: ``/dev/nvidia0``, or a group such as ``char-nvidia``.
        mode: ``r``, ``w`` or ``rw``.
    """

    path: str
    mode: str = "rw"

    def to_dict(self) -> dict[str, str]:
        """
        Returns:
            The device as JSON-ready data.
        """
        return {"path": self.path, "mode": self.mode}


@dataclass(frozen=True)
class Tuning:
    """
    The options an application's unit is tuned with; ``None`` or empty is not set.

    Attributes:
        memory_high_mb: ``MemoryHigh=`` in megabytes.
        memory_swap_max_mb: ``MemorySwapMax=`` in megabytes; 0 forbids swap.
        cpu_weight: ``CPUWeight=``, 1 to 10000 (100 is the default share).
        allowed_cpus: ``AllowedCPUs=``, such as ``0-3``.
        io_weight: ``IOWeight=``, 1 to 10000.
        limit_nofile: ``LimitNOFILE=``.
        limit_nproc: ``LimitNPROC=``.
        limit_memlock_mb: ``LimitMEMLOCK=`` in megabytes, or ``infinity``.
        core_dumps: ``LimitCORE=``: ``infinity`` when True, 0 when False.
        capabilities: Capability names without the ``CAP_`` prefix.
        devices: Device nodes and groups the process may open.
    """

    memory_high_mb: int | None = None
    memory_swap_max_mb: int | None = None
    cpu_weight: int | None = None
    allowed_cpus: str | None = None
    io_weight: int | None = None
    limit_nofile: int | None = None
    limit_nproc: int | None = None
    limit_memlock_mb: int | str | None = None
    core_dumps: bool | None = None
    capabilities: tuple[str, ...] = ()
    devices: tuple[Device, ...] = ()

    def is_empty(self) -> bool:
        """
        Returns:
            Whether nothing is set.
        """
        return not self.to_dict()

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Only what is set, as JSON-ready data (what the store keeps).
        """
        out: dict[str, Any] = {}
        for name in OPTION_NAMES:
            value = getattr(self, name)
            if name == "devices":
                if value:
                    out[name] = [device.to_dict() for device in value]
            elif name == "capabilities":
                if value:
                    out[name] = list(value)
            elif value is not None:
                out[name] = value
        return out


@dataclass(frozen=True)
class _Bounds:
    """The numeric range of an option and how to say it."""

    low: int
    high: int
    unit: str = ""


_INTEGERS: dict[str, _Bounds] = {
    "memory_high_mb": _Bounds(MIN_MEMORY_MB, 16_777_216, "MB"),
    "memory_swap_max_mb": _Bounds(0, 16_777_216, "MB"),
    "cpu_weight": _Bounds(1, MAX_WEIGHT),
    "io_weight": _Bounds(1, MAX_WEIGHT),
    "limit_nofile": _Bounds(MIN_NOFILE, MAX_NOFILE),
    "limit_nproc": _Bounds(MIN_NPROC, MAX_NPROC),
}


def _integer(name: str, value: object) -> int:
    """
    Read a whole number inside its range.

    Args:
        name: The option.
        value: What the caller gave.

    Returns:
        The number.

    Raises:
        ValidationError: Not a whole number, or out of range.
    """
    bounds = _INTEGERS.get(name) or _Bounds(0, 1 << 31)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            f"{name} must be a whole number, not {value!r}",
            details=f"Give a number from {bounds.low} to {bounds.high}.",
            field=name,
        )
    if not bounds.low <= value <= bounds.high:
        raise ValidationError(
            f"{name} of {value}{bounds.unit and ' ' + bounds.unit} is not possible",
            details=f"Use {bounds.low} to {bounds.high}{bounds.unit and ' ' + bounds.unit}.",
            field=name,
        )
    return value


def check_cpu_set(value: object, *, cpus: int | None = None) -> str:
    """
    Accept the CPUs a unit is pinned to.

    Args:
        value: Such as ``0-3`` or ``0,2``.
        cpus: CPUs on this machine; read from the machine when None.

    Returns:
        The set without spaces.

    Raises:
        ValidationError: Not the syntax of ``AllowedCPUs=``, a range backwards, or a CPU the
            machine does not have.
    """
    available = cpus if cpus is not None else (os.cpu_count() or 1)
    text = str(value).replace(" ", "") if isinstance(value, str) else ""
    how = f"Name CPUs from 0 to {available - 1}, such as 0-{max(available - 1, 0)} or 0,2."
    if not _CPU_SET.fullmatch(text):
        raise ValidationError(f"{value!r} is not a set of CPUs", details=how, field="allowed_cpus")
    for part in text.split(","):
        low, _, high = part.partition("-")
        first, last = int(low), int(high or low)
        if first > last:
            raise ValidationError(f"{part} runs backwards", details=how, field="allowed_cpus")
        if last >= available:
            raise ValidationError(
                f"CPU {last} does not exist: this machine has {available}",
                details=how,
                field="allowed_cpus",
            )
    return text


def check_capabilities(values: object) -> tuple[str, ...]:
    """
    Accept the capabilities a unit keeps.

    Args:
        values: Names such as ``NET_BIND_SERVICE``, with or without ``CAP_``.

    Returns:
        The names without the prefix, upper case, once each.

    Raises:
        ValidationError: Not a list of names, or a capability that is not offered.
    """
    if not isinstance(values, (list, tuple)):
        raise ValidationError(
            "capabilities must be a list of names",
            details=f"Offered: {', '.join(CAPABILITIES)}.",
            field="capabilities",
        )
    found: list[str] = []
    for raw in values:
        name = str(raw).strip().upper().removeprefix("CAP_")
        if name not in CAPABILITIES:
            raise ValidationError(
                f"{raw!r} is not a capability Noust gives a unit",
                details=f"Offered: {', '.join(CAPABILITIES)}. The others (SYS_ADMIN, "
                "SYS_PTRACE, DAC_OVERRIDE, SETUID...) are root's: an application that needs "
                "one is a custom unit, not a process Noust runs as an unprivileged account.",
                field="capabilities",
            )
        if name not in found:
            found.append(name)
    return tuple(found)


def check_devices(values: object) -> tuple[Device, ...]:
    """
    Accept the devices a unit may open.

    Args:
        values: Entries of ``{"path": ..., "mode": ...}``, or bare paths (read and write).

    Returns:
        The devices, once each by path.

    Raises:
        ValidationError: Not a list, a path outside ``/dev``, a group name that is not one, or a
            mode that is not ``r``, ``w`` or ``rw``.
    """
    how = "Name a node under /dev (/dev/nvidia0) or a device group (char-nvidia, char-drm)."
    if not isinstance(values, (list, tuple)):
        raise ValidationError("devices must be a list", details=how, field="devices")
    found: dict[str, Device] = {}
    for entry in values:
        if isinstance(entry, Mapping):
            path, mode = str(entry.get("path", "")), str(entry.get("mode", "rw"))
        elif isinstance(entry, str):
            path, _, given = entry.partition(":")
            mode = given or "rw"
        else:
            raise ValidationError(f"{entry!r} is not a device", details=how, field="devices")
        if mode not in DEVICE_MODES:
            raise ValidationError(
                f"{mode!r} is not a way to open a device",
                details=f"Use one of: {', '.join(DEVICE_MODES)}.",
                field="devices",
            )
        node = path.startswith("/dev/")
        if (node and (".." in path.split("/") or not _DEVICE_NODE.fullmatch(path))) or (
            not node and not _DEVICE_GROUP.fullmatch(path)
        ):
            raise ValidationError(
                f"{path!r} is not a device Noust can allow", details=how, field="devices"
            )
        found[path] = Device(path, mode)
    return tuple(found.values())


def validate(options: Mapping[str, object], *, cpus: int | None = None) -> Tuning:
    """
    Check a set of options as a whole.

    Args:
        options: Option name to value; a name that is absent or None is not set.
        cpus: CPUs on this machine; read from the machine when None.

    Returns:
        The tuning.

    Raises:
        ValidationError: An option is unknown, out of range or not of its type.
    """
    unknown = sorted(set(options) - set(OPTION_NAMES))
    if unknown:
        raise ValidationError(
            f"Unknown tuning option: {', '.join(unknown)}",
            details=f"Options: {', '.join(OPTION_NAMES)}.",
            field=unknown[0],
        )
    values: dict[str, Any] = {}
    for name in ("memory_high_mb", "memory_swap_max_mb", "cpu_weight", "io_weight"):
        if options.get(name) is not None:
            values[name] = _integer(name, options[name])
    for name in ("limit_nofile", "limit_nproc"):
        if options.get(name) is not None:
            values[name] = _integer(name, options[name])
    if options.get("allowed_cpus") not in (None, ""):
        values["allowed_cpus"] = check_cpu_set(options["allowed_cpus"], cpus=cpus)
    memlock = options.get("limit_memlock_mb")
    if memlock is not None:
        if memlock == "infinity":
            values["limit_memlock_mb"] = "infinity"
        else:
            if isinstance(memlock, bool) or not isinstance(memlock, int):
                raise ValidationError(
                    f"limit_memlock_mb must be a number of megabytes or 'infinity', not {memlock!r}",
                    details=f"Use 0 to {MAX_MEMLOCK_MB}.",
                    field="limit_memlock_mb",
                )
            if not 0 <= memlock <= MAX_MEMLOCK_MB:
                raise ValidationError(
                    f"limit_memlock_mb of {memlock} is not possible",
                    details=f"Use 0 to {MAX_MEMLOCK_MB}, or 'infinity'.",
                    field="limit_memlock_mb",
                )
            values["limit_memlock_mb"] = memlock
    if options.get("core_dumps") is not None:
        if not isinstance(options["core_dumps"], bool):
            raise ValidationError(
                "core_dumps must be true or false",
                details="true allows core dumps.",
                field="core_dumps",
            )
        values["core_dumps"] = options["core_dumps"]
    if options.get("capabilities"):
        values["capabilities"] = check_capabilities(options["capabilities"])
    if options.get("devices"):
        values["devices"] = check_devices(options["devices"])
    return Tuning(**values)


def from_record(record: TuningRecord | None) -> Tuning:
    """
    Read the tuning a stored record holds.

    Args:
        record: The application's record, or None.

    Returns:
        The tuning; none for an application never tuned. A stored option that no longer validates
        (the machine lost a CPU) is left out rather than failing every unit write.
    """
    if record is None:
        return Tuning()
    try:
        return validate(record.options)
    except ValidationError:
        kept: dict[str, object] = {}
        for name, value in record.options.items():
            try:
                validate({name: value})
            except ValidationError:
                continue
            kept[name] = value
        return validate(kept)


def check_against_limits(tuning: Tuning, app: App) -> None:
    """
    Refuse a combination that does nothing or contradicts the application's hard limits.

    Args:
        tuning: The tuning.
        app: The application, whose ``memory_max_mb`` is the hard limit.

    Raises:
        ValidationError: A soft memory limit above the hard one.
    """
    if (
        tuning.memory_high_mb is not None
        and app.memory_max_mb is not None
        and tuning.memory_high_mb > app.memory_max_mb
    ):
        raise ValidationError(
            f"The soft memory limit ({tuning.memory_high_mb} MB) is above the hard one "
            f"({app.memory_max_mb} MB)",
            details="MemoryHigh only throttles before MemoryMax kills: put it below. "
            f"Raise the hard limit first with: noust app limits {app.domain} --memory MB",
            field="memory_high_mb",
        )


def _size(megabytes: int | str) -> str:
    """
    Args:
        megabytes: A size in MB, or ``infinity``.

    Returns:
        The value of a size directive.
    """
    return "infinity" if megabytes == "infinity" else f"{megabytes}M"


def directives(tuning: Tuning) -> list[str]:
    """
    Write tuning as unit directives.

    Args:
        tuning: The options.

    Returns:
        One line per directive, in a fixed order; empty for no tuning.
    """
    lines: list[str] = []
    if tuning.memory_high_mb is not None:
        lines.append(f"MemoryHigh={tuning.memory_high_mb}M")
    if tuning.memory_swap_max_mb is not None:
        lines.append(f"MemorySwapMax={tuning.memory_swap_max_mb}M")
    if tuning.cpu_weight is not None:
        lines.append(f"CPUWeight={tuning.cpu_weight}")
    if tuning.allowed_cpus:
        lines.append(f"AllowedCPUs={tuning.allowed_cpus}")
    if tuning.io_weight is not None:
        lines.append(f"IOWeight={tuning.io_weight}")
    if tuning.limit_nofile is not None:
        lines.append(f"LimitNOFILE={tuning.limit_nofile}")
    if tuning.limit_nproc is not None:
        lines.append(f"LimitNPROC={tuning.limit_nproc}")
    if tuning.limit_memlock_mb is not None:
        lines.append(f"LimitMEMLOCK={_size(tuning.limit_memlock_mb)}")
    if tuning.core_dumps is not None:
        lines.append("LimitCORE=infinity" if tuning.core_dumps else "LimitCORE=0")
    if tuning.capabilities:
        named = " ".join(f"CAP_{name}" for name in tuning.capabilities)
        # After the sandbox's empty set: an empty assignment resets the list, a later one fills it.
        lines += [f"CapabilityBoundingSet={named}", f"AmbientCapabilities={named}"]
    if tuning.devices:
        lines += ["PrivateDevices=false", "DevicePolicy=closed"]
        lines += [f"DeviceAllow={device.path} {device.mode}" for device in tuning.devices]
    return lines


def directives_for_app(app: App, record: TuningRecord | None) -> list[str]:
    """
    Write the tuning an application's row asks for.

    Args:
        app: The application.
        record: Its tuning record, or None for a unit never tuned.

    Returns:
        The directives; empty for an application with none, or that cannot be tuned.
    """
    if record is None or unit_hardening.ineligible(app) is not None:
        return []
    return directives(from_record(record))


def tuning_for_unit(unit: str, apps: Sequence[App]) -> list[str]:
    """
    Name the directives the unit of an application must be written with.

    The chokepoint ``ServiceManager`` asks whenever it writes an application's unit or its
    blue/green template, so whoever writes it (a deploy, a migration, the console) gives it the
    tuning the application has.

    Args:
        unit: The unit's name without ``.service`` or the ``@``.
        apps: The applications the store records.

    Returns:
        The directives; empty when no application owns the unit or it has none.
    """
    from pathlib import Path

    from noust.core.unit_tuning_store import get_record

    for app in apps:
        if app.app_path and Path(app.app_path).name == unit:
            return directives_for_app(app, get_record(app.domain))
    return []


def with_tuning(unit: str, lines: Sequence[str]) -> str:
    """
    Put tuning into an existing unit, leaving everything else as it is.

    The unit is edited rather than rendered again, because an operator may have edited it by hand
    (the console's unit editor exists for that). The block this module wrote before is removed and
    the new one goes at the end of ``[Service]``, after everything it overrides.

    Args:
        unit: The unit file.
        lines: What :func:`directives` returned; empty removes the block.

    Returns:
        The unit with exactly this tuning.

    Raises:
        ServiceError: The unit has no ``[Service]`` section.
    """
    section = ""
    start: int | None = None
    end: int | None = None
    kept: list[str] = []
    in_block = False
    for line in unit.split("\n"):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if section == "[Service]" and end is None:
                end = len(kept)
            section = stripped
            in_block = False
            if section == "[Service]":
                start = len(kept)
        elif section == "[Service]":
            if stripped == BLOCK_COMMENT[0]:
                in_block = True
                if kept and not kept[-1].strip():
                    # The blank line that set the block apart goes with it.
                    kept.pop()
                continue
            if in_block:
                if not stripped:
                    in_block = False
                else:
                    continue
        kept.append(line)
    if start is None:
        raise ServiceError(
            "The unit has no [Service] section to tune",
            details="Check the unit with 'systemctl cat <name>'; Noust writes one in every unit.",
        )
    if end is None:
        end = len(kept)
    while end > start + 1 and not kept[end - 1].strip():
        end -= 1
    if not lines:
        return "\n".join(kept)
    block = ["", *BLOCK_COMMENT, *lines]
    return "\n".join(kept[:end] + block + kept[end:])
