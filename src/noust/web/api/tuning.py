# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How an application's unit is tuned beyond its hard limits (4.0): see it, or change it behind the gate.

A client of :mod:`noust.deployers.unit_tuning`, like ``noust app tuning``: ``GET`` says the options,
the unit lines they become and what can be asked for (the capabilities, the GPU presets, the CPUs
of the machine); ``PUT`` queues the change as a job, since it restarts the application behind the
same health gate as a deploy and puts the previous unit back when it does not answer. It changes
what an application's process may do and take, so it needs sudo mode.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.exceptions import ValidationError
from noust.core.teams import scoped_store
from noust.deployers import unit_tuning as tuning_engine
from noust.deployers.recorder import CapturingLogger
from noust.managers import unit_hardening, unit_tuning
from noust.web.api.auth import get_current_session
from noust.web.api.deps import (
    JobAcceptedResponse,
    NoustErrorRoute,
    require_elevated,
    strict_domain,
)
from noust.web.auth import actor_label
from noust.web.jobs import JobContext, JobType, get_job_manager
from noust.web.pydantic_compat import dump_model

router = APIRouter(route_class=NoustErrorRoute)


class UnitDevice(BaseModel):
    """A device node or group the process may open."""

    path: str = Field(description="/dev/nvidia0, or a device group such as char-nvidia")
    mode: str = Field(default="rw", description="r, w or rw")


class UnitTuningOptions(BaseModel):
    """The options an application's unit can be tuned with; one that is absent is not set."""

    memory_high_mb: int | None = Field(
        default=None, description="Soft memory limit (MemoryHigh): throttled above it, not killed"
    )
    memory_swap_max_mb: int | None = Field(
        default=None, description="Swap the unit may use (MemorySwapMax); 0 forbids it"
    )
    cpu_weight: int | None = Field(
        default=None, description="Share of the CPU when it is contended, 1 to 10000 (default 100)"
    )
    allowed_cpus: str | None = Field(
        default=None, description="CPUs the unit is pinned to, such as 0-3 or 0,2"
    )
    io_weight: int | None = Field(
        default=None, description="Share of the disk when it is contended, 1 to 10000"
    )
    limit_nofile: int | None = Field(default=None, description="Open files (ulimit -n)")
    limit_nproc: int | None = Field(
        default=None, description="Processes of the account (ulimit -u)"
    )
    limit_memlock_mb: int | str | None = Field(
        default=None, description="Locked memory in MB (ulimit -l), or 'infinity'"
    )
    core_dumps: bool | None = Field(default=None, description="Allow core dumps (ulimit -c)")
    capabilities: list[str] = Field(
        default_factory=list, description="Capabilities the process keeps, without the CAP_ prefix"
    )
    devices: list[UnitDevice] = Field(
        default_factory=list, description="Devices the process may open (a GPU, for one)"
    )


class UnitTuningResponse(BaseModel):
    """How an application's unit is tuned."""

    domain: str
    configured: bool = Field(description="Whether anything is set")
    eligible: bool = Field(description="Whether its unit can be tuned")
    reason: str | None = Field(default=None, description="Why it cannot, when it cannot")
    options: UnitTuningOptions = Field(default_factory=UnitTuningOptions)
    directives: list[str] = Field(
        default_factory=list, description="The unit lines the options become"
    )
    capabilities: dict[str, str] = Field(
        default_factory=dict, description="Every capability that can be given, and what it is for"
    )
    gpu_presets: dict[str, list[str]] = Field(
        default_factory=dict, description="Each GPU preset and the device groups it allows"
    )
    cpus: int = Field(default=1, description="CPUs this machine has")


class UnitTuningRequest(BaseModel):
    """The tuning an application's unit must have: all of it, an empty one removes it."""

    options: UnitTuningOptions
    restart: bool = Field(
        default=True,
        description="Restart it now, behind the health gate, so it runs under the new options",
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


@router.get("/{domain}/tuning", response_model=UnitTuningResponse)
def get_tuning(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> UnitTuningResponse:
    """
    Show how an application's unit is tuned. Changes nothing.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The options, the lines they become and what can be asked for.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    return UnitTuningResponse(**tuning_engine.status(_known(domain)).to_dict())


def tuning_job(
    domain: str,
    options: dict[str, Any],
    restart: bool,
    actor: str,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Change an application's tuning, as a background job.

    Args:
        domain: Domain of the application.
        options: The complete options.
        restart: Restart behind the health gate.
        actor: Who asked, for the audit trail.
        job_context: Injected by the job manager.

    Returns:
        What was done.

    Raises:
        ValidationError: A value is not one a unit can have.
        DeploymentError: It did not answer under the new options; the old ones are back.
        AppBusyError: Another operation is running on the application.
    """
    logger = CapturingLogger(verbose=False)
    if job_context is not None:
        job_context.set_metadata("domain", domain)
        job_context.update("Tuning the application's unit", 10)
        logger.attach_sink(job_context.log)
    change = tuning_engine.set_tuning(domain, options, restart=restart, actor=actor, logger=logger)
    if job_context is not None:
        job_context.update("Done", 100)
    return change.to_dict()


@router.put("/{domain}/tuning", response_model=JobAcceptedResponse, status_code=202)
def put_tuning(
    domain: str,
    body: UnitTuningRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> JobAcceptedResponse:
    """
    Queue the change of an application's tuning.

    It is restarted behind its health gate; when it does not answer, the previous units and record
    are put back and it is restarted as it was.

    Args:
        domain: Domain of the application.
        body: The complete options, and whether to restart now.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: An option a unit cannot have, or an application that cannot be tuned
            (400), before anything is queued.
    """
    validated = _known(domain)
    app = scoped_store().get_app(validated)
    options = {
        name: value
        for name, value in dump_model(body.options).items()
        if value is not None and value != []
    }
    if app is not None:
        reason = unit_hardening.ineligible(app)
        if reason is not None:
            raise ValidationError(
                f"{validated} cannot be tuned: {reason}",
                details="Nothing was changed.",
                field="domain",
            )
        unit_tuning.check_against_limits(unit_tuning.validate(options), app)
    actor = actor_label(session)
    job = get_job_manager().create_job(
        job_type=JobType.TUNING,
        name=f"Tuning for {validated}",
        description=f"Tuning the unit of {validated}",
        func=tuning_job,
        kwargs={
            "domain": validated,
            "options": options,
            "restart": body.restart,
            "actor": actor,
        },
        metadata={"domain": validated},
        actor=actor,
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Tuning the unit of {validated}",
        job=job.to_dict(),
    )
