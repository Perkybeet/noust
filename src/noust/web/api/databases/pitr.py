# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/pitr``: recover PostgreSQL to an instant (checklist H-32).

A client of :class:`~noust.managers.database.pitr.PitrManager`, like ``noust db pitr``. Reading
and planning are requests; turning archiving on or off, a base backup, sending the archive,
bringing it back and the recovery itself are jobs, because each restarts the engine, copies the
cluster or waits for a replay of the log. The guards that matter are in the manager, not here:
a restart or a replaced cluster is refused without ``confirm`` whoever asks.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from noust.core.exceptions import DatabaseEngineError, ValidationError
from noust.managers.database.pitr import PitrManager
from noust.managers.database.pitr_archive import parse_recovery_time
from noust.managers.database.postgres import PostgresManager
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import queue, service
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.jobs import JobType, database_pitr_job
from noust.web.pydantic_compat import dump_model

router = APIRouter(route_class=NoustErrorRoute)


class PitrDestinationRequest(BaseModel):
    """
    A destination the archive is sent to.

    Attributes:
        name: The backup destination.
        retention_count: Base backups kept there; the policy's when empty.
        retention_days: How far back a recovery must reach there; the policy's when empty.
    """

    name: str = Field(max_length=64)
    retention_count: int | None = Field(default=None, ge=1, le=100_000)
    retention_days: int | None = Field(default=None, ge=1, le=100_000)


class PitrEnableRequest(BaseModel):
    """
    How to archive the log and what to keep.

    Attributes:
        base_every_hours: A base backup is taken when the last is this old.
        retention_count: Base backups kept; no limit by number when empty.
        retention_days: How far back a recovery must reach; no limit by age when empty.
        destinations: Where the archive is sent.
        confirm: The operator accepted that PostgreSQL restarts once.
    """

    base_every_hours: int = Field(default=168, ge=1, le=8784)
    retention_count: int | None = Field(default=None, ge=1, le=100_000)
    retention_days: int | None = Field(default=7, ge=1, le=100_000)
    destinations: list[PitrDestinationRequest] = Field(default_factory=list, max_length=10)
    confirm: bool = False


class PitrDisableRequest(BaseModel):
    """
    Turning archiving off.

    Attributes:
        delete_archive: Also delete the archive, the base backups and the destinations' copies.
        confirm: The operator accepted that PostgreSQL restarts once.
    """

    delete_archive: bool = False
    confirm: bool = False


class PitrFetchRequest(BaseModel):
    """
    Bringing the archive back.

    Attributes:
        destination: One of the policy's destinations.
    """

    destination: str = Field(max_length=64)


class PitrRestoreRequest(BaseModel):
    """
    The instant to recover to.

    Attributes:
        time: The instant (UTC unless it carries an offset); the end of the log when empty.
        base: A base backup to start from instead of the best one.
        confirm: The operator accepted that the whole cluster is replaced.
    """

    time: str | None = Field(default=None, max_length=64)
    base: str | None = Field(default=None, max_length=64)
    confirm: bool = False


class PitrStatusResponse(BaseModel):
    """
    How point-in-time recovery stands.

    Attributes:
        enabled: The log is being archived.
        policy: The policy, when there is one.
        bases: The base backups, oldest first.
        archive: Where the log is, how many files and bytes.
        window: The earliest and latest instants a recovery can reach.
        archiver: The engine's own account of its archiver.
        timer: The upkeep timer.
        warnings: What the operator should look at.
    """

    engine: str
    enabled: bool
    policy: dict[str, Any] | None = None
    bases: list[dict[str, Any]] = Field(default_factory=list)
    archive: dict[str, Any] | None = None
    window: dict[str, Any] | None = None
    archiver: dict[str, Any] | None = None
    timer: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)


class PitrEnablePlanResponse(BaseModel):
    """
    What turning archiving on would do. Nothing was changed.

    Attributes:
        settings: What the engine is given.
        restart: Whether it restarts.
        archive_path: Where the log is archived.
    """

    settings: dict[str, str]
    restart: bool
    archive_path: str
    system_id: str
    data_directory: str
    base_every_hours: int
    retention_count: int | None = None
    retention_days: int | None = None
    wal_level_was: str | None = None
    destinations: list[dict[str, Any]] = Field(default_factory=list)


class PitrRestorePlanResponse(BaseModel):
    """
    What a recovery would do. Nothing was changed.

    Attributes:
        base: The base backup it starts from.
        target: The instant it stops at; empty for the end of the log.
        data_directory: The cluster that is replaced.
        saved_as: Where the replaced cluster is kept.
        segments: How many segments of the log it replays.
        last_archived_at: The newest instant the archive reaches.
        missing: Segments the log lacks (empty when it is whole).
        warnings: What the operator should know.
        timeline: The timeline it follows.
        restarts_engine: Always true.
    """

    base: dict[str, Any]
    target: str | None = None
    data_directory: str
    saved_as: str
    segments: int
    last_archived_at: str | None = None
    missing: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    timeline: int | None = None
    restarts_engine: bool = True


def _pitr(session: dict[str, Any]) -> PitrManager:
    """
    Build the manager every route here uses.

    Args:
        session: The authenticated session.

    Returns:
        The manager over the server's PostgreSQL.

    Raises:
        DatabaseEngineError: When the engine resolved is not PostgreSQL's manager.
    """
    manager = service(session).manager("postgresql")
    if not isinstance(manager, PostgresManager):
        raise DatabaseEngineError("Point-in-time recovery is for PostgreSQL")
    return PitrManager(manager)


def _job(
    session: dict[str, Any], action: str, name: str, options: dict[str, Any]
) -> JobAcceptedResponse:
    """
    Queue one action.

    Args:
        session: The authenticated session.
        action: The job function's action.
        name: What the jobs list shows.
        options: What the action takes.

    Returns:
        The accepted job.
    """
    return queue(
        session,
        job_type=JobType.CUSTOM,
        name=name,
        description=name,
        func=database_pitr_job,
        kwargs={"action": action, "options": options},
        metadata={"engine": "postgresql", "action": action},
        message=f"{name}: queued",
    )


@router.get("/pitr", response_model=PitrStatusResponse)
def get_status(session: Annotated[dict, Depends(get_current_session)]) -> PitrStatusResponse:
    """
    Say how point-in-time recovery stands.

    Args:
        session: The authenticated session.

    Returns:
        The status.
    """
    return PitrStatusResponse(**_pitr(session).status())


@router.post("/pitr/enable/plan", response_model=PitrEnablePlanResponse)
def plan_enable(
    request: PitrEnableRequest, session: Annotated[dict, Depends(get_current_session)]
) -> PitrEnablePlanResponse:
    """
    Say what turning archiving on would do, changing nothing.

    Args:
        request: The policy.
        session: The authenticated session.

    Returns:
        The plan.
    """
    return PitrEnablePlanResponse(
        **_pitr(session).plan_enable(
            base_every_hours=request.base_every_hours,
            retention_count=request.retention_count,
            retention_days=request.retention_days,
            destinations=[dump_model(d) for d in request.destinations],
        )
    )


@router.post("/pitr/enable", response_model=JobAcceptedResponse, status_code=202)
def enable(
    request: PitrEnableRequest, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Queue turning archiving on, with sudo mode.

    Args:
        request: The policy and the confirmation of the restart.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        ValidationError: When the restart was not confirmed.
    """
    destinations = [dump_model(d) for d in request.destinations]
    plan = _pitr(session).plan_enable(
        base_every_hours=request.base_every_hours,
        retention_count=request.retention_count,
        retention_days=request.retention_days,
        destinations=destinations,
    )
    if plan["restart"] and not request.confirm:
        raise ValidationError(
            "Turning on archiving restarts PostgreSQL",
            details="archive_mode only changes with a restart: every connection is dropped for "
            "a few seconds. Send confirm to go ahead.",
            field="confirm",
        )
    return _job(
        session,
        "enable",
        "Archive PostgreSQL's log",
        {
            "base_every_hours": request.base_every_hours,
            "retention_count": request.retention_count,
            "retention_days": request.retention_days,
            "destinations": destinations,
        },
    )


@router.post("/pitr/disable", response_model=JobAcceptedResponse, status_code=202)
def disable(
    request: PitrDisableRequest, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Queue turning archiving off, with sudo mode.

    Args:
        request: Whether to delete the archive, and the confirmation of the restart.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        ValidationError: When the restart was not confirmed.
    """
    if not request.confirm:
        raise ValidationError(
            "Turning off archiving restarts PostgreSQL",
            details="archive_mode only changes with a restart. Send confirm to go ahead.",
            field="confirm",
        )
    return _job(
        session,
        "disable",
        "Stop archiving PostgreSQL's log",
        {"delete_archive": request.delete_archive},
    )


@router.post("/pitr/base", response_model=JobAcceptedResponse, status_code=202)
def take_base(session: Annotated[dict, Depends(require_elevated)]) -> JobAcceptedResponse:
    """
    Queue a base backup, with sudo mode.

    Args:
        session: The authenticated, elevated session.

    Returns:
        The queued job.
    """
    return _job(session, "base", "Base backup of PostgreSQL", {})


@router.post("/pitr/sync", response_model=JobAcceptedResponse, status_code=202)
def sync(session: Annotated[dict, Depends(require_elevated)]) -> JobAcceptedResponse:
    """
    Queue sending the archive to the destinations, with sudo mode.

    Args:
        session: The authenticated, elevated session.

    Returns:
        The queued job.
    """
    return _job(session, "sync", "Send PostgreSQL's archive to its destinations", {})


@router.post("/pitr/fetch", response_model=JobAcceptedResponse, status_code=202)
def fetch(
    request: PitrFetchRequest, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Queue bringing the archive back from a destination, with sudo mode.

    Args:
        request: The destination.
        session: The authenticated, elevated session.

    Returns:
        The queued job.
    """
    return _job(
        session,
        "fetch",
        "Fetch PostgreSQL's archive",
        {"destination": request.destination},
    )


@router.post("/pitr/restore/plan", response_model=PitrRestorePlanResponse)
def plan_restore(
    request: PitrRestoreRequest, session: Annotated[dict, Depends(get_current_session)]
) -> PitrRestorePlanResponse:
    """
    Say what a recovery would do, changing nothing.

    Args:
        request: The instant and, optionally, the base backup.
        session: The authenticated session.

    Returns:
        The plan.
    """
    target = parse_recovery_time(request.time) if request.time else None
    return PitrRestorePlanResponse(
        **_pitr(session).plan_restore(target, base_id=request.base or None).to_dict()
    )


@router.post("/pitr/restore", response_model=JobAcceptedResponse, status_code=202)
def restore(
    request: PitrRestoreRequest, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Queue a recovery to an instant, with sudo mode.

    The plan is made first, so a refusal (no base backup that old, a gap in the log) answers
    now and not from a job.

    Args:
        request: The instant, the base backup and the confirmation.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        ValidationError: When the replacement of the cluster was not confirmed.
    """
    target = parse_recovery_time(request.time) if request.time else None
    plan = _pitr(session).plan_restore(target, base_id=request.base or None)
    if not request.confirm:
        raise ValidationError(
            "A recovery replaces the whole cluster",
            details=f"{plan.data_directory} is renamed aside and the base backup {plan.base.id} "
            "is replayed over a fresh one. Every database goes back to that instant. Send "
            "confirm to go ahead.",
            field="confirm",
        )
    return _job(
        session,
        "restore",
        "Recover PostgreSQL to an instant",
        {"time": request.time, "base": request.base},
    )
