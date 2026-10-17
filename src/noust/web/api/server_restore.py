# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/server/restore``: bringing the applications and databases of a restored server back.

A client of :class:`~noust.managers.restore_server.RestoreServer`, which ``noust restore-server``
calls too. ``GET /plan`` says what a restore would do now, ``POST /`` does it as a job: installs
the web server, Docker and the database engines when asked, creates the PostgreSQL roles, brings
back the databases no application owns and restores each application from its newest backup behind
the health gate.

What cannot be done from the API is putting the state back: ``noust restore-server`` replaces the
store the console is running on, so that step is a command at a terminal and the job always keeps
the state that is here. Run the command once on the new machine, then drive the rest from the
console.

These are the server's, not a team's: they install software and restore every application.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from noust.core.audit import Actor
from noust.deployers.recorder import CapturingLogger
from noust.managers.restore_server import RestoreServer
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import queue, session_actor
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.jobs import JobContext, JobType

router = APIRouter(route_class=NoustErrorRoute)


class RestorePlanResponse(BaseModel):
    """
    What restoring would do now. Nothing was changed.

    Attributes:
        requirements: The web server and Docker: ``name``, ``state`` (``present``, ``missing``
            or ``failed``), ``detail`` and the ``command`` that installs it by hand.
        engines_missing: The database engines the databases live on that are not installed,
            each with its command.
        roles: The PostgreSQL servers whose roles wait for their engine.
        databases: The databases no application owns, with the dump each would be loaded from.
        apps: Every application with its newest backup (``source`` is ``local`` or a
            destination).
    """

    requirements: list[dict[str, Any]] = Field(default_factory=list)
    engines_missing: list[dict[str, str]] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    databases: list[dict[str, Any]] = Field(default_factory=list)
    apps: list[dict[str, Any]] = Field(default_factory=list)


class RestoreRequest(BaseModel):
    """
    A restore to run.

    Attributes:
        install: Install the web server, Docker (when an application or an instance needs it)
            and the database engines that are missing.
        databases: Also bring back the databases no application owns.
        from_destination: The destination a move was prepared on.
        apps: Restore only these applications (every one when empty).
        skip: Leave these applications out.
        accept_schema_changes: Restore an application's files even past a schema change
            nothing restores the database for.
    """

    install: bool = False
    databases: bool = True
    from_destination: str | None = Field(default=None, max_length=64)
    apps: list[str] = Field(default_factory=list, max_length=500)
    skip: list[str] = Field(default_factory=list, max_length=500)
    accept_schema_changes: bool = False


def _flow(
    session: dict[str, Any] | None = None,
    job_context: JobContext | None = None,
    actor: str | None = None,
) -> RestoreServer:
    """
    Build the restore a request or a job uses.

    Args:
        session: The authenticated session of a request.
        job_context: The running job; its log receives what the restore reports.
        actor: The label of who queued a job.

    Returns:
        The restore.
    """
    logger = CapturingLogger(verbose=False)
    who: Actor | None = session_actor(session) if session is not None else None
    if job_context is not None:
        logger.attach_sink(job_context.log)
        who = Actor.from_label(actor) if actor else None
    return RestoreServer(logger=logger, actor=who)


@router.get("/plan", response_model=RestorePlanResponse)
def plan_restore(
    session: Annotated[dict, Depends(get_current_session)],
    from_destination: Annotated[str | None, Query(max_length=64)] = None,
) -> RestorePlanResponse:
    """
    Say what restoring would do now. Changes nothing.

    Args:
        session: The authenticated session.
        from_destination: The destination a move was prepared on.

    Returns:
        What is missing on this machine, the roles and databases that wait, and each
        application's newest backup.
    """
    return RestorePlanResponse(**_flow(session).preview(from_destination=from_destination))


def restore_job(
    install: bool = False,
    databases: bool = True,
    from_destination: str | None = None,
    apps: list[str] | None = None,
    skip: list[str] | None = None,
    accept_schema_changes: bool = False,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Bring the server's applications and databases back, keeping the state that is here.

    Args:
        install: Install what is missing.
        databases: Also bring back the databases no application owns.
        from_destination: The destination a move was prepared on.
        apps: Only these applications.
        skip: Leave these out.
        accept_schema_changes: Restore past a schema change.
        actor: Who queued it.
        job_context: Injected by the job manager.

    Returns:
        The report, as :meth:`~noust.managers.restore_server.ServerRestoreReport.to_dict` says.
    """
    if job_context is not None:
        job_context.update("Restoring the server", 5)
    flow = _flow(None, job_context, actor)
    report = flow.run(
        skip_state=True,
        only=apps or (),
        skip=skip or (),
        accept_schema_changes=accept_schema_changes,
        install=install,
        databases=databases,
        from_destination=from_destination,
    )
    if job_context is not None:
        job_context.update("Done", 100)
    return report.to_dict()


@router.post("", response_model=JobAcceptedResponse, status_code=202)
def restore(
    request: RestoreRequest, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Queue bringing the applications and databases back, with sudo mode.

    Args:
        request: What to install, restore and leave out.
        session: The authenticated, elevated session.

    Returns:
        The queued job; its result is the report.
    """
    return queue(
        session,
        # Not BACKUP: a failed backup job is announced as a failed backup, and this is a
        # restore of a whole server, which reports through its own report.
        job_type=JobType.CUSTOM,
        name="Restore the server",
        description="Installing what is missing and restoring the databases and applications",
        func=restore_job,
        kwargs={
            "install": request.install,
            "databases": request.databases,
            "from_destination": request.from_destination,
            "apps": list(request.apps),
            "skip": list(request.skip),
            "accept_schema_changes": request.accept_schema_changes,
        },
        metadata={"kind": "server-restore", "install": request.install},
        message="Restoring the server",
    )
