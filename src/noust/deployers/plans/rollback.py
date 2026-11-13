# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What going back to a deployment would do (item 183).

Going back is :func:`noust.deployers.lifecycle.rollback_to_deployment`, called as it is called to
apply it and rehearsed. What the plan adds is what is known without doing it: which version serves
again and how (a release that is on disk, a commit built again, a backup restored), that the
database does not go back, and whether going back passes a deployment that changed its schema, which
applying it will ask to confirm.
"""

from __future__ import annotations

from noust.core.appdir.layout import app_root
from noust.core.appdir.releases import ReleaseManager
from noust.core.exceptions import DeploymentError, NoustError
from noust.core.plans import engine
from noust.core.plans.shape import PlanBlock, PlanNeeds, PlanOut
from noust.core.server_text import ServerText, say
from noust.core.store import DeploymentRecord, get_store
from noust.deployers import lifecycle
from noust.deployers.helpers.health_gate import HealthCheck
from noust.deployers.plans import common
from noust.validators.domain import validate_domain

OPERATION = "app.rollback"


def _effect(method: str, record: DeploymentRecord, domain: str, previous: str | None) -> ServerText:
    """
    Args:
        method: ``instant``, ``rebuild`` or ``snapshot``, as
            :func:`~noust.deployers.lifecycle.rollback_methods` says.
        record: The deployment gone back to.
        domain: The application.
        previous: The release serving now, when the application has releases.

    Returns:
        What changes, in a sentence.
    """
    if method == "instant":
        return say(
            "plans.rollback.instant",
            domain=domain,
            release=record.release_id or "",
            previous=previous or "",
        )
    if method == "rebuild":
        return say("plans.rollback.rebuild", domain=domain, commit=(record.git_commit or "")[:7])
    return say("plans.rollback.snapshot", domain=domain, backup=record.snapshot_backup or "")


def plan_rollback(domain: str, deployment_id: int) -> PlanOut:
    """
    Say what going back to a deployment would do.

    Args:
        domain: The application's domain.
        deployment_id: The deployment to go back to.

    Returns:
        The plan. When the deployment cannot be gone back to, a plan with nothing to run that says
        why in :attr:`~noust.core.plans.shape.PlanOut.blocked`.

    Raises:
        NoustError: The application is not deployed, or the deployment is not one of its own.
    """
    domain = validate_domain(domain)
    store = get_store()
    app = store.get_app(domain)
    record = store.get_deployment(deployment_id)
    if app is None or record is None or record.domain != domain:
        raise NoustError(
            f"Deployment {deployment_id} of {domain} not found",
            details=f"See the application's history: GET /api/deployments?domain={domain}",
        )

    unavailable = lifecycle.rollback_availability([record]).get(deployment_id)
    if unavailable is not None:
        details = (
            f"Rebuild its commit instead: noust update {domain} --commit {record.git_commit}"
            if record.git_commit
            else f"Restore a backup instead: noust rollback {domain}"
        )
        return PlanOut(
            operation=OPERATION,
            subject=domain,
            effect=say("plans.rollback.unavailable", deployment=deployment_id, domain=domain),
            needs=PlanNeeds(),
            blocked=common.refusal(DeploymentError(unavailable, details=details)),
        )

    method = lifecycle.rollback_methods([record], {deployment_id: None})[deployment_id]
    active = ReleaseManager(app_root(app)).current() if method == "instant" else None
    previous = active.id if active is not None else None

    def run(_frozen_ok: bool, schema_ok: bool) -> engine.Rehearsal[object]:
        return engine.rehearse(
            lambda: lifecycle.rollback_to_deployment(
                domain,
                deployment_id,
                schema_changed_ok=schema_ok,
                logger=common.quiet_logger(),
            ),
            scrub=common.scrub_for(domain),
        )

    confirmed = common.confirming(domain, f"deployment {deployment_id}", run)
    attempt = confirmed.rehearsal

    findings = [say("plans.found.database_stays")]
    if previous is not None:
        findings.append(say("plans.found.serving_now", release=previous))

    blocked: PlanBlock | None = common.busy(domain)
    unknown: list[ServerText] = []
    if blocked is None:
        blocked, stopped = common.stopped(attempt)
        unknown.extend(stopped)
    if method == "instant":
        unknown.append(
            say("plans.unknown.health_older", check=HealthCheck.for_app(app).describe(app.port))
        )
    elif method == "rebuild":
        unknown.append(say("plans.unknown.rebuild", commit=(record.git_commit or "")[:7]))
    else:
        unknown.append(say("plans.unknown.build"))
    if attempt.files or any(not command.probe for command in attempt.commands):
        unknown.append(say("plans.unknown.readback"))
    unknown.extend(common.undone_probes(attempt))

    return common.assemble(
        OPERATION,
        domain,
        _effect(method, record, domain, previous),
        attempt,
        findings=findings,
        unknown=unknown,
        confirmations=confirmed.confirmations,
        blocked=blocked,
    )
