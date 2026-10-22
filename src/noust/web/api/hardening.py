# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How hardened an application's unit is (3.4): see it, or change it behind the health gate.

A client of :mod:`noust.deployers.hardening`, like ``noust app hardening``:
``GET`` says the profile, the directories the unit may write to, its directives
and what ``systemd-analyze security`` scores it; ``PUT`` queues the change as a
job, since it restarts the application behind the same health gate as a deploy
and puts the previous sandbox back when it does not answer. It changes what an
application's process can do, so it needs sudo mode.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.exceptions import ValidationError
from noust.core.teams import scoped_store
from noust.deployers import hardening as hardening_engine
from noust.deployers.recorder import CapturingLogger
from noust.managers import unit_hardening
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


class HardeningResponse(BaseModel):
    """How hardened an application's unit is."""

    domain: str
    profile: str = Field(description="off, default or strict")
    configured: bool = Field(
        description="Whether the application has a profile at all; one from before 3.4 has not"
    )
    eligible: bool = Field(description="Whether its unit can be hardened")
    reason: str | None = Field(default=None, description="Why it cannot, when it cannot")
    writable_paths: list[str] = Field(
        default_factory=list, description="Extra directories its unit may write to"
    )
    directives: list[str] = Field(
        default_factory=list, description="The sandbox directives its unit has"
    )
    exposure: float | None = Field(
        default=None, description="systemd-analyze security's score, 0 (nothing exposed) to 10"
    )
    exposure_label: str | None = Field(default=None, description="systemd's word for the score")
    unit: str | None = Field(default=None, description="The unit scored")


class HardeningRequest(BaseModel):
    """The sandbox an application's unit must run in."""

    profile: str = Field(
        ..., description="off (the unit as before 3.4), default, or strict (adds privileged calls)"
    )
    writable_paths: list[str] | None = Field(
        default=None,
        description="Extra directories the unit may write to, beyond the application's own "
        "tree. Omitted: keep what it has",
    )
    restart: bool = Field(
        default=True,
        description="Restart it now, behind the health gate, so it runs in the new sandbox",
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


@router.get("/{domain}/hardening", response_model=HardeningResponse)
def get_hardening(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> HardeningResponse:
    """
    Show how hardened an application's unit is. Changes nothing.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The profile, the directories, the directives and the score.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    return HardeningResponse(**hardening_engine.status(_known(domain)).to_dict())


def hardening_job(
    domain: str,
    profile: str,
    writable_paths: list[str] | None,
    restart: bool,
    actor: str,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Change an application's sandbox, as a background job.

    Args:
        domain: Domain of the application.
        profile: The profile.
        writable_paths: The extra directories, or None to keep them.
        restart: Restart behind the health gate.
        actor: Who asked, for the audit trail.
        job_context: Injected by the job manager.

    Returns:
        What was done.

    Raises:
        ValidationError: A value is not one a unit can have.
        DeploymentError: It did not answer in the new sandbox; the old one is back.
        AppBusyError: Another operation is running on the application.
    """
    logger = CapturingLogger(verbose=False)
    if job_context is not None:
        job_context.set_metadata("domain", domain)
        job_context.update("Changing the sandbox of the application's unit", 10)
        logger.attach_sink(job_context.log)
    change = hardening_engine.set_hardening(
        domain,
        profile,
        writable_paths=writable_paths,
        restart=restart,
        actor=actor,
        logger=logger,
    )
    if job_context is not None:
        job_context.update("Done", 100)
    return {
        "domain": change.domain,
        "profile": change.profile,
        "writable_paths": list(change.writable_paths),
        "units": list(change.units),
        "restarted": change.restarted,
        "exposure_before": change.exposure_before,
        "exposure_after": change.exposure_after,
    }


@router.put("/{domain}/hardening", response_model=JobAcceptedResponse, status_code=202)
def put_hardening(
    domain: str,
    body: HardeningRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> JobAcceptedResponse:
    """
    Queue the change of an application's sandbox.

    It is restarted behind its health gate; when it does not answer, the
    previous units and record are put back and it is restarted as it was.

    Args:
        domain: Domain of the application.
        body: The profile and the extra directories.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: A profile or a path a unit cannot have, or an
            application that cannot be hardened (400), before anything is queued.
    """
    validated = _known(domain)
    app = scoped_store().get_app(validated)
    if app is not None:
        reason = unit_hardening.ineligible(app)
        if reason is not None:
            raise ValidationError(
                f"{validated} cannot be hardened: {reason}",
                details="Nothing was changed.",
                field="domain",
            )
    if body.writable_paths is not None:
        unit_hardening.check_writable_paths(body.writable_paths)
    actor = actor_label(session)
    job = get_job_manager().create_job(
        job_type=JobType.HARDENING,
        name=f"Sandbox for {validated}",
        description=f"Running {validated} with the {body.profile} sandbox",
        func=hardening_job,
        kwargs={
            "domain": validated,
            "profile": body.profile,
            "writable_paths": body.writable_paths,
            "restart": body.restart,
            "actor": actor,
        },
        metadata={"domain": validated},
        actor=actor,
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Changing the sandbox of {validated}",
        job=job.to_dict(),
    )
