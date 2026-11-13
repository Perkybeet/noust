# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What changing an application's resource limits would do (item 183).

The change is :func:`noust.deployers.lifecycle.set_resource_limits`, rehearsed: the unit it would
rewrite (with the diff of the file, which holds no secret), the reload and, when asked to restart,
the restart behind the health check.
"""

from __future__ import annotations

from noust.core.exceptions import NoustError
from noust.core.plans.shape import PlanOut
from noust.core.server_text import ServerText, say
from noust.core.store import get_store
from noust.deployers import lifecycle
from noust.deployers.helpers.health_gate import HealthCheck
from noust.deployers.plans import common
from noust.managers.service_manager import ResourceLimits
from noust.validators.domain import validate_domain

OPERATION = "app.limits"


def _said(value: int | None, unit: str = "") -> str:
    """
    Args:
        value: A limit, or None for no limit.
        unit: What it is measured in, put after the number.

    Returns:
        The limit as a sentence puts it; ``-`` for none.
    """
    return "-" if value is None else f"{value}{unit}"


def plan_limits(
    domain: str,
    *,
    memory_max_mb: int | None = None,
    cpu_quota_percent: int | None = None,
    tasks_max: int | None = None,
    restart: bool = False,
) -> PlanOut:
    """
    Say what giving an application these limits would do.

    Args:
        domain: The application's domain.
        memory_max_mb: ``MemoryMax`` in MB; None for no limit.
        cpu_quota_percent: ``CPUQuota``; None for no limit.
        tasks_max: ``TasksMax``; None for no limit.
        restart: Restart now, so the processes run under the new limits.

    Returns:
        The plan.

    Raises:
        NoustError: The application is not deployed.
        ValidationError: A limit is out of range.
    """
    domain = validate_domain(domain)
    app = get_store().get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}",
            details="Run 'noust list' to see what is deployed.",
        )
    limits = ResourceLimits(
        memory_max_mb=memory_max_mb, cpu_quota_percent=cpu_quota_percent, tasks_max=tasks_max
    )
    shown = {
        "domain": domain,
        "memory": _said(memory_max_mb, " MB"),
        "cpu": _said(cpu_quota_percent, "%"),
        "tasks": _said(tasks_max),
    }
    effect = say("plans.limits.restart" if restart else "plans.limits.no_restart", **shown)
    unknown: list[ServerText] = []
    if restart:
        unknown.append(
            say("plans.unknown.health_limits", check=HealthCheck.for_app(app).describe(app.port))
        )
    return common.plan_call(
        OPERATION,
        domain,
        effect,
        lambda: lifecycle.set_resource_limits(
            domain, limits, restart=restart, logger=common.quiet_logger()
        ),
        domain=domain,
        unknown=unknown,
    )
