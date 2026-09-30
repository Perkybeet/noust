# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Restarting the services an update left running on replaced libraries.

An update replaces a library on disk; a service that loaded the old one keeps
running it, fix or no fix, until it restarts. The restart probe of each package
family (needrestart, ``dnf needs-restarting -s``, ``zypper ps -sss``) lists them;
this is what the operator does with the list short of a reboot.

What may be restarted is decided by :meth:`ServiceManager.restart_outdated
<noust.managers.service_manager.ServiceManager.restart_outdated>`, the chokepoint
(rule 4); the plan here only reads that verdict in advance, so the console can
show it before anything happens. The console's own unit, when it is on the
list, restarts last and without blocking: the page that asked is served by it,
and says so before, not after.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from noust.core import paths
from noust.core.exceptions import ServiceError
from noust.managers.service_manager import ServiceManager, outdated_restart_refusal

#: The console's units: restarting one ends the request that asked for it.
CONSOLE_UNITS = frozenset(f"{unit}.service" for unit in (paths.WEB_UNIT, paths.LEGACY_WEB_UNIT))

#: Why a unit that was asked for is not restarted.
NOT_REPORTED = "it does not run replaced libraries"


@dataclass(frozen=True)
class RestartPlan:
    """
    Which of the services on replaced libraries would restart, in order.

    Attributes:
        services: What the probe reported.
        restart: What restarts, the console's own unit last.
        refused: Unit to why it does not restart.
        restarts_console: The console restarts at the end.
    """

    services: tuple[str, ...]
    restart: tuple[str, ...]
    refused: dict[str, str] = field(default_factory=dict)
    restarts_console: bool = False


@dataclass(frozen=True)
class RestartOutcome:
    """
    What restarting did.

    Attributes:
        restarted: The units restarted (the console's queued).
        failed: Unit to systemd's own words.
    """

    restarted: tuple[str, ...]
    failed: dict[str, str]


def plan_service_restarts(
    services: Sequence[str], requested: Sequence[str] | None = None
) -> RestartPlan:
    """
    Decide which services on replaced libraries restart.

    Args:
        services: What the restart probe reported.
        requested: The units the operator chose; every reported one when None.

    Returns:
        The plan.
    """
    reported = tuple(sorted({unit.strip() for unit in services if unit.strip()}))
    chosen = reported if requested is None else tuple(dict.fromkeys(u.strip() for u in requested))
    refused: dict[str, str] = {}
    restart: list[str] = []
    for unit in chosen:
        if unit not in reported:
            refused[unit] = NOT_REPORTED
            continue
        reason = outdated_restart_refusal(unit)
        if reason is not None:
            refused[unit] = reason
            continue
        restart.append(unit)
    ordered = sorted(restart, key=lambda unit: (unit in CONSOLE_UNITS, unit))
    return RestartPlan(
        services=reported,
        restart=tuple(ordered),
        refused=refused,
        restarts_console=any(unit in CONSOLE_UNITS for unit in ordered),
    )


def restart_services(
    plan: RestartPlan, services: ServiceManager, *, on_line: Callable[[str], None]
) -> RestartOutcome:
    """
    Restart what a plan says, one unit at a time, the console's last.

    A unit that fails does not stop the others: each is its own service, and
    the operator reads every failure at the end, in systemd's words.

    Args:
        plan: The plan.
        services: The service manager, whose guard every restart passes.
        on_line: Where each step is said.

    Returns:
        What restarted and what failed.
    """
    restarted: list[str] = []
    failed: dict[str, str] = {}
    for unit in plan.restart:
        console = unit in CONSOLE_UNITS
        if console:
            on_line(f"Restarting the console ({unit}): this page reconnects by itself")
        else:
            on_line(f"Restarting {unit}")
        try:
            services.restart_outdated(unit, outdated=plan.services, no_block=console)
        except ServiceError as exc:
            words = (exc.details or exc.message).strip()
            on_line(f"{unit} did not restart: {words}")
            failed[unit] = words
            continue
        restarted.append(unit)
    for unit, reason in plan.refused.items():
        on_line(f"Left as it is: {unit} ({reason})")
    return RestartOutcome(restarted=tuple(restarted), failed=failed)


__all__ = [
    "CONSOLE_UNITS",
    "RestartOutcome",
    "RestartPlan",
    "plan_service_restarts",
    "restart_services",
]
