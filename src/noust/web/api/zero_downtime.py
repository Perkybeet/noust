# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Blue/green activation of an application: turning it on and off (2.2).

A client of :mod:`noust.deployers.bluegreen`, like ``noust app zero-downtime``:
``GET`` reads :func:`~noust.deployers.bluegreen.zero_downtime_status`, ``PUT``
queues :func:`~noust.deployers.bluegreen.set_zero_downtime` as a job, because
switching the mode starts an instance, waits for its health check and drains
the old one, which is longer than a request should hold a connection.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.store import MAX_DRAIN_SECONDS, get_store
from noust.deployers.bluegreen import (
    ZeroDowntimeStatus,
    check_eligible,
    set_zero_downtime,
    zero_downtime_status,
)
from noust.deployers.recorder import CapturingLogger
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


class ZeroDowntimeInstance(BaseModel):
    """One instance of an application in zero-downtime mode."""

    color: str = Field(description="blue or green")
    unit: str = Field(description="Its systemd unit, without .service")
    port: int = Field(description="The port it listens on")
    release: str | None = Field(default=None, description="The release it runs")
    serving: bool = Field(description="Whether nginx sends it the traffic")
    state: str = Field(description="systemd's ActiveState, or unknown")


class ZeroDowntimeResponse(BaseModel):
    """Whether an application activates without a cut, and how."""

    domain: str
    enabled: bool
    active_color: str | None = Field(default=None, description="The instance that serves")
    drain_seconds: int = Field(
        description="Seconds the old instance keeps running after traffic moved"
    )
    instances: list[ZeroDowntimeInstance] = Field(default_factory=list)
    upstream_port: int | None = Field(
        default=None, description="The port nginx's upstream names, when the mode is on"
    )
    eligible: bool = Field(description="Whether the mode can be turned on")
    reason: str | None = Field(default=None, description="Why it cannot, when it cannot")
    hint: str | None = Field(default=None, description="What to do about it")


class ZeroDowntimeRequest(BaseModel):
    """The mode an application must be in."""

    enabled: bool = Field(description="True for blue/green activation, false for a restart")
    drain_seconds: int | None = Field(
        default=None,
        ge=0,
        le=MAX_DRAIN_SECONDS,
        description=f"Seconds the old instance keeps running after a switch, 0 to "
        f"{MAX_DRAIN_SECONDS}. Omitted: what the application has, or 10",
    )


def _response(status: ZeroDowntimeStatus) -> ZeroDowntimeResponse:
    """
    Translate the mode to its API model.

    Args:
        status: The mode.

    Returns:
        The model.
    """
    return ZeroDowntimeResponse(
        domain=status.domain,
        enabled=status.enabled,
        active_color=status.active_color,
        drain_seconds=status.drain_seconds,
        instances=[
            ZeroDowntimeInstance(
                color=instance.color,
                unit=instance.unit,
                port=instance.port,
                release=instance.release,
                serving=instance.serving,
                state=instance.state,
            )
            for instance in status.instances
        ],
        upstream_port=status.upstream_port,
        eligible=status.eligible,
        reason=status.reason,
        hint=status.hint,
    )


def _known(domain: str) -> None:
    """
    Refuse a domain nothing is deployed at.

    Args:
        domain: A validated domain.

    Raises:
        HTTPException: 404.
    """
    if get_store().get_app(domain) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {domain}")


@router.get("/{domain}/zero-downtime", response_model=ZeroDowntimeResponse)
def get_zero_downtime(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> ZeroDowntimeResponse:
    """
    Show whether an application activates without a cut. Changes nothing.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The mode, both instances when it is on, and whether it could be
        turned on when it is off.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = strict_domain(domain)
    _known(validated)
    return _response(zero_downtime_status(validated))


def zero_downtime_job(
    domain: str,
    enabled: bool,
    drain_seconds: int | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Turn an application's zero-downtime mode on or off, as a background job.

    Args:
        domain: Domain of the application.
        enabled: The mode wanted.
        drain_seconds: The drain, or None to keep the application's.
        job_context: Injected by the job manager.

    Returns:
        What was done.

    Raises:
        ValidationError: The application cannot use the mode.
        DeploymentError: A step failed; the application serves as before.
        AppBusyError: Another operation is running on the application.
    """
    logger = CapturingLogger(verbose=False)
    if job_context is not None:
        job_context.set_metadata("domain", domain)
        job_context.update("Turning zero downtime " + ("on" if enabled else "off"), 10)
        logger.attach_sink(job_context.log)
    change = set_zero_downtime(domain, enabled, drain_seconds=drain_seconds, logger=logger)
    if job_context is not None:
        job_context.update("Done", 100)
    return {
        "domain": change.domain,
        "enabled": change.enabled,
        "active_color": change.active_color,
        "drain_seconds": change.drain_seconds,
        "changed": change.changed,
    }


@router.put("/{domain}/zero-downtime", response_model=JobAcceptedResponse, status_code=202)
def put_zero_downtime(
    domain: str,
    body: ZeroDowntimeRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> JobAcceptedResponse:
    """
    Queue turning an application's blue/green activation on or off.

    It rewrites units and the site and starts and stops processes, so it
    needs sudo mode. An application that cannot use the mode is refused
    here, before a job is queued; the job checks again when it runs. With
    the mode already as asked, the job only records a new drain.

    Args:
        domain: Domain of the application.
        body: The mode, and optionally the drain.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: It cannot run as two instances (400, with why).
    """
    validated = strict_domain(domain)
    _known(validated)
    app = get_store().get_app(validated)
    if app is not None and body.enabled and not app.zero_downtime:
        # Not the site of a Compose stack: turning its relay on writes the
        # servers files first, so the line an operator's site is asked to add
        # loads, and the job refuses a site that lacks it after that.
        check_eligible(app, site=False)
    verb = "on" if body.enabled else "off"
    job = get_job_manager().create_job(
        job_type=JobType.ZERO_DOWNTIME,
        name=f"Zero downtime {verb} for {validated}",
        description=f"Turning blue/green activation {verb} for {validated}",
        func=zero_downtime_job,
        kwargs={
            "domain": validated,
            "enabled": body.enabled,
            "drain_seconds": body.drain_seconds,
        },
        metadata={"domain": validated},
        actor=actor_label(session),
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Zero downtime {verb} for {validated} queued",
        job=job.to_dict(),
    )
