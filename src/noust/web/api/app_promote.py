# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Promoting a release to the same application in another environment.

A client of :mod:`noust.deployers.promote`, like ``noust app promote``. The plan
is made before the job is queued, so a refusal (another project, another type,
a release that does not exist) is a 400 the console can show beside the form,
and what the job does is the same function the terminal runs. The target is
checked as the source is: an application the caller does not see answers as one
that does not exist.
"""

from __future__ import annotations

import dataclasses
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from noust.core.audit import record as audit_record
from noust.core.store import get_store
from noust.core.teams import TeamStore, current_scope, scoped_store
from noust.deployers.app_clone import (
    ClonePlan,
    apply_clone,
    deploy_through_job,
    plan_clone,
)
from noust.deployers.app_export import report_summary
from noust.deployers.helpers.layout import app_root
from noust.deployers.promote import plan_promotion, promotion_targets
from noust.deployers.recorder import CapturingLogger
from noust.deployers.releases import ReleaseManager
from noust.validators.port import find_available_port
from noust.web.api.apps import DEFAULT_PORT, _require_local_source_privilege
from noust.web.api.auth import get_current_session
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated, strict_domain
from noust.web.api.scope import resolve_target_for
from noust.web.auth import actor_label
from noust.web.jobs import JobContext, JobType, get_job_manager, promote_app_job
from noust.web.permissions import Permission
from noust.web.permissions.principal import actor_of

router = APIRouter(route_class=NoustErrorRoute)


class PromotionTargetOut(BaseModel):
    """
    An application a release can be promoted to.

    Attributes:
        domain: Its domain.
        environment: The slug of the environment it is in.
        release: The release it runs now, when it has one.
        commit: The commit that release was built from.
    """

    domain: str
    environment: str | None = None
    release: str | None = None
    commit: str | None = None


class PromotionResponse(BaseModel):
    """
    What can be promoted from an application, and to where.

    Attributes:
        domain: The application.
        release: The release that serves, which is what is promoted by default.
        commit: The commit it was built from.
        targets: The applications it can be promoted to.
    """

    domain: str
    release: str | None = None
    commit: str | None = None
    targets: list[PromotionTargetOut]


class PromoteRequest(BaseModel):
    """
    Promote a release to another application.

    Attributes:
        target: The application it goes to.
        release: The release; the one that serves when left out.
        schema_changed_ok: Confirm going back past a deployment of the target
            that changed its database's schema.
    """

    target: str
    release: str | None = None
    schema_changed_ok: bool = False


@router.get("/{domain}/promotion", response_model=PromotionResponse)
def get_promotion(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> PromotionResponse:
    """
    Read where the release of an application can be promoted.

    Args:
        domain: The application.
        session: The authenticated session.

    Returns:
        Its serving release and the applications that can receive one.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = strict_domain(domain)
    app = scoped_store().get_app(validated)
    if app is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    labels = TeamStore().owner_labels()
    visible = {a.domain for a in scoped_store().list_apps()}
    targets = []
    for other in promotion_targets(validated):
        if other.domain not in visible:
            continue
        active = ReleaseManager(app_root(other)).current()
        targets.append(
            PromotionTargetOut(
                domain=other.domain,
                environment=labels.of(other.project_id, other.environment_id)[2],
                release=active.id if active else None,
                commit=active.commit if active else None,
            )
        )
    current = ReleaseManager(app_root(app)).current() if app.layout == "releases" else None
    return PromotionResponse(
        domain=validated,
        release=current.id if current else None,
        commit=current.commit if current else None,
        targets=targets,
    )


@router.post("/{domain}/promote", response_model=JobAcceptedResponse, status_code=202)
def promote_release(
    domain: str,
    body: PromoteRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> JobAcceptedResponse:
    """
    Queue the promotion of a release to another application.

    Args:
        domain: The application the release is of.
        body: Where it goes, and which release.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        HTTPException: 404 when either application is unknown.
        ValidationError: The promotion is refused (400), with the reason.
    """
    source = strict_domain(domain)
    target = strict_domain(body.target)
    scoped = scoped_store()
    for name in (source, target):
        if scoped.get_app(name) is None:
            raise HTTPException(status_code=404, detail=f"Application not found: {name}")
    plan = plan_promotion(source, target, body.release)
    job = get_job_manager().create_job(
        job_type=JobType.UPDATE,
        name=f"Promote {source} to {target}",
        description=f"Promoting release {plan.release.id} of {source} to {target}",
        func=promote_app_job,
        kwargs={
            "domain": source,
            "target": target,
            "release": plan.release.id,
            "schema_changed_ok": body.schema_changed_ok,
        },
        metadata={"domain": target, "source": source, "release": plan.release.id},
        actor=actor_label(session),
    )
    audit_record(
        "apps.promote",
        actor=actor_of(session),
        target=f"app:{target}",
        details={"from": source, "release": plan.release.id},
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Promotion of release {plan.release.id} to {target} queued",
        job=job.to_dict(),
    )


class CloneRequest(BaseModel):
    """
    Clone an application.

    Attributes:
        domain: The clone's domain.
        team: The team it goes to; the original's by default.
        project: The project it goes to; the original's by default.
        environment: The environment it goes to; the original's by default.
        database: ``empty`` (a database of its own) or ``share`` (the original's);
            required when the application has a database.
        source: Deploy the clone from this source instead of the original's.
    """

    domain: str
    team: str | None = None
    project: str | None = None
    environment: str | None = None
    database: str | None = None
    source: str | None = None


def clone_app_job(
    plan: ClonePlan, actor: str = "api", job_context: JobContext | None = None
) -> dict[str, object]:
    """
    Carry out a clone as a background job.

    Args:
        plan: What :func:`~noust.deployers.app_clone.plan_clone` decided in the request.
        actor: Who asked, for the records.
        job_context: Injected by the job manager.

    Returns:
        What was applied and what was not.

    Raises:
        DeploymentError: The deploy failed; nothing else was applied.
    """
    logger = CapturingLogger(verbose=False)
    if job_context is not None:
        job_context.set_metadata("domain", plan.plan.domain)
        logger.attach_sink(job_context.log)
    logger.info(f"Cloning {plan.source.domain} as {plan.plan.domain}")
    for line in (*plan.plan.steps, *plan.extras):
        logger.substep(line)
    report = apply_clone(plan, deploy=deploy_through_job(job_context), logger=logger, actor=actor)
    if job_context is not None:
        job_context.update("Clone complete", 100)
    return report_summary(report)


@router.post("/{domain}/clone", response_model=JobAcceptedResponse, status_code=202)
def clone_app(
    domain: str,
    body: CloneRequest,
    request: Request,
    session: Annotated[dict, Depends(require_elevated)],
) -> JobAcceptedResponse:
    """
    Queue the clone of an application: the same application on another domain.

    Everything the export carries is copied (secrets included: it stays on this
    server) except the names the original answers on, plus its tags, build
    block, hooks and how its previews are made. An application with a database
    must say what its clone does with it. It goes to the original's project and
    environment unless told otherwise. The cron jobs and the backup schedule are
    the server's: they are copied only for a caller that sees the whole server.

    Args:
        domain: The application to clone.
        body: The clone's domain, where it goes, and what it does with the database.
        request: The request, for the audit record of a refusal.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        HTTPException: 404 when the application is unknown, 409 when the domain is
            taken, 503 when no port is free.
        ValidationError: The clone is refused (400), with the reason.
    """
    source = strict_domain(domain)
    new = strict_domain(body.domain)
    scoped = scoped_store()
    app = scoped.get_app(source)
    if app is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {source}")
    if scoped.domain_is_taken(new):
        raise HTTPException(status_code=409, detail=f"Application already exists: {new}")
    if body.source:
        _require_local_source_privilege(request, session, body.source)
    # Where it goes: the original's project and environment, or what is named.
    named = body.team or body.project or body.environment
    if named:
        target = resolve_target_for(
            request,
            session,
            team=body.team,
            project=body.project,
            environment=body.environment,
            permission=Permission.APPS_MANAGE,
        )
        ownership = (target.project_id, target.environment_id)
    else:
        ownership = (app.project_id or 0, app.environment_id or 0)
    plan = plan_clone(
        source,
        new,
        database=body.database,
        source=body.source,
        server_parts=not current_scope().restricted,
    )
    if plan.plan.create.port is None:
        port = find_available_port(
            preferred=DEFAULT_PORT, exclude=get_store().ports_owned_by_apps()
        )
        if port is None:
            raise HTTPException(status_code=503, detail="No available port found")
        plan.plan.create = dataclasses.replace(plan.plan.create, port=port)
    job = get_job_manager().create_job(
        job_type=JobType.DEPLOY,
        name=f"Clone {source} as {new}",
        description=f"Cloning {source} as {new}",
        func=clone_app_job,
        kwargs={"plan": plan, "actor": actor_label(session)},
        metadata={
            "domain": new,
            "source": source,
            "app_type": plan.plan.create.app_type,
            "clone": True,
        },
        actor=actor_label(session),
        ownership=ownership if all(ownership) else None,
    )
    audit_record(
        "apps.clone",
        actor=actor_of(session),
        target=f"app:{new}",
        details={"from": source, "database": plan.database},
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Clone of {source} queued as {new}",
        job=job.to_dict(),
    )
