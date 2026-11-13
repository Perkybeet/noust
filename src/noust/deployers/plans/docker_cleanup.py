# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a cleanup of Docker would do, in the common shape (item 183).

A cleanup was already a plan: :meth:`noust.core.docker.gate.DockerGate.cleanup_plan` says what would go
and what stays, and keeps its route and its answer. This is the other half of "what will it do": the
commands that applying that plan, as it was shown, would run. They come from the code that applies it
(:meth:`~noust.core.docker.gate.DockerGate.apply_cleanup`), rehearsed with the token and the choice a
person made, so what is listed is what is removed, through the gate that judges each owner again.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from noust.core.docker.gate import DockerGate
from noust.core.plans.shape import PlanOut
from noust.core.server_text import say
from noust.deployers.plans import common

OPERATION = "docker.cleanup"


def plan_docker_cleanup(
    token: str,
    select: Sequence[str],
    *,
    confirm_names: Mapping[str, str] | None = None,
    gate: Callable[[], DockerGate] = DockerGate,
) -> PlanOut:
    """
    Say what applying a cleanup plan, as it was shown, would run.

    Args:
        token: The token of the plan that was shown.
        select: The id of each item chosen.
        confirm_names: For each volume chosen, its name as the person typed it.
        gate: Builds the gate; the one of the request when it is asked for, so what it sees is what
            the person who asks sees.

    Returns:
        The plan. A cleanup that is no longer the one that was shown, or that chose something that is
        not in it, is a plan that says it would be refused.
    """
    named = dict(confirm_names or {})
    return common.plan_call(
        OPERATION,
        "docker",
        say("plans.docker.cleanup", count=len(select)),
        lambda: gate().apply_cleanup(token, list(select), confirm_names=named),
        domain=None,
        unknown=[say("plans.unknown.docker_owner")],
    )
