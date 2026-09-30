# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The server in one look: what the overview page and a fleet's server list need.

One function, :func:`build_summary`, answers "how is this server?" from facts that
are cheap to read or already remembered (:mod:`noust.managers.server.facts`),
never from a probe that takes a second. The page that shows it opens at once, the
fleet view can ask twenty nodes for it, and what is slow is computed in the
background and picked up on the next look, with its age.

Each section says when its probe failed instead of leaving its box empty: an
``error`` in the tool's own words. A summary with a hole in it and no reason is
the kind of thing that is read as "all fine".

Other areas add their own sections (the hardening checks) through
:func:`register_summary_section`, so this module does not import them and they
do not edit it.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from noust.core.exceptions import NoustError
from noust.managers.server.context import ServerContext
from noust.managers.server.errors import ServerError
from noust.managers.server.facts import Fact

log = logging.getLogger(__name__)

#: How long each fact stays fresh, in seconds.
TTL_UPDATES = 900
TTL_RESTART = 300
TTL_AUTO = 600
TTL_DISK = 30
TTL_TIME = 60
TTL_SWAP = 60
TTL_STATE = 60
TTL_IDENTITY = 60

#: Sections other areas add, by name. Each is called with the context and returns
#: a JSON-serialisable mapping.
_extra_sections: dict[str, Callable[[ServerContext], dict[str, Any]]] = {}

#: What an extra section can fail with; anything else is a bug and stays loud.
_SECTION_ERRORS: tuple[type[Exception], ...] = (NoustError, OSError, ValueError, sqlite3.Error)


def register_summary_section(name: str, build: Callable[[ServerContext], dict[str, Any]]) -> None:
    """
    Add a section to the summary.

    Args:
        name: The key it appears under. Registering a name twice replaces the
            first, which is how a test undoes it.
        build: Returns the section. It must be cheap: it runs on every summary.
    """
    _extra_sections[name] = build


def read_system_state(runner: Any) -> dict[str, Any]:
    """
    Ask systemd whether the whole system is well, and which units failed.

    Args:
        runner: The command runner.

    Returns:
        ``state`` (``running``, ``degraded``, ``starting``...) and ``failed``,
        the names of the units in the failed state.
    """
    state = runner.run(["systemctl", "is-system-running"], timeout=20).stdout.strip() or "unknown"
    failed = runner.run(
        ["systemctl", "--failed", "--no-legend", "--plain", "--no-pager"], timeout=20
    )
    units = [line.split()[0] for line in failed.stdout.splitlines() if line.strip()]
    return {"state": state, "failed": units}


def _error(fact: Fact | None) -> str | None:
    """
    Say why a fact is missing or stale.

    Args:
        fact: The fact.

    Returns:
        The failure, verbatim, or None.
    """
    return fact.error if fact is not None else None


def _updates_section(ctx: ServerContext, wait: bool) -> dict[str, Any]:
    """
    Summarise the pending updates, the reboot and the automatic updates.

    Args:
        ctx: The managers.
        wait: Compute what is missing now instead of in the background.

    Returns:
        The three sections, keyed ``updates``, ``reboot``, ``stale_services`` and
        ``auto_updates``.
    """
    platform = ctx.platform
    pending = ctx.cache.get("updates", ctx.updates.pending, TTL_UPDATES, wait=wait)
    restart = ctx.cache.get("restart", ctx.updates.restart_probe, TTL_RESTART, wait=wait)
    auto = ctx.cache.get("auto", ctx.updates.backend.auto_status, TTL_AUTO, wait=wait)

    listing = pending.value if pending is not None else None
    updates: dict[str, Any] = {
        "supported": platform.updates_supported,
        "security_scope": platform.security_scope_supported,
        "pending": listing.pending if listing else None,
        "security": listing.security if listing else None,
        "kept_back": len(listing.kept_back) if listing else None,
        "broken": listing.broken if listing else None,
        "checked_at": listing.checked_at if listing else None,
        "lists_age_seconds": listing.lists_age_seconds if listing else None,
        "notes": listing.notes if listing else [],
        "error": _error(pending),
    }
    if not platform.updates_supported:
        updates["reason"] = platform.why_updates_unsupported()

    probe = restart.value if restart is not None else None
    reboot: dict[str, Any] = {
        "required": probe.reboot.required if probe else None,
        "since": probe.reboot.since if probe else None,
        "reasons": list(probe.reboot.reasons) if probe else [],
        "packages": list(probe.reboot.packages) if probe else [],
        "detector_available": probe.available if probe else None,
        "checked_at": restart.checked_at if restart else None,
        "error": _error(restart),
    }
    status = auto.value if auto is not None else None
    return {
        "updates": updates,
        "reboot": reboot,
        "stale_services": len(probe.services) if probe else None,
        "auto_updates": {
            "mechanism": status.mechanism if status else None,
            "supported": status.supported if status else None,
            "enabled": status.enabled if status else None,
            "security_only": status.security_only if status else None,
            "reboots": status.reboots if status else None,
            "error": _error(auto),
        },
    }


def _disk_section(ctx: ServerContext, wait: bool) -> dict[str, Any]:
    """
    Summarise the disks by their worst.

    Args:
        ctx: The managers.
        wait: Compute now instead of in the background.

    Returns:
        ``worst_mount``, ``worst_percent``, ``inodes_percent``, ``status`` and
        the number of real mounts.
    """
    fact = ctx.cache.get("disk", ctx.storage.mounts, TTL_DISK, wait=wait)
    mounts = fact.value if fact is not None else None
    worst = ctx.storage.worst_mount(mounts) if mounts else None
    return {
        "worst_mount": worst.mount_point if worst else None,
        "worst_percent": worst.percent_used if worst else None,
        "inodes_percent": max((m.inodes_percent for m in mounts), default=None) if mounts else None,
        "free_bytes": worst.free_bytes if worst else None,
        "status": worst.status if worst else None,
        "mounts": len(mounts) if mounts else 0,
        "error": _error(fact),
    }


def build_summary(ctx: ServerContext, *, wait: bool = False) -> dict[str, Any]:
    """
    Build the server's summary.

    Args:
        ctx: The managers, and the cache the slow facts are kept in.
        wait: Compute a missing fact in the caller. The command line passes
            True; the console never does, so a first look answers with what is
            cheap and what is not yet known says so.

    Returns:
        A JSON-serialisable mapping with the sections ``os``, ``updates``,
        ``reboot``, ``auto_updates``, ``disk``, ``time``, ``swap``, ``power``,
        ``system`` and ``capabilities``, plus whatever other areas registered.
    """
    platform = ctx.platform
    identity_fact = ctx.cache.get("identity", ctx.identity.identity, TTL_IDENTITY, wait=True)
    identity = identity_fact.value if identity_fact is not None else None
    if identity is None:
        # Reading a few files cannot fail in operation; if it did, the summary is
        # not worth building around a hole that hides the reason.
        raise ServerError(
            "Could not read what this machine is",
            (identity_fact.error if identity_fact else None) or "",
        )

    time_fact = ctx.cache.get("time", ctx.clock.status, TTL_TIME, wait=wait)
    clock = time_fact.value if time_fact is not None else None
    swap_fact = ctx.cache.get("swap", ctx.swap.status, TTL_SWAP, wait=wait)
    swap = swap_fact.value if swap_fact is not None else None
    state_fact = ctx.cache.get(
        "state", lambda: read_system_state(ctx.updates.runner), TTL_STATE, wait=wait
    )
    state = state_fact.value if state_fact is not None else None

    schedule = ctx.power.status().scheduled

    summary: dict[str, Any] = {
        "hostname": identity.hostname.hostname,
        "os": {
            "id": identity.os_id,
            "name": identity.os_name,
            "version": identity.os_version,
            "eol": identity.eol.to_dict(),
        },
        "kernel": identity.kernel,
        "uptime_seconds": identity.uptime_seconds,
        **_updates_section(ctx, wait),
        "disk": _disk_section(ctx, wait),
        "time": {
            "timezone": clock.timezone if clock else None,
            "synchronized": clock.synchronized if clock else None,
            "ntp_enabled": clock.ntp_enabled if clock else None,
            "ntp_supported": clock.ntp_supported if clock else None,
            "error": _error(time_fact),
        },
        "swap": {
            "total_bytes": swap.total_bytes if swap else None,
            "used_bytes": swap.used_bytes if swap else None,
            "recommended": swap.recommended if swap else None,
            "error": _error(swap_fact),
        },
        "power": {"scheduled": schedule.to_dict() if schedule else None},
        "system": {
            "state": state["state"] if state else None,
            "failed_units": state["failed"] if state else None,
            "error": _error(state_fact),
        },
        "capabilities": {
            "packages": platform.family.value,
            "updates": platform.updates_supported,
            "security_updates": platform.security_scope_supported,
            "transactional": platform.transactional,
            "container": platform.container,
            "systemd": platform.systemd,
            "swap": not platform.container,
            "docker": ctx.updates.runner.exists("docker"),
        },
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    for name, build in _extra_sections.items():
        try:
            summary[name] = build(ctx)
        except _SECTION_ERRORS as exc:
            log.warning("The summary section %r failed: %s", name, exc)
            summary[name] = {"error": str(exc)}
    return summary
