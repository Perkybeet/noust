# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Build a release without serving it, and make it serve later (checklist B-18).

A client of :mod:`noust.deployers.staged`, like ``noust update --build-only`` and ``noust
releases promote``: ``POST /api/apps/{domain}/releases/stage`` queues the build, which leaves a
release ``waiting`` (``GET .../releases`` marks it) and touches nothing that serves, and
``POST .../releases/{release_id}/promote`` queues making it the one that serves, with its hooks
and migrations and behind the health gate, without building it again. Both are jobs: a build
takes minutes, and so can a migration.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core import deploy_guard
from noust.core.store import DeploymentTrigger
from noust.core.teams import scoped_store
from noust.deployers.lifecycle import list_releases
from noust.deployers.recorder import CapturingLogger
from noust.deployers.releases import is_release_id
from noust.deployers.staged import promote_staged, stage_build
from noust.web.api.auth import get_current_session
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, strict_domain
from noust.web.auth import actor_label
from noust.web.jobs import JobContext, JobType, get_job_manager

router = APIRouter(route_class=NoustErrorRoute)


class StageRequest(BaseModel):
    """What to build."""

    commit: str | None = Field(
        default=None, description="Build this commit (full or abbreviated) instead of the branch"
    )
    branch: str | None = Field(
        default=None, description="Build this branch instead of the one the application follows"
    )
    no_cache: bool = Field(default=False, description="Build from scratch")


class PromoteRequest(BaseModel):
    """What to confirm when a built release is made to serve."""

    schema_changed_ok: bool = Field(
        default=False,
        description="Switch although later deployments changed the database's schema",
    )
    frozen_ok: bool = Field(
        default=False,
        description="Switch although deploys are frozen for the application: a person's "
        "decision, recorded in the audit trail",
    )


def stage_release_job(
    domain: str,
    commit: str | None = None,
    branch: str | None = None,
    no_cache: bool = False,
    job_context: JobContext | None = None,
) -> dict[str, object]:
    """
    Build a release and leave it waiting, as a console job.

    Args:
        domain: The application.
        commit: Build this commit.
        branch: Build this branch.
        no_cache: Build from scratch.
        job_context: Injected by the job manager.

    Returns:
        The release that was built.

    Raises:
        NoustError: The build failed; the half-built release was removed.
    """
    logger = CapturingLogger(verbose=False)
    if job_context is not None:
        job_context.set_metadata("domain", domain)
        job_context.update("Building without activating", 10)
        logger.attach_sink(job_context.log)
    built = stage_build(
        domain,
        commit=commit,
        branch=branch,
        trigger=DeploymentTrigger.PANEL.value,
        job_id=job_context.job_id if job_context is not None else None,
        on_step=job_context.log if job_context is not None else None,
        logger=logger,
        no_cache=no_cache,
    )
    if job_context is not None:
        job_context.update("Built, waiting to be promoted", 100)
    return {
        "domain": domain,
        "status": "built",
        "release_id": built.release_id,
        "commit": built.commit,
    }


def promote_release_job(
    domain: str,
    release_id: str,
    schema_changed_ok: bool = False,
    frozen_ok: bool = False,
    job_context: JobContext | None = None,
) -> dict[str, object]:
    """
    Make a release that was built and is waiting the one that serves, as a console job.

    Args:
        domain: The application.
        release_id: The release.
        schema_changed_ok: Switch past a schema change.
        frozen_ok: Switch during a freeze.
        job_context: Injected by the job manager.

    Returns:
        What was done.

    Raises:
        NoustError: A hook failed or the release did not pass the health gate (what served
            before is back by then).
    """
    logger = CapturingLogger(verbose=False)
    if job_context is not None:
        job_context.set_metadata("domain", domain)
        job_context.update("Making the release serve", 10)
        logger.attach_sink(job_context.log)
    promoted = promote_staged(
        domain,
        release_id,
        trigger=DeploymentTrigger.PANEL.value,
        job_id=job_context.job_id if job_context is not None else None,
        on_step=job_context.log if job_context is not None else None,
        logger=logger,
        frozen_ok=frozen_ok,
        schema_changed_ok=schema_changed_ok,
    )
    if job_context is not None:
        job_context.update("Promoted", 100)
    return {
        "domain": domain,
        "status": "promoted",
        "release_id": promoted.release_id,
        "previous_id": promoted.previous_id,
        "deployment_id": promoted.deployment_id,
        "warnings": promoted.warnings,
    }


def _known(domain: str) -> str:
    """
    Args:
        domain: From the path.

    Returns:
        The validated domain of an application the caller sees.

    Raises:
        HTTPException: 404 when nothing is deployed there.
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


@router.post("/{domain}/releases/stage", response_model=JobAcceptedResponse, status_code=202)
def stage_release(
    domain: str,
    body: StageRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> JobAcceptedResponse:
    """
    Queue a build that leaves its release waiting instead of serving it.

    Nothing that serves is touched: ``current`` stays, no hook runs, nothing restarts. The
    release is listed as ``waiting``; ``POST .../releases/{id}/promote`` makes it serve.

    Args:
        domain: The application.
        body: What to build.
        session: The authenticated session.

    Returns:
        The queued job.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known(domain)
    job = get_job_manager().create_job(
        job_type=JobType.UPDATE,
        name=f"Build {validated} without activating",
        description=f"Building the next release of {validated} and leaving it waiting",
        func=stage_release_job,
        kwargs={
            "domain": validated,
            "commit": body.commit,
            "branch": body.branch,
            "no_cache": body.no_cache,
        },
        metadata={"domain": validated, "staged": True},
        actor=actor_label(session),
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Build of {validated} queued; it will wait for a promotion",
        job=job.to_dict(),
    )


@router.post(
    "/{domain}/releases/{release_id}/promote", response_model=JobAcceptedResponse, status_code=202
)
def promote_release(
    domain: str,
    release_id: str,
    body: PromoteRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> JobAcceptedResponse:
    """
    Queue making a release that was built and is waiting the one that serves.

    Its hooks and migrations run against the application's database and it is activated
    behind the health gate, which puts back what served when it does not answer. Held by a
    freeze of deploys like an update.

    Args:
        domain: The application.
        release_id: The release.
        body: What the operator confirms.
        session: The authenticated session.

    Returns:
        The queued job.

    Raises:
        HTTPException: 400 for something that is not a release id, 404 for an unknown
            application or a release that does not wait.
        DeploysFrozenError: 409 while a freeze holds it and ``frozen_ok`` was not given.
    """
    validated = _known(domain)
    if not is_release_id(release_id):
        raise HTTPException(status_code=400, detail=f"Not a release id: {release_id!r}")
    if not any(info.id == release_id and info.waiting for info in list_releases(validated)):
        raise HTTPException(
            status_code=404,
            detail=f"Release {release_id} of {validated} was not built to wait for a promotion",
        )
    deploy_guard.require_open(validated, override=body.frozen_ok, record=False)
    job = get_job_manager().create_job(
        job_type=JobType.UPDATE,
        name=f"Promote release {release_id} of {validated}",
        description=f"Making release {release_id} of {validated} serve",
        func=promote_release_job,
        kwargs={
            "domain": validated,
            "release_id": release_id,
            "schema_changed_ok": body.schema_changed_ok,
            "frozen_ok": body.frozen_ok,
        },
        metadata={"domain": validated, "release": release_id},
        actor=actor_label(session),
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Promotion of release {release_id} queued",
        job=job.to_dict(),
    )
