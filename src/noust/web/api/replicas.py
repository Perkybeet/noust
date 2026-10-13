# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How many copies of its web process an application runs (3.4, checklist B-68).

A client of :mod:`noust.managers.app_replicas`, like ``noust app replicas``: ``GET``
says how the application is scaled and how each copy is, ``PUT`` queues the change as
a job, since new copies are started and asked the application's own health check
before nginx shares the traffic with them. It changes what runs and what answers, so
it needs sudo mode.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.app_replica_store import MAX_COPIES, check_copies
from noust.core.exceptions import ValidationError
from noust.core.logger import Logger
from noust.core.teams import scoped_store
from noust.deployers.recorder import CapturingLogger
from noust.managers.app_replicas import ReplicaManager, eligibility_problem
from noust.web.api.auth import get_current_session
from noust.web.api.deps import (
    JobAcceptedResponse,
    NoustErrorRoute,
    require_elevated,
    strict_domain,
)
from noust.web.auth import actor_label
from noust.web.jobs import JobContext, JobType, get_job_manager

router = APIRouter(route_class=NoustErrorRoute)


class ReplicaCopyOut(BaseModel):
    """One copy of an application's web process."""

    unit: str
    port: int
    state: str = Field(description="systemd's ActiveState of the copy")
    own: bool = Field(description="The application's own unit, not an extra copy")
    serving: bool = Field(description="Whether nginx sends it traffic")


class ReplicasResponse(BaseModel):
    """How an application is scaled."""

    domain: str
    copies: int = Field(description="Copies that run, the application's own unit included")
    maximum: int = Field(description="The most copies there can be")
    ports: list[int] = Field(description="Ports reserved for the extra copies")
    units: list[ReplicaCopyOut]
    eligible: bool = Field(description="Whether it can be scaled out")
    reason: str | None = Field(default=None, description="Why it cannot, when it cannot")


class ReplicasRequest(BaseModel):
    """How many copies the application must run."""

    copies: int = Field(
        ...,
        ge=1,
        le=MAX_COPIES,
        description="The copies in all, the application's own unit included; 1 runs it alone",
    )


def _known(domain: str) -> str:
    """
    Validate a domain and refuse one nothing is deployed at.

    Args:
        domain: The domain as the client sent it.

    Returns:
        The validated domain.

    Raises:
        HTTPException: 404.
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


@router.get("/{domain}/replicas", response_model=ReplicasResponse)
def get_replicas(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> ReplicasResponse:
    """
    Show how an application is scaled. Changes nothing.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        Its copies, the ports reserved and whether it can be scaled out.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    return ReplicasResponse(**ReplicaManager(log=Logger()).status(_known(domain)).to_dict())


def replicas_job(
    domain: str, copies: int, actor: str, job_context: JobContext | None = None
) -> dict[str, Any]:
    """
    Scale an application, as a background job.

    Args:
        domain: Domain of the application.
        copies: The copies it must run.
        actor: Who asked, for the audit trail.
        job_context: Injected by the job manager.

    Returns:
        What was done.

    Raises:
        ValidationError: The count is out of range or the application cannot be scaled.
        DeploymentError: A new copy did not answer; nothing changed.
        AppBusyError: Another operation is running on the application.
    """
    logger = CapturingLogger(verbose=False)
    if job_context is not None:
        job_context.set_metadata("domain", domain)
        job_context.update("Starting the copies and asking each one if it answers", 10)
        logger.attach_sink(job_context.log)
    change = ReplicaManager(log=logger).set_copies(domain, copies, actor=actor)
    if job_context is not None:
        job_context.update("Done", 100)
    return {
        "domain": change.domain,
        "copies": change.copies,
        "before": change.before,
        "ports": list(change.ports),
        "started": list(change.started),
        "stopped": list(change.stopped),
    }


@router.put("/{domain}/replicas", response_model=JobAcceptedResponse, status_code=202)
def put_replicas(
    domain: str,
    body: ReplicasRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> JobAcceptedResponse:
    """
    Queue running an application as this many copies of its web process.

    Each new copy is started and asked the application's health check on its own
    port before nginx is told about it; one that does not answer leaves things as
    they were.

    Args:
        domain: Domain of the application.
        body: The copies.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: The application cannot be scaled out, or the count is out
            of range (400), before anything is queued.
    """
    validated = _known(domain)
    check_copies(body.copies)
    app = scoped_store().get_app(validated)
    if body.copies > 1 and app is not None:
        problem = eligibility_problem(app)
        if problem is not None:
            raise ValidationError(
                f"{validated} cannot run as several copies: {problem}",
                details="Nothing was changed.",
                field="copies",
            )
    actor = actor_label(session)
    job = get_job_manager().create_job(
        job_type=JobType.REPLICAS,
        name=f"Scale {validated}",
        description=f"Running {validated} as {body.copies} cop{'y' if body.copies == 1 else 'ies'}",
        func=replicas_job,
        kwargs={"domain": validated, "copies": body.copies, "actor": actor},
        metadata={"domain": validated},
        actor=actor,
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Scaling {validated} to {body.copies}",
        job=job.to_dict(),
    )
