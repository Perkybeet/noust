# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The system account an application runs as (3.2): see it, or give it its own.

A client of :mod:`noust.managers.app_identity`, like ``noust app identity``:
``GET`` says which account the application runs as and the one it would get;
``POST .../migrate`` queues the move to its own account as a job, since it
restarts the application behind its health gate. Changing who a process runs
as and who owns its files is root's business, so the move is root-equivalent
and needs sudo mode; the module records it in the audit trail.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.store import get_store
from noust.deployers.recorder import CapturingLogger
from noust.managers import app_identity
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


class IdentityResponse(BaseModel):
    """The account an application runs as."""

    domain: str
    account: str = Field(description="The system account its processes run as")
    group: str = Field(description="Their group")
    own: bool = Field(description="Whether the account is the application's own")
    eligible: bool = Field(description="Whether it can have an account of its own")
    reason: str | None = Field(
        default=None, description="Why it cannot have its own account, when it cannot"
    )
    proposed: str | None = Field(
        default=None, description="The account a migration would give it, when it can move"
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
    if get_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


@router.get("/{domain}/identity", response_model=IdentityResponse)
def get_identity(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> IdentityResponse:
    """
    Show the account an application runs as. Changes nothing.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The account, whether it is its own, and what a migration would give it.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    return IdentityResponse(**app_identity.status(_known(domain)))


def identity_migrate_job(
    domain: str, actor: str, job_context: JobContext | None = None
) -> dict[str, Any]:
    """
    Move an application to its own account, as a background job.

    Args:
        domain: Domain of the application.
        actor: Who asked, for the audit trail.
        job_context: Injected by the job manager.

    Returns:
        The account, the one it ran as, and the units or pool rewritten.

    Raises:
        ValidationError: It cannot or need not move.
        DeploymentError: It did not answer with its own account; everything
            is back as it was.
        AppBusyError: Another operation is running on the application.
    """
    logger = CapturingLogger(verbose=False)
    if job_context is not None:
        job_context.set_metadata("domain", domain)
        job_context.update("Moving the application to its own account", 10)
        logger.attach_sink(job_context.log)
    done = app_identity.migrate(domain, actor=actor, logger=logger)
    if job_context is not None:
        job_context.update("Done", 100)
    return done.to_dict()


@router.post("/{domain}/identity/migrate", response_model=JobAcceptedResponse, status_code=202)
def post_identity_migrate(
    domain: str, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Queue the move of an application to its own system account.

    It is restarted behind its health gate; when it does not answer, the
    files' owners, its unit or pool and the records are put back exactly.

    Args:
        domain: Domain of the application.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: It cannot or need not move (400), before anything
            is queued.
    """
    validated = _known(domain)
    app = get_store().get_app(validated)
    if app is not None:
        app_identity.refuse_migration(app)
    actor = actor_label(session)
    job = get_job_manager().create_job(
        job_type=JobType.IDENTITY_MIGRATE,
        name=f"Own account for {validated}",
        description=f"Moving {validated} to its own system account",
        func=identity_migrate_job,
        kwargs={"domain": validated, "actor": actor},
        metadata={"domain": validated},
        actor=actor,
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Moving {validated} to its own account",
        job=job.to_dict(),
    )
