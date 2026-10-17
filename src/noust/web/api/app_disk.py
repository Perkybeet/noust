# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application takes of the disk, and the limit it is held to (checklist B-63).

A client of :mod:`noust.managers.app_disk`, like ``noust app disk``. ``GET`` says the limit, the
last measurement and whether the filesystem could hold a kernel quota, and measures nothing.
``POST .../measure`` queues the measurement as a job, since a release tree takes a while to walk
and nothing measures a tree in a request. ``PUT`` sets or removes the limit a deploy is held to.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.teams import scoped_store
from noust.managers import app_disk
from noust.web.api.auth import get_current_session
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, strict_domain
from noust.web.auth import actor_label
from noust.web.jobs import JobContext, JobType, get_job_manager

router = APIRouter(route_class=NoustErrorRoute)


class DiskPartModel(BaseModel):
    """One place an application takes disk."""

    name: str = Field(description="tree, current, releases, shared or repo")
    bytes: int
    path: str | None
    reclaimable_bytes: int | None = Field(
        description="What a prune of the releases it keeps would give back"
    )


class DiskUsageModel(BaseModel):
    """What an application took of the disk when it was measured."""

    parts: list[DiskPartModel]
    total_bytes: int = Field(description="What its directory takes: the figure the limit holds")
    backups_bytes: int = Field(description="Its backups, which live elsewhere and do not count")
    measured_at: str
    errors: list[str] = Field(description="Paths that could not be measured in time")


class DiskVerdictModel(BaseModel):
    """How an application stands against its limit."""

    limit_bytes: int
    used_bytes: int
    percent: float
    state: Literal["ok", "near", "over"]


class DiskFilesystemModel(BaseModel):
    """Whether the filesystem it lives on could hold a kernel project quota."""

    mount: str | None
    fstype: str | None
    options: list[str]
    project_quota: Literal["enabled", "available", "unsupported", "unknown"]


class DiskResponse(BaseModel):
    """An application's disk: the limit, the last measurement and what the filesystem could hold."""

    domain: str
    soft_mb: int | None = Field(description="The limit a new release is held to, in MB")
    usage: DiskUsageModel | None = Field(description="The last measurement; none before the first")
    verdict: DiskVerdictModel | None
    filesystem: DiskFilesystemModel


class DiskLimitRequest(BaseModel):
    """The limit to hold the application to."""

    soft_mb: int | None = Field(
        description="Megabytes, from 64; null removes the limit",
    )


def _known(domain: str) -> Any:
    """
    Find an application the caller may see.

    Args:
        domain: The domain from the path.

    Returns:
        Its row.

    Raises:
        HTTPException: 404 when it does not exist or is outside the caller's teams.
    """
    validated = strict_domain(domain)
    app = scoped_store().get_app(validated)
    if app is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return app


@router.get("/{domain}/disk", response_model=DiskResponse)
def get_disk(domain: str, session: Annotated[dict, Depends(get_current_session)]) -> DiskResponse:
    """
    Show what an application takes of the disk, as it was last measured. Measures nothing.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The limit, the measurement and the filesystem's facts.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    return DiskResponse(**app_disk.status(_known(domain)).to_dict())


@router.put("/{domain}/disk", response_model=DiskResponse)
def put_disk(
    domain: str,
    body: DiskLimitRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> DiskResponse:
    """
    Set or remove the limit an application's deploys are held to.

    It is Noust's limit, checked before a deploy builds a new release; it does not stop the
    application from writing.

    Args:
        domain: Domain of the application.
        body: The limit in MB, or null.
        session: The authenticated session.

    Returns:
        The disk as it is now.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: The limit is out of range (400).
    """
    app = _known(domain)
    app_disk.set_disk_limit(app, body.soft_mb, actor=actor_label(session))
    return DiskResponse(**app_disk.status(app).to_dict())


def measure_job(domain: str, job_context: JobContext | None = None) -> dict[str, Any]:
    """
    Measure what an application takes of the disk, as a background job.

    Args:
        domain: Domain of the application.
        job_context: Injected by the job manager.

    Returns:
        The measurement.

    Raises:
        NoustError: The application is gone.
    """
    if job_context is not None:
        job_context.set_metadata("domain", domain)
        job_context.update("Measuring the application's directory", 10)
    usage = app_disk.measure_domain(domain)
    if job_context is not None:
        job_context.update("Done", 100)
    return usage.to_dict()


@router.post("/{domain}/disk/measure", response_model=JobAcceptedResponse, status_code=202)
def post_disk_measure(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> JobAcceptedResponse:
    """
    Queue the measurement of what an application takes of the disk.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The queued job.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known(domain).domain
    actor = actor_label(session)
    job = get_job_manager().create_job(
        job_type=JobType.DISK,
        name=f"Disk of {validated}",
        description=f"Measuring what {validated} takes of the disk",
        func=measure_job,
        kwargs={"domain": validated},
        metadata={"domain": validated},
        actor=actor,
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Measuring what {validated} takes of the disk",
        job=job.to_dict(),
    )
