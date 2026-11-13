# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What restarting, stopping or starting an application would do (item 183).

The three are one function of the console (``_service_action`` of :mod:`noust.web.api.apps`: the
unit, the processes that belong to it and its copies), and the plan rehearses it. That function is of
the web layer, so this plan is too: a deployer or a command cannot import it.
"""

from __future__ import annotations

from noust.core.plans.shape import PlanOut
from noust.core.server_text import ServerText, say
from noust.deployers.plans import common
from noust.web.api.apps import _service_action

#: What each action says it did, as the operation's own response words it.
_PAST = {"restart": "restarted", "stop": "stopped", "start": "started"}

#: What each action changes, as the catalog says it.
_EFFECT = {
    "restart": "plans.service.restart",
    "stop": "plans.service.stop",
    "start": "plans.service.start",
}


def plan_service(domain: str, action: str) -> PlanOut:
    """
    Say what an action on an application's service would do.

    Args:
        domain: The application's domain, validated.
        action: ``restart``, ``stop`` or ``start``.

    Returns:
        The plan.

    Raises:
        HTTPException: 404 when the application has no unit, as the action says it.
    """
    unknown: list[ServerText] = []
    if action != "stop":
        unknown.append(say("plans.unknown.service_health", domain=domain))
    return common.plan_call(
        f"app.{action}",
        domain,
        say(_EFFECT[action], domain=domain),
        lambda: _service_action(domain, action, _PAST[action]),
        domain=domain,
        unknown=unknown,
    )
