# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What every databases router shares: the service, the job queue, the models.

The routers are clients of :class:`~noust.managers.database.service.DatabaseService`
and nothing else: an endpoint translates HTTP into a service call and back.
Anything longer than a request should take (a dump, a restore, a drop with
its last dump, a link or a rotation that restarts applications) is queued as
a job, because a central's proxy cuts a request at 300 seconds and a large
dump outlives that while it keeps running on the node.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, Field

from noust.core.audit import Actor
from noust.core.logger import Logger
from noust.managers.database.service import DatabaseService
from noust.web.api.deps import JobAcceptedResponse
from noust.web.auth import actor_label
from noust.web.jobs import JobType, get_job_manager
from noust.web.permissions.principal import actor_of
from noust.web.pydantic_compat import iso_offset_validator


def session_actor(session: Mapping[str, Any] | None) -> Actor | None:
    """
    Name who a request acts for, as the audit trail records it.

    Args:
        session: The authenticated payload, or None outside a request.

    Returns:
        The actor, from the session's principal, else from its label.
    """
    if session is None:
        return None
    built = actor_of(session)
    return built if isinstance(built, Actor) else Actor.from_label(actor_label(session))


def service(
    session: Mapping[str, Any] | None = None, logger: Logger | None = None
) -> DatabaseService:
    """
    Build the service a request uses.

    Args:
        session: The authenticated session, whose actor the audit trail names.
        logger: Where progress goes; a job passes its capturing logger.

    Returns:
        A service over the process-wide store and registry.
    """
    return DatabaseService(actor=session_actor(session), logger=logger)


def queue(
    session: Mapping[str, Any],
    *,
    job_type: JobType,
    name: str,
    description: str,
    func: Callable[..., Any],
    kwargs: dict[str, Any],
    metadata: dict[str, Any],
    message: str,
    pass_actor: bool = True,
) -> JobAcceptedResponse:
    """
    Hand a long operation to the job manager and answer 202.

    Args:
        session: The authenticated session, for the job's actor.
        job_type: The job's type.
        name: Short name, for the jobs list.
        description: What it does.
        func: The job function; it receives ``job_context``.
        kwargs: Its arguments.
        metadata: Context for the jobs list. A ``domain`` also teaches the
            job's log scrubber that application's secrets.
        message: The response's summary.
        pass_actor: Hand the session's actor label to the job function as
            ``actor``, so what it records names who asked. Every job of this
            package takes it; a job function from elsewhere may not.

    Returns:
        The accepted job.
    """
    label = actor_label(session)
    job = get_job_manager().create_job(
        job_type=job_type,
        name=name,
        description=description,
        func=func,
        kwargs={**kwargs, "actor": label} if pass_actor else kwargs,
        metadata=metadata,
        actor=label,
    )
    return JobAcceptedResponse(
        job_id=job.id, status=job.status.value, message=message, job=job.to_dict()
    )


class ActionResponse(BaseModel):
    """Generic action outcome."""

    success: bool
    message: str


class DatabaseInfoResponse(BaseModel):
    """
    One database, as the engine and the store together describe it.

    Attributes:
        owner: The owning role or account. Null for engines with no such
            concept: MySQL/MariaDB and Redis have none, and MongoDB grants
            roles to users rather than owning a database with one.
        tables: Tables or collections; null when the listing does not count
            them.
        keys: Keys in a Redis slot.
        tracked: Recorded by Noust: backed up with its application, linkable.
        missing: Recorded by Noust but gone from the engine.
        unverified: Recorded by Noust, on an engine that could not be read.
        app: The application it belongs to, whose backups include it.
        apps: Every application linked to it.
        username: The account Noust provisioned for it.
        engine_version: The engine's version.
        last_backup: When its newest dump was taken.
    """

    name: str
    engine: str
    size: str | None = None
    tables: int | None = None
    keys: int | None = None
    owner: str | None = None
    encoding: str | None = None
    tracked: bool = False
    missing: bool = False
    unverified: bool = False
    app: str | None = None
    apps: list[str] = Field(default_factory=list)
    username: str | None = None
    engine_version: str | None = None
    last_backup: str | None = None

    _iso_timestamps = iso_offset_validator("last_backup")


class SupportNoticeResponse(BaseModel):
    """
    Where an engine version stands in its upstream support.

    Attributes:
        status: ``supported``, ``ending_soon``, ``ended`` or ``unknown``.
        message: One English sentence; the console renders its own copy
            from ``status`` and ``end_of_life``.
    """

    family: str
    version: str | None = None
    major: str | None = None
    end_of_life: str | None = None
    status: str
    message: str


class LinkResponse(BaseModel):
    """
    One database an application uses.

    Attributes:
        url: The connection string with the password masked, or null when
            the engine is down or the variable is not known (a database
            provisioned before 3.1 has no recorded variable).
        exists: Whether the database is still on the engine.
    """

    domain: str
    engine: str
    database: str
    username: str | None = None
    env_var: str
    extra_vars: bool = False
    url: str | None = None
    exists: bool = True
    size: str | None = None
    engine_version: str | None = None
    created_at: str | None = None

    _iso_timestamps = iso_offset_validator("created_at")


def database_response(view: Any) -> DatabaseInfoResponse:
    """
    Turn a service view into the response model.

    Args:
        view: A :class:`~noust.managers.database.service.DatabaseView`.

    Returns:
        The response.
    """
    return DatabaseInfoResponse(**view.to_dict())
