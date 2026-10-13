# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/server/migration``: moving this server's applications, data and state to another server.

A client of :class:`~noust.managers.server_migration.ServerMigration`, which ``noust server
migrate`` calls too. ``GET /plan`` says what moving would take and what stops it, ``POST
/prepare`` copies everything to a destination as a job (the applications' backups, their data, the
databases no application owns and, last, Noust's own state sealed under a passphrase that is never
sent anywhere), and ``GET /check``, on the new server, asks every application the questions to
answer before DNS moves.

What cannot be done from the API is the restore itself: ``noust restore-server`` replaces the store
the console is running on, so it is a command at a terminal, like ``noust self-backup restore``.

These are the server's, not a team's: they copy every application and the server's secrets.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from noust.core.audit import Actor
from noust.deployers.recorder import CapturingLogger
from noust.managers.server_migration import ServerMigration
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import queue, session_actor
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.jobs import JobContext, JobType

router = APIRouter(route_class=NoustErrorRoute)


class MigrationPlanResponse(BaseModel):
    """
    What moving this server would take. Nothing was changed.

    Attributes:
        host: This server's name as a folder on the destination.
        destination: Where the copies would go.
        applications: Each application: ``domain``, ``type``, ``names``, ``databases``,
            ``data`` (how many pieces) and ``last_backup``.
        databases: The databases no application owns, as ``engine/database``.
        engines: The database engines the new server needs.
        instances: The database instances Noust runs here, which are made again there.
        passphrase_stored: Whether the bundle can be sealed under the stored passphrase.
        blockers: What stops the preparation. Nothing is copied while any is listed.
        warnings: What to know first.
    """

    host: str
    destination: str
    applications: list[dict[str, Any]] = Field(default_factory=list)
    databases: list[str] = Field(default_factory=list)
    engines: list[str] = Field(default_factory=list)
    instances: list[str] = Field(default_factory=list)
    passphrase_stored: bool = False
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PrepareRequest(BaseModel):
    """
    A preparation to run.

    Attributes:
        destination: A backup destination both servers reach.
        passphrase: What Noust's own state is sealed under; the stored one when omitted. It
            is not kept, not logged and not part of any result.
        apps: Copy only these applications (every one when empty).
        data: Also copy the applications' data.
    """

    destination: str = Field(..., max_length=64)
    passphrase: str | None = Field(default=None, min_length=1, max_length=1024)
    apps: list[str] = Field(default_factory=list, max_length=500)
    data: bool = True


class MigrationCheckResponse(BaseModel):
    """
    What the check found on the new server.

    Attributes:
        generated_at: When it was made.
        host: This server's name.
        ok: False when an application is down (a name that still points elsewhere is pending).
        counts: How many applications are ``ok``, need ``attention`` and are ``down``.
        addresses: This server's addresses.
        applications: Each application with its state, probe, names and databases.
        pending: The names that still resolve elsewhere, and where.
    """

    generated_at: str
    host: str
    ok: bool
    counts: dict[str, int]
    addresses: list[str] = Field(default_factory=list)
    applications: list[dict[str, Any]] = Field(default_factory=list)
    pending: list[dict[str, Any]] = Field(default_factory=list)


def _migration(
    session: dict[str, Any] | None = None,
    job_context: JobContext | None = None,
    actor: str | None = None,
) -> ServerMigration:
    """
    Build the migration a request or a job uses.

    Args:
        session: The authenticated session of a request.
        job_context: The running job; its log receives what the migration reports.
        actor: The label of who queued a job.

    Returns:
        The migration.
    """
    logger = CapturingLogger(verbose=False)
    who: Actor | None = session_actor(session) if session is not None else None
    if job_context is not None:
        logger.attach_sink(job_context.log)
        who = Actor.from_label(actor) if actor else None
    return ServerMigration(logger=logger, actor=who)


@router.get("/plan", response_model=MigrationPlanResponse)
def plan_migration(
    destination: Annotated[str, Query(max_length=64)],
    session: Annotated[dict, Depends(get_current_session)],
) -> MigrationPlanResponse:
    """
    Say what moving this server would take, and whether it can start. Changes nothing.

    Args:
        destination: A backup destination both servers reach.
        session: The authenticated session.

    Returns:
        The plan; ``blockers`` lists what stops the preparation (the destination does not
        exist or cannot be reached).
    """
    return MigrationPlanResponse(**_migration(session).plan(destination).to_dict())


def prepare_job(
    destination: str,
    passphrase: str | None = None,
    apps: list[str] | None = None,
    data: bool = True,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Copy everything to a destination.

    Args:
        destination: The destination.
        passphrase: What the state is sealed under; the stored one when None.
        apps: Only these applications.
        data: Also copy their data.
        actor: Who queued it.
        job_context: Injected by the job manager.

    Returns:
        The report, without the passphrase.

    Raises:
        ValidationError: No passphrase was given and none is stored, or the destination
            cannot be used.
    """
    if job_context is not None:
        job_context.update("Preparing the move", 5)
    migration = _migration(None, job_context, actor)
    sealed_under = passphrase or migration.self_backups.stored_passphrase()
    if not sealed_under:
        from noust.core.exceptions import ValidationError

        raise ValidationError(
            "No passphrase was given and none is stored",
            details="Give one with the request, or set the self-backup schedule with a passphrase.",
            field="passphrase",
        )
    report = migration.prepare(destination, sealed_under, only=apps or (), data=data)
    if job_context is not None:
        job_context.update("Done", 100)
    return report.to_dict()


@router.post("/prepare", response_model=JobAcceptedResponse, status_code=202)
def prepare_migration(
    request: PrepareRequest, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Queue copying everything to a destination, with sudo mode.

    The plan is read first, so a destination that does not exist or cannot be reached is
    refused here rather than in a failed job.

    Args:
        request: The destination, the passphrase and what to leave out.
        session: The authenticated, elevated session.

    Returns:
        The queued job; its result is the report.

    Raises:
        ValidationError: 400 when the plan has a blocker or no passphrase is available.
    """
    migration = _migration(session)
    plan = migration.plan(request.destination)
    if plan.blockers or not (request.passphrase or plan.passphrase_stored):
        from noust.core.exceptions import ValidationError

        reasons = list(plan.blockers)
        if not (request.passphrase or plan.passphrase_stored):
            reasons.append(
                "No passphrase was given and none is stored: Noust's state is sealed under one"
            )
        raise ValidationError(
            "This server cannot be prepared for a move yet",
            details="\n".join(f"- {reason}" for reason in reasons),
            field="destination",
        )
    return queue(
        session,
        job_type=JobType.BACKUP,
        name=f"Prepare the move to another server ({request.destination})",
        description="Copying the applications, their data and Noust's own state",
        func=prepare_job,
        kwargs={
            "destination": request.destination,
            "passphrase": request.passphrase,
            "apps": list(request.apps),
            "data": request.data,
        },
        metadata={"kind": "server-migration", "destination": request.destination},
        message="Preparing the move",
    )


@router.get("/check", response_model=MigrationCheckResponse)
def check_migration(
    session: Annotated[dict, Depends(get_current_session)],
) -> MigrationCheckResponse:
    """
    Ask every application the questions to answer before DNS moves. Changes nothing.

    Run it on the new server after ``noust restore-server``.

    Args:
        session: The authenticated session.

    Returns:
        The dated report.
    """
    return MigrationCheckResponse(**_migration(session).check().to_dict())
