# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What moving an application onto releases would do, in the common shape (item 183).

The move already had a plan (:func:`noust.deployers.migrate.plan_migration`), which keeps its route and
its answer: what would go to ``shared/``, which unit and site are rewritten, what stays out. This adds
the common shape to it. The move itself has a rehearsal of its own that says what it would move without
running each command (:func:`~noust.deployers.migrate.migrate` under ``--dry-run``), so what this plan
lists of the commands is what that rehearsal runs, and it says it is not the whole.
"""

from __future__ import annotations

from collections.abc import Sequence

from noust.core.exceptions import NoustError
from noust.core.plans.shape import PlanOut
from noust.core.server_text import ServerText, say
from noust.core.store import get_store
from noust.deployers import migrate
from noust.deployers.plans import common
from noust.validators.domain import validate_domain

OPERATION = "app.migrate"


def plan_migrate(domain: str, persist: Sequence[str] | None = None) -> PlanOut:
    """
    Say what moving an application onto releases would do.

    Args:
        domain: The application's domain.
        persist: Paths to keep in ``shared/``; what git does not track when omitted.

    Returns:
        The plan.

    Raises:
        NoustError: The application is not deployed, is on releases already, or cannot use them.
    """
    domain = validate_domain(domain)
    if get_store().get_app(domain) is None:
        raise NoustError(
            f"Application not found: {domain}", details="Run 'noust list' to see what is deployed."
        )
    plan = migrate.plan_migration(domain, persist)
    findings: list[ServerText] = [
        say("plans.found.migrate_persistent", count=len(plan.persistent)),
        say("plans.found.migrate_untracked", count=len(plan.untracked_files)),
    ]
    findings.extend(say("plans.found.migrate_warning", warning=text) for text in plan.warnings)
    return common.plan_call(
        OPERATION,
        domain,
        say("plans.migrate.app", domain=domain, release=plan.release_id),
        lambda: migrate.migrate(domain, plan, logger=common.quiet_logger()),
        domain=domain,
        findings=findings,
        unknown=[say("plans.unknown.migrate_steps")],
    )
