# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Restore drills of an application (4.0): prove its newest backup restores, without restoring it.

A client of :class:`~noust.managers.restore_drill.RestoreDrill`, like ``noust
backup drill``: ``POST`` queues a drill as a job (it verifies in depth, unpacks
and loads each dump into a temporary database, which takes as long as the backup
is big), ``GET`` lists the reports kept, a scheduled backup's included.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from noust.core.audit import Actor
from noust.core.teams import scoped_store
from noust.deployers.recorder import CapturingLogger
from noust.managers.restore_drill import DrillRecords, RestoreDrill, drill_days
from noust.validators.domain import validate_domain
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import queue
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute
from noust.web.jobs import JobContext, JobType

router = APIRouter(route_class=NoustErrorRoute)


class DrillStepOut(BaseModel):
    """
    One thing a drill checked.

    Attributes:
        name: What it was (``archive``, ``unpack``, ``postgresql/shop``...).
        ok: Whether it passed.
        detail: What was found, or what went wrong, verbatim.
        skipped: Whether it was not tried, and ``detail`` says why.
    """

    name: str
    ok: bool
    detail: str
    skipped: bool = False


class DrillOut(BaseModel):
    """
    A drill's report.

    Attributes:
        id: The report's number.
        domain: The application.
        backup_id: The backup rehearsed.
        trigger: ``manual`` or ``schedule``.
        started_at: When it began (UTC).
        finished_at: When it ended.
        ok: Whether everything that was tried passed.
        summary: One sentence.
        steps: Everything it checked, in order.
    """

    id: int | None = None
    domain: str
    backup_id: str | None = None
    trigger: str
    started_at: str
    finished_at: str = ""
    ok: bool
    summary: str
    steps: list[DrillStepOut] = Field(default_factory=list)


class DrillListOut(BaseModel):
    """
    The drills of an application.

    Attributes:
        items: The reports, newest first.
        drill_days: Days between the drills a scheduled backup runs on its
            own (``backup.drill_days``); 0 when they are off.
    """

    items: list[DrillOut]
    drill_days: int


class DrillRequest(BaseModel):
    """
    A drill to run.

    Attributes:
        backup_id: The backup to rehearse; the newest when omitted.
    """

    backup_id: str | None = Field(default=None, max_length=200)


def _known(domain: str) -> str:
    """
    Validate a domain and refuse one nothing is deployed at.

    Args:
        domain: The domain as the client sent it.

    Returns:
        The validated domain.

    Raises:
        HTTPException: 404 when nothing is deployed at it (or it is another
            team's).
    """
    validated = validate_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


def drill_job(
    domain: str,
    backup_id: str | None = None,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Run a restore drill as a background job.

    Args:
        domain: The application.
        backup_id: The backup to rehearse; the newest when None.
        actor: Who queued it.
        job_context: Injected by the job manager.

    Returns:
        The report as :meth:`~noust.managers.restore_drill.DrillReport.to_dict`
        gives it. A drill that fails is a report with ``ok`` false, not an
        error: the job succeeded in finding out.
    """
    steps = {"count": 0}

    def progress(text: str) -> None:
        steps["count"] += 1
        if job_context is not None:
            job_context.update(text, min(90, 10 + 15 * steps["count"]))

    logger = CapturingLogger(verbose=False)
    if job_context is not None:
        logger.attach_sink(job_context.log)
    who = Actor.from_label(actor) if actor else None
    report = RestoreDrill(logger=logger, actor=who).run(
        domain, backup_id=backup_id, on_step=progress
    )
    if job_context is not None:
        job_context.update("Done", 100)
    return report.to_dict()


@router.get("/{domain}/restore-drills", response_model=DrillListOut)
def list_drills(
    domain: str,
    session: Annotated[dict, Depends(get_current_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DrillListOut:
    """
    List an application's restore drills, newest first.

    Args:
        domain: The application.
        session: The authenticated session.
        limit: How many to return.

    Returns:
        The reports, and how often a scheduled backup runs one on its own.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known(domain)
    return DrillListOut(
        items=[
            DrillOut(**report.to_dict()) for report in DrillRecords().list(validated, limit=limit)
        ],
        drill_days=drill_days(),
    )


@router.post("/{domain}/restore-drills", response_model=JobAcceptedResponse, status_code=202)
def run_drill(
    domain: str,
    session: Annotated[dict, Depends(get_current_session)],
    body: DrillRequest | None = None,
) -> JobAcceptedResponse:
    """
    Queue a restore drill of an application.

    The newest backup (or the one named) is verified in depth, unpacked with a
    restore's own rules, and each database dump in it is loaded into a temporary
    database and dropped. Nothing of the application is touched.

    Args:
        domain: The application.
        session: The authenticated session.
        body: The backup to rehearse, if not the newest.

    Returns:
        The queued job; its result is the report.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known(domain)
    return queue(
        session,
        job_type=JobType.BACKUP,
        name=f"Restore drill of {validated}",
        description=f"Rehearsing a restore of {validated}'s newest backup",
        func=drill_job,
        kwargs={"domain": validated, "backup_id": (body.backup_id if body else None)},
        metadata={"domain": validated, "kind": "restore_drill"},
        message=f"Restore drill of {validated} queued",
    )
