# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application's unit may do: systemd's sandbox, without containers.

An application's unit already had ``NoNewPrivileges`` and ``PrivateTmp``, and
since 3.2 its own account. This adds the rest of what systemd can take away from
a process for free (checklist B-58): a filesystem that is read-only but for the
application's own directories and the ones the operator declares, no home, no
device nodes, no kernel tunables, modules, logs, clock or control groups, only
the socket families a web application uses, a system-call filter, and no
capability at all. A Docker container does the same with ``cap_drop`` and a
read-only root; Coolify applies none of it by default.

**Profiles.** ``default`` is what nothing legitimate for a web application
needs and everything else is built to survive. ``strict`` adds the privileged
and resource system calls and, where the runtime has no JIT, a ban on writable
executable memory; it is opt-in because a runtime that maps its own code (Node's
V8, a Python with cffi) breaks under it. ``off`` is the unit as it was before
3.4, for the application that cannot run any other way. The directives are one
list built here, which the unit template renders when a unit is created and
:func:`with_hardening` writes into one that exists, so the two cannot disagree.

**New applications** are hardened from their first deployment
(:func:`adopt_new_app`; ``deploy.hardening`` sets the profile a server gives
them). **Existing ones** are not touched by a redeploy: an operator moves them
with ``noust app hardening``, which restarts the application behind the same
health gate as a deploy and puts the previous unit back when it does not
answer (:mod:`noust.deployers.hardening`).

**Who is hardened.** An application whose unit runs its code: Node, Python and
the like. Not a Docker Compose stack or a container application (their processes
are Docker's, and the unit that starts them is root's), not a monorepo (one
unit per workspace), not a site nothing runs for, and not a PHP application
(its pool lives in the PHP-FPM every PHP application shares).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from noust.core import paths
from noust.core.config import Config
from noust.core.exceptions import ServiceError, ValidationError
from noust.core.store import App, NoustStore, get_store
from noust.core.unit_hardening_store import PROFILES, HardeningRecord, get_record, set_record

if TYPE_CHECKING:
    from noust.core.runner import CommandRunner

logger = logging.getLogger(__name__)

#: ``systemd-analyze security`` reads a unit and reports a score; read only.
READ_ONLY_PROBES: tuple[tuple[object, ...], ...] = (
    ("systemd-analyze", "security", "--no-pager", "*"),
)

#: Deadline of ``systemd-analyze security``, which parses the unit and loads no process.
ANALYZE_TIMEOUT = 60

#: The runtime families, which decide what ``strict`` may add.
FAMILY_NODE = "node"
FAMILY_PYTHON = "python"
FAMILY_GENERIC = "generic"

#: Application types whose runtime maps its own executable code (V8's JIT).
_NODE_TYPES = frozenset(
    {
        "nodejs",
        "nextjs",
        "nuxt",
        "remix",
        "astro",
        "sveltekit",
        "angular",
        "vite",
    }
)

#: The directives this module owns, which :func:`with_hardening` removes before it writes.
HARDENING_KEYS = frozenset(
    {
        "ProtectSystem",
        "ProtectHome",
        "PrivateDevices",
        "ProtectKernelTunables",
        "ProtectKernelModules",
        "ProtectKernelLogs",
        "ProtectControlGroups",
        "ProtectClock",
        "ProtectHostname",
        "ProtectProc",
        "LockPersonality",
        "RestrictRealtime",
        "RestrictSUIDSGID",
        "RestrictNamespaces",
        "RestrictAddressFamilies",
        "SystemCallArchitectures",
        "SystemCallFilter",
        "SystemCallErrorNumber",
        "CapabilityBoundingSet",
        "AmbientCapabilities",
        "ReadWritePaths",
        "MemoryDenyWriteExecute",
        "RemoveIPC",
    }
)

#: The comment that heads the block, so a unit explains the lines to whoever reads it.
BLOCK_COMMENT = (
    "# Hardening: a read-only filesystem but for the application's own directories",
    "# and the ones declared, no capability, a system-call filter. Change it with:",
    "# noust app hardening",
)

#: Characters a path may hold to be named in ``ReadWritePaths=``: ``%`` is a
#: specifier and whitespace separates entries, so a path with either would be
#: misread rather than refused here.
_UNIT_PATH = re.compile(r"^/[A-Za-z0-9._/@+=,-]*$")

#: Where nothing may be made writable: the system, the devices, and Noust's own state.
_NEVER_WRITABLE = (
    "/",
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/lib",
    "/lib32",
    "/lib64",
    "/proc",
    "/root",
    "/run",
    "/sbin",
    "/sys",
    "/usr",
    "/var",
    "/var/lib",
    "/var/run",
)

#: Where an application's own tree may live and ``ProtectHome`` would hide it.
_HOME_PREFIXES = ("/home", "/root")

#: How ``systemd-analyze security`` ends its report.
_EXPOSURE = re.compile(r"Overall exposure level for (\S+): ([0-9.]+) ([A-Z]+)")


@dataclass(frozen=True)
class Exposure:
    """
    What ``systemd-analyze security`` says about a unit.

    Attributes:
        unit: The unit analysed.
        score: Its exposure, from 0.0 (nothing to take away) to 10.0.
        label: systemd's word for it (``SAFE``, ``OK``, ``MEDIUM``, ``EXPOSED``,
            ``UNSAFE``).
    """

    unit: str
    score: float
    label: str


def family_of(app_type: str | None) -> str:
    """
    Say which runtime family an application type belongs to.

    Args:
        app_type: The application's type.

    Returns:
        ``node``, ``python`` or ``generic``.
    """
    kind = app_type or ""
    if kind in _NODE_TYPES:
        return FAMILY_NODE
    if kind == "python":
        return FAMILY_PYTHON
    return FAMILY_GENERIC


def ineligible(app: App) -> str | None:
    """
    Say why an application's unit cannot be hardened.

    Args:
        app: Its row.

    Returns:
        The reason, or None when it can.
    """
    kind = app.app_type or ""
    if kind == "adopted":
        return "its unit is yours: Noust restarts an adopted site and never rewrites its unit"
    if kind in ("docker-compose", "container"):
        return (
            "its processes are Docker's: the unit that starts the stack is root's, "
            "and the containers are confined by Docker"
        )
    if kind == "monorepo":
        return "a monorepo runs several units, which are not hardened yet"
    if kind == "php-fpm":
        return "its code runs in the PHP-FPM every PHP application shares"
    if kind == "static" or app.is_static:
        return "nothing runs for it: the web server serves its files"
    return None


def check_writable_path(path: str) -> str:
    """
    Accept a directory a hardened unit may write to.

    Args:
        path: An absolute path.

    Returns:
        The path without a trailing slash.

    Raises:
        ValidationError: It is relative, holds a character a unit would misread,
            climbs with ``..``, is the root of something the system owns, or is
            Noust's own state.
    """
    how = "Name the directory the application writes to, such as /var/lib/shop or /srv/uploads."
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValidationError(f"{path!r} is not an absolute path", details=how, field="paths")
    cleaned = path.rstrip("/") or "/"
    if ".." in cleaned.split("/") or not _UNIT_PATH.fullmatch(cleaned):
        raise ValidationError(
            f"{path!r} cannot be named in a unit",
            details=f"{how} Letters, digits and . _ - / @ + = , only; no spaces and no '..'.",
            field="paths",
        )
    if cleaned in _NEVER_WRITABLE:
        raise ValidationError(
            f"{cleaned} is the system's: it cannot be made writable",
            details=how,
            field="paths",
        )
    protected = (
        "/etc",
        "/usr",
        "/boot",
        "/proc",
        "/sys",
        "/dev",
        "/bin",
        "/sbin",
        "/lib",
        "/lib64",
        str(paths.config_dir()),
        str(paths.state_dir()),
        str(paths.backup_dir()),
    )
    for guarded in protected:
        if cleaned == guarded or cleaned.startswith(guarded + "/"):
            raise ValidationError(
                f"{cleaned} is under {guarded}, which an application's unit never writes",
                details=how,
                field="paths",
            )
    return cleaned


def check_writable_paths(values: Sequence[str]) -> tuple[str, ...]:
    """
    Accept the directories an operator declares, once each.

    Args:
        values: The paths.

    Returns:
        The validated paths, in order, without repeats.

    Raises:
        ValidationError: See :func:`check_writable_path`.
    """
    seen: list[str] = []
    for value in values:
        cleaned = check_writable_path(value)
        if cleaned not in seen:
            seen.append(cleaned)
    return tuple(seen)


def directives_for(
    profile: str,
    *,
    family: str,
    app_root: Path,
    extra_writable: Sequence[str] = (),
) -> list[str]:
    """
    Write a profile as unit directives.

    Args:
        profile: ``off``, ``default`` or ``strict``.
        family: The runtime family (:func:`family_of`).
        app_root: The application's directory, which stays writable.
        extra_writable: Directories the operator declared.

    Returns:
        One line per directive, in a fixed order; empty for ``off``.

    Raises:
        ValidationError: The profile is not one of :data:`PROFILES`.
    """
    if profile not in PROFILES:
        raise ValidationError(
            f"{profile!r} is not a hardening profile",
            details=f"Use one of: {', '.join(PROFILES)}.",
            field="profile",
        )
    if profile == "off":
        return []
    root = str(app_root)
    writable = [root, *check_writable_paths(extra_writable)]
    lines = [
        "ProtectSystem=strict",
        # '-' skips a directory that does not exist yet, so a declared path
        # that is created later does not stop the unit from starting.
        "ReadWritePaths=" + " ".join(f"-{path}" for path in dict.fromkeys(writable)),
    ]
    if not root.startswith(_HOME_PREFIXES):
        # An application that lives under /home would be hidden from itself.
        lines.append("ProtectHome=true")
    lines += [
        "PrivateDevices=true",
        "ProtectKernelTunables=true",
        "ProtectKernelModules=true",
        "ProtectKernelLogs=true",
        "ProtectControlGroups=true",
        "ProtectClock=true",
        "ProtectHostname=true",
        "ProtectProc=invisible",
        "LockPersonality=true",
        "RestrictRealtime=true",
        "RestrictSUIDSGID=true",
        "RestrictNamespaces=true",
        # AF_NETLINK is there because Node's os.networkInterfaces() fails
        # without it, and every framework build and server calls it.
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK",
        "SystemCallArchitectures=native",
        "SystemCallFilter=@system-service",
        # A refused call fails with EPERM and the program carries on, instead
        # of the whole process being killed by SIGSYS.
        "SystemCallErrorNumber=EPERM",
        "CapabilityBoundingSet=",
        "AmbientCapabilities=",
    ]
    if profile == "strict":
        lines += [
            "SystemCallFilter=~@privileged @resources",
            "RemoveIPC=true",
        ]
        if family != FAMILY_NODE:
            # V8 maps writable executable memory for its JIT: banning it
            # would stop a Node application from starting.
            lines.append("MemoryDenyWriteExecute=true")
    return lines


def directives_for_app(app: App, record: HardeningRecord | None) -> list[str]:
    """
    Write the hardening an application's row asks for.

    Args:
        app: The application.
        record: Its hardening record, or None for one that predates it.

    Returns:
        The directives; empty for an application with no record, with the
        profile ``off``, or that cannot be hardened.
    """
    if record is None or record.profile == "off" or ineligible(app) is not None:
        return []
    from noust.deployers.helpers.layout import app_root

    return directives_for(
        record.profile,
        family=family_of(app.app_type),
        app_root=app_root(app),
        extra_writable=record.writable_paths,
    )


def with_hardening(unit: str, directives: Sequence[str]) -> str:
    """
    Put hardening into an existing unit, leaving everything else as it is.

    The unit is edited rather than rendered again, because an operator may have
    edited it by hand (the console's unit editor exists for that) and a profile
    is no reason to throw that away. Any directive this module owns is removed
    from ``[Service]`` first; the block goes after ``PrivateTmp=`` when the unit
    has it, which is where the template puts it, or at the end of the section.

    Args:
        unit: The unit file.
        directives: What :func:`directives_for` returned; empty removes the block.

    Returns:
        The unit with exactly these directives.

    Raises:
        ServiceError: The unit has no ``[Service]`` section.
    """
    section = ""
    start: int | None = None
    end: int | None = None
    anchor: int | None = None
    kept: list[str] = []
    for line in unit.split("\n"):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if section == "[Service]" and end is None:
                end = len(kept)
            section = stripped
            if section == "[Service]":
                start = len(kept)
        elif section == "[Service]":
            key = stripped.split("=", 1)[0].strip()
            if stripped == BLOCK_COMMENT[0] and kept and not kept[-1].strip():
                # The blank line that set the block apart goes with it.
                kept.pop()
            if stripped in BLOCK_COMMENT or (
                "=" in stripped and not stripped.startswith("#") and key in HARDENING_KEYS
            ):
                continue
            if stripped.startswith("PrivateTmp="):
                anchor = len(kept) + 1
        kept.append(line)
    if start is None:
        raise ServiceError(
            "The unit has no [Service] section to harden",
            details="Check the unit with 'systemctl cat <name>'; Noust writes one in every unit.",
        )
    if end is None:
        end = len(kept)
    while end > start + 1 and not kept[end - 1].strip():
        end -= 1
    position = anchor if anchor is not None else end
    if not directives:
        return "\n".join(kept)
    block = ["", *BLOCK_COMMENT, *directives]
    return "\n".join(kept[:position] + block + kept[position:])


def parse_exposure(output: str) -> Exposure | None:
    """
    Read the verdict at the end of ``systemd-analyze security``.

    Args:
        output: What the command printed.

    Returns:
        The unit's exposure, or None when the report has no verdict.
    """
    verdicts = list(_EXPOSURE.finditer(output))
    if not verdicts:
        return None
    found = verdicts[-1]
    return Exposure(unit=found.group(1), score=float(found.group(2)), label=found.group(3))


def exposure_of(unit: str, runner: CommandRunner) -> Exposure | None:
    """
    Ask systemd how exposed a unit is.

    Args:
        unit: The unit, with or without ``.service``.
        runner: The runner.

    Returns:
        Its exposure, or None when systemd-analyze is absent, older than the
        feature, or cannot read the unit.
    """
    name = unit if unit.endswith(".service") else f"{unit}.service"
    if not runner.exists("systemd-analyze"):
        return None
    result = runner.run(
        ["systemd-analyze", "security", "--no-pager", name], timeout=ANALYZE_TIMEOUT
    )
    if not result.success:
        logger.debug("systemd-analyze security %s: %s", name, (result.stderr or "").strip())
        return None
    return parse_exposure(result.stdout)


def default_profile(config: Config | None = None) -> str:
    """
    Say which profile a new application gets.

    Args:
        config: The configuration; read when None.

    Returns:
        ``deploy.hardening``: ``default`` unless the operator set another.
    """
    value = str((config or Config()).get("deploy.hardening", "default") or "default")
    return value if value in PROFILES else "default"


def adopt_new_app(domain: str, *, store: NoustStore | None = None) -> HardeningRecord | None:
    """
    Harden an application created now.

    Called by a deployment on an application's first deployment, before its
    unit is written: the unit asks :func:`hardening_for_unit` for its directives.
    An application that already has a record keeps it.

    Args:
        domain: The application, whose row exists.
        store: The store; the process-wide one when None.

    Returns:
        Its record, or None when it cannot be hardened.
    """
    target = store or get_store()
    app = target.get_app(domain)
    if app is None or ineligible(app) is not None:
        return None
    set_record(domain, default_profile(), (), changed_by="noust", keep_existing=True, store=target)
    return get_record(domain, store=target)


def hardening_for_unit(unit: str, apps: Sequence[App]) -> list[str]:
    """
    Name the directives the unit of an application must be written with.

    The chokepoint ``ServiceManager`` asks whenever it writes an application's
    unit or its blue/green template, so whoever writes it - a deploy, a
    migration, the console - gives it the hardening the application has, as
    for its account.

    Args:
        unit: The unit's name without ``.service`` or the ``@``.
        apps: The applications the store records.

    Returns:
        The directives; empty when no application owns the unit or it has none.
    """
    for app in apps:
        if app.app_path and Path(app.app_path).name == unit:
            return directives_for_app(app, get_record(app.domain))
    return []
