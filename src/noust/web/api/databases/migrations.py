# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Moving a database to another engine, and an engine to a newer major version.

``/api/databases/moves`` moves one database (dump, accounts, load, the
applications pointed at the target behind their health gate, the source left as
it was). ``/api/databases/instances/migrate`` does it for every database of an
engine, into a new instance of a newer version, and ``.../migrations`` lists the
moves and rolls one back. All of it is
:class:`~noust.managers.database.moves.DatabaseMover` and
:class:`~noust.managers.database.instance_migration.InstanceMigrations`, which
``noust db move`` and ``noust db instance migrate`` call too. Planning changes
nothing and answers at once; moving and rolling back ask for sudo mode and run
as jobs, because they dump, load and restart applications.

These are the server's, not a team's: they make an instance, rewrite applications'
variables and use an engine nobody owns.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from noust.managers.database.instance_migration import InstanceMigrations
from noust.managers.database.managed import ManagedInstances
from noust.managers.database.moves import DatabaseMover
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import queue, service
from noust.web.api.databases.jobs import migrate_job, move_job, rollback_job
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.jobs import JobType

router = APIRouter(route_class=NoustErrorRoute)


class MoveRequest(BaseModel):
    """
    A database to move.

    Attributes:
        source: The engine it is on: ``postgresql``, or an instance's key such
            as ``postgresql@pg16``.
        database: Its name.
        target: The engine it goes to.
        replace: Replace a database of that name the target already holds,
            after a safety copy.
        drop_source: Drop it from the source once moved, after a last dump. It
            stays there, untouched, otherwise.
    """

    source: str = Field(..., max_length=120)
    database: str = Field(..., max_length=128)
    target: str = Field(..., max_length=120)
    replace: bool = False
    drop_source: bool = False


class MovePlanResponse(BaseModel):
    """
    What moving a database would do. Nothing was changed.

    Attributes:
        apps: The applications that will be pointed at the target and restarted.
        accounts: The accounts that will exist on the target.
        new_passwords: The accounts among them Noust does not know the password
            of: they are made with a new one.
        policies: The backup policies that will follow the database.
        blockers: What stops the move.
        warnings: What the operator should know first.
    """

    source: str
    target: str
    database: str
    engine: str
    source_version: str | None = None
    target_version: str | None = None
    size: str | None = None
    exists_on_target: bool = False
    apps: list[str] = Field(default_factory=list)
    accounts: list[str] = Field(default_factory=list)
    new_passwords: list[str] = Field(default_factory=list)
    policies: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MigrateRequest(BaseModel):
    """
    An engine to move to a newer major version.

    Attributes:
        source: The instance (``pg16``) or the host's engine (``postgresql``).
        to_version: The version to go to, one the catalog offers and newer than
            the one it leaves.
        name: What to call the new instance; the flavour and the version when omitted.
        memory_mb: Its memory limit; the old instance's, or the engine's
            suggestion, when omitted.
        port: Its port on 127.0.0.1; a free one when omitted.
    """

    source: str = Field(..., max_length=120)
    to_version: str = Field(..., max_length=16)
    name: str | None = Field(default=None, max_length=24)
    memory_mb: int | None = Field(default=None, ge=1, le=4194304)
    port: int | None = Field(default=None, ge=1, le=65535)


class MigrationInstancePlan(BaseModel):
    """The instance a move would make: what ``/instances/plan`` says of one."""

    name: str
    key: str
    flavour: str
    engine: str
    display_name: str
    version: str
    image: str
    digest: str
    reference: str
    host_port: int
    memory_limit_mb: int
    warnings: list[str] = Field(default_factory=list)


class MigratePlanResponse(BaseModel):
    """
    What moving an engine to a newer version would do. Nothing was changed.

    Attributes:
        instance: The source's name when it is an instance; null for the host's engine.
        new_instance: The instance that will be made.
        databases: One plan per database that will move.
        apps: Every application that will be restarted.
        blockers: What stops the move. Nothing is changed while any is listed.
        warnings: What the operator should know first.
    """

    source: str
    instance: str | None = None
    flavour: str
    display_name: str
    from_version: str | None = None
    to_version: str
    target: str
    new_instance: MigrationInstancePlan
    databases: list[MovePlanResponse] = Field(default_factory=list)
    apps: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MovedDatabaseResponse(BaseModel):
    """One database a move carried across, and the applications pointed at its new home."""

    database: str
    apps: list[str] = Field(default_factory=list)


class MigrationResponse(BaseModel):
    """
    One move of an engine to a newer version.

    Attributes:
        id: Its number.
        source: The engine the databases left.
        target: The instance made for them.
        state: ``moving``, ``done``, ``rolling_back``, ``rolled_back`` or ``failed``.
        moved: What went across.
        can_roll_back: Whether the applications can still be pointed back: it
            finished and the engine it left is still there.
    """

    id: int
    source: str
    target: str
    flavour: str
    from_version: str | None = None
    to_version: str
    state: str
    moved: list[MovedDatabaseResponse] = Field(default_factory=list)
    created_at: str
    created_by: str | None = None
    finished_at: str | None = None
    can_roll_back: bool = False


class MigrationListResponse(BaseModel):
    """The moves to a newer version, newest first."""

    migrations: list[MigrationResponse]


@router.post("/moves/plan", response_model=MovePlanResponse)
def plan_move(
    request: MoveRequest, session: Annotated[dict, Depends(get_current_session)]
) -> MovePlanResponse:
    """
    Say what moving a database would do. Changes nothing.

    Args:
        request: The database and the two engines.
        session: The authenticated session.

    Returns:
        The plan, with what blocks it and what to know first.
    """
    plan = DatabaseMover(service(session)).plan(
        request.source, request.database, request.target, replace=request.replace
    )
    return MovePlanResponse(**plan.to_dict())


@router.post("/moves", response_model=JobAcceptedResponse, status_code=202)
def move_database(
    request: MoveRequest, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Queue moving a database to another engine, with sudo mode.

    It is planned first, so a move that could not succeed is refused here rather
    than failing in the job. The job dumps the database, makes its accounts on
    the target, loads it and points each application that uses it at the target
    behind its health gate; the source is not touched, and a failure puts
    everything back.

    Args:
        request: The database and the two engines.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        ValidationError: The plan has blockers.
    """
    plan = DatabaseMover(service(session)).plan(
        request.source, request.database, request.target, replace=request.replace
    )
    if plan.blockers:
        # The same refusal the job would give, now rather than in a failed job.
        DatabaseMover(service(session)).move(
            request.source, request.database, request.target, replace=request.replace
        )
    return queue(
        session,
        job_type=JobType.DATABASE,
        name=f"Move {request.database} to {plan.target}",
        description=f"Moving {request.database} from {plan.source} to {plan.target}",
        func=move_job,
        kwargs={
            "source": request.source,
            "database": request.database,
            "target": request.target,
            "replace": request.replace,
            "drop_source": request.drop_source,
        },
        metadata={
            "engine": plan.source,
            "database": request.database,
            "target": plan.target,
            "action": "move",
        },
        message=f"Moving {request.database} to {plan.target}",
    )


@router.post("/instances/migrate/plan", response_model=MigratePlanResponse)
def plan_migration(
    request: MigrateRequest, session: Annotated[dict, Depends(get_current_session)]
) -> MigratePlanResponse:
    """
    Say what moving an engine to a newer major version would do. Changes nothing.

    Args:
        request: The engine, the version and, optionally, the new instance's name, memory and port.
        session: The authenticated session.

    Returns:
        The plan, with every database and application it touches, what blocks it
        and what to know first.
    """
    plan = InstanceMigrations(ManagedInstances(service(session))).plan(
        request.source,
        request.to_version,
        name=request.name,
        memory=request.memory_mb,
        port=request.port,
    )
    return MigratePlanResponse(**plan.to_dict())


@router.post("/instances/migrate", response_model=JobAcceptedResponse, status_code=202)
def migrate_engine(
    request: MigrateRequest, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Queue moving an engine to a newer major version, with sudo mode.

    A new instance of the newer version is made, every database is moved into it
    and the applications are pointed at it behind their health gate. The engine
    it leaves is not touched: ``rollback`` goes back. A failure at any point
    undoes everything, the new instance included.

    Args:
        request: The engine and the version.
        session: The authenticated, elevated session.

    Returns:
        The queued job; its result is what was moved.

    Raises:
        ValidationError: The plan has blockers.
    """
    migrations = InstanceMigrations(ManagedInstances(service(session)))
    plan = migrations.plan(
        request.source,
        request.to_version,
        name=request.name,
        memory=request.memory_mb,
        port=request.port,
    )
    if plan.blockers:
        migrations.run(
            request.source,
            request.to_version,
            name=request.name,
            memory=request.memory_mb,
            port=request.port,
        )
    return queue(
        session,
        job_type=JobType.DATABASE,
        name=f"Move {plan.source} to {plan.display_name} {plan.to_version}",
        description=f"Moving the databases of {plan.source} to the new instance {plan.target}",
        func=migrate_job,
        kwargs={
            "source": request.source,
            "to_version": plan.to_version,
            "name": plan.target,
            "memory_mb": plan.new_instance["memory_limit_mb"],
            "port": plan.new_instance["host_port"],
        },
        metadata={"instance": plan.target, "action": "migrate"},
        message=f"Moving {plan.source} to {plan.display_name} {plan.to_version}",
    )


@router.get("/instances/migrations", response_model=MigrationListResponse)
def list_migrations(
    session: Annotated[dict, Depends(get_current_session)],
) -> MigrationListResponse:
    """
    List the moves to a newer version, newest first.

    Args:
        session: The authenticated session.

    Returns:
        The moves, each with whether it can still be rolled back.
    """
    entries: list[dict[str, Any]] = InstanceMigrations(ManagedInstances(service(session))).all()
    return MigrationListResponse(migrations=[MigrationResponse(**entry) for entry in entries])


@router.post(
    "/instances/migrations/{migration_id}/rollback",
    response_model=JobAcceptedResponse,
    status_code=202,
)
def rollback_migration(
    migration_id: int, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Queue pointing the applications back at the engine a move left, with sudo mode.

    What was written to the new instance since the move is dumped first and is
    not carried back; the new instance is stopped, not removed.

    Args:
        migration_id: The move's number.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        DatabaseNotFoundError: There is no such move.
        DatabaseEngineError: It cannot be rolled back.
    """
    migrations = InstanceMigrations(ManagedInstances(service(session)))
    entry = next((item for item in migrations.all() if item["id"] == migration_id), None)
    if entry is None or not entry["can_roll_back"]:
        # The same refusal the job would give, now rather than in a failed job.
        migrations.rollback(migration_id)
    return queue(
        session,
        job_type=JobType.DATABASE,
        name=f"Roll back move {migration_id}",
        description=f"Pointing the applications back at {entry['source'] if entry else '?'}",
        func=rollback_job,
        kwargs={"migration": migration_id},
        metadata={"migration": migration_id, "action": "rollback"},
        message=f"Rolling back move {migration_id}",
    )
