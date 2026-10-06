# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/databases``: list, create, describe, drop, adopt, fix the owner.

Create, drop and adopt go through the one service the CLI uses, so a database
made or dropped here is recorded or forgotten in the store exactly as
``noust db create`` and ``noust db drop`` do it; before 3.1 this module did
neither, and a database dropped from the console broke its application's next
backup.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from noust.core.exceptions import ValidationError
from noust.core.teams import ownership_target, scoped_store
from noust.managers.database.registry import DatabaseRegistry
from noust.web.api.auth import get_current_session
from noust.web.api.databases import jobs
from noust.web.api.databases.common import (
    ActionResponse,
    DatabaseInfoResponse,
    LinkResponse,
    SupportNoticeResponse,
    database_response,
    queue,
    service,
)
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.api.scope import resolve_target_for
from noust.web.jobs import JobType
from noust.web.permissions import Permission

router = APIRouter(route_class=NoustErrorRoute)


class ListingProblemResponse(BaseModel):
    """
    An engine whose databases could not be read.

    Attributes:
        message: What failed.
        hint: How to fix it.
        output: The engine's own message, verbatim.
        access: The engine refused to sign Noust in: the fix is to store the
            account Noust uses.
        kind: ``host``, ``container`` (an instance Docker runs; ``engine``
            is its key) or ``docker`` (Docker itself did not answer).
    """

    engine: str
    display_name: str
    message: str
    hint: str
    output: str
    access: bool
    kind: str = "host"


class DatabaseListResponse(BaseModel):
    """
    Response for listing databases.

    Attributes:
        problems: The engines that could not be read, so an empty list is
            never mistaken for an engine with nothing in it.
    """

    databases: list[DatabaseInfoResponse]
    total: int
    problems: list[ListingProblemResponse] = Field(default_factory=list)


class CreateDatabaseRequest(BaseModel):
    """
    Request to create a database.

    Attributes:
        app: The application it belongs to, so its backups include it. It then
            lives where the application does.
        team: The team it belongs to when it has no application; omitted: the
            one the caller works in.
        project: Its project; omitted: the team's default.
        environment: Its environment; omitted: production.
    """

    name: str = Field(..., description="Database name")
    engine: str = Field(..., description="Database engine")
    owner: str | None = Field(default=None, description="Database owner")
    encoding: str | None = Field(default=None, description="Character encoding")
    app: str | None = Field(default=None, description="Application the database belongs to")
    team: str | None = Field(default=None, description="Team, for a database with no application")
    project: str | None = Field(default=None, description="Project it belongs to")
    environment: str | None = Field(default=None, description="Environment it belongs to")


class AdoptRequest(BaseModel):
    """Request to adopt the databases Noust does not track."""

    engine: str | None = Field(default=None, description="Only this engine")


class AdoptResponse(BaseModel):
    """
    What adopting recorded.

    Attributes:
        adopted: The databases now tracked, as ``engine/name``.
    """

    adopted: list[str]


class RecordLinkRequest(BaseModel):
    """
    A use found in an application's environment, to record as a link.

    Attributes:
        app: The application whose environment names the database.
    """

    app: str = Field(..., min_length=1, max_length=253)


class AccessEntryResponse(BaseModel):
    """
    One account's access to one database.

    Attributes:
        profile: ``owner``, ``read_write``, ``read_only`` or ``custom``.
        internal: The engine's own account or Noust's read-only console's;
            never changed through Noust.
        managed: Noust knows its password, so it can be shown (sudo mode) or
            linked to an application.
        apps: Applications whose connection string signs in as it.
        password_changed_at: When Noust last set its password.
    """

    username: str
    host: str = "localhost"
    profile: str = "custom"
    privileges: list[str] = Field(default_factory=list)
    internal: bool = False
    managed: bool = False
    apps: list[str] = Field(default_factory=list)
    password_changed_at: str | None = None


class DatabaseOverviewResponse(BaseModel):
    """
    Everything the database page's Overview shows, in one call.

    Attributes:
        port: The port the engine really listens on.
        service: The systemd unit it runs as.
        capabilities: The engine's capabilities, for the page's tabs.
        support: Upstream support for the engine's version.
        warnings: What the operator must know about the engine.
        access: Who can reach the database.
        links: The applications using it.
        backups: How many dumps of it are on disk.
    """

    database: DatabaseInfoResponse
    display_name: str
    port: int
    service: str
    capabilities: list[str]
    support: SupportNoticeResponse
    warnings: list[str]
    access: list[AccessEntryResponse]
    links: list[LinkResponse]
    backups: int


class OwnerPlanResponse(BaseModel):
    """
    What fix-owner would change, or changed.

    Attributes:
        current_owner: The owner before the change.
        new_owner: The owner it is given.
        objects: The objects re-owned with it, ``KIND schema.name``.
        statements: The exact statements.
        applied: Whether they ran.
    """

    database: str
    current_owner: str | None = None
    new_owner: str
    objects: list[str]
    statements: list[str]
    applied: bool


class FixOwnerRequest(BaseModel):
    """
    Request to give a database to its application's role.

    Attributes:
        owner: The role; the one Noust provisioned for it when omitted.
    """

    owner: str | None = Field(default=None, description="Role that must own the database")


class ProvisioningPlanResponse(BaseModel):
    """
    What creating a database for an application would write.

    Attributes:
        url: The connection string, password masked.
        database_exists: A database of that name is already on the engine.
        version: The version asked for, when one was.
        running_version: The version of the installed engine.
        will_install: The engine is not installed, and creating the database
            with ``install`` would install it in that version first.
    """

    engine: str
    display_name: str
    installed: bool
    running: bool
    database: str
    username: str
    env_vars: list[str]
    url: str
    database_exists: bool
    version: str | None = None
    running_version: str | None = None
    will_install: bool = False


class ChoiceVersionResponse(BaseModel):
    """
    One version of an engine a database can be created on.

    Attributes:
        version: The version (a PostgreSQL major, a MariaDB release).
        state: ``installed`` (it is the one that runs), ``installable`` (it can
            be installed now) or ``unavailable`` (it cannot be had: another
            version of the engine, or another flavour, is installed).
        default: Whether it is what installs when no version is chosen.
    """

    version: str
    state: str
    default: bool = False


class EngineChoiceResponse(BaseModel):
    """
    One engine a database can be created on, and its versions.

    Attributes:
        flavour: ``postgresql``, ``mysql``, ``mariadb``, ``redis``, ``valkey``
            or ``mongodb``; what ``engine`` takes in ``POST
            /api/apps/{domain}/databases`` besides the engine's own name.
        engine: The manager that runs it.
        display_name: How it is named to a person.
        installed: This flavour is the one installed.
        running_version: The version that runs, when installed.
        installable: It can be installed now.
        reason: Why not, or what is installed, as one sentence.
        versions: The versions on offer, oldest first.
    """

    flavour: str
    engine: str
    display_name: str
    installed: bool
    running_version: str | None = None
    installable: bool
    reason: str | None = None
    versions: list[ChoiceVersionResponse]


class EngineChoicesResponse(BaseModel):
    """The engines and versions a new database can be created on."""

    choices: list[EngineChoiceResponse]


@router.get("/databases", response_model=DatabaseListResponse)
def list_databases(
    session: Annotated[dict, Depends(get_current_session)],
    engine: Annotated[str | None, Query(description="Restrict to one engine")] = None,
    team: Annotated[str | None, Query(description="Only this team's databases")] = None,
    project: Annotated[str | None, Query(description="Only this project's databases")] = None,
    environment: Annotated[
        str | None, Query(description="Only this environment's databases")
    ] = None,
) -> DatabaseListResponse:
    """
    List databases across the running engines, joined with the store.

    Args:
        engine: Engine to restrict the listing to.
        session: The authenticated session.
        team: Only this team's databases.
        project: Only the projects with this slug.
        environment: Only the environments with this slug.

    Returns:
        Every database the running engines report, the tracked ones they
        no longer have (``missing``), and the engines that could not be read
        (``problems``). A team member sees the databases its teams own: one an
        engine holds that Noust never recorded belongs to nobody.
    """
    listing = service(session).listing(engine)
    scoped = scoped_store()
    views = listing.databases
    # The store says whose a database is; an engine's own listing does not.
    if scoped.scope.restricted or team or project or environment:
        owned = {
            (record.name, record.engine)
            for record in scoped.list_databases(
                engine=DatabaseRegistry.canonical(engine) or engine if engine else None,
                project=project,
                environment=environment,
                team=team,
            )
        }
        views = [view for view in views if (view.name, view.engine) in owned]
    return DatabaseListResponse(
        databases=[database_response(view) for view in views],
        total=len(views),
        problems=[ListingProblemResponse(**problem.to_dict()) for problem in listing.problems],
    )


@router.post("/databases", response_model=DatabaseInfoResponse)
def create_database(
    request: CreateDatabaseRequest,
    http_request: Request,
    session: Annotated[dict, Depends(get_current_session)],
) -> DatabaseInfoResponse:
    """
    Create a database and record it in the store.

    It belongs to the application it is created for, and to the project the
    request names when it has none.

    Args:
        request: The create request.
        http_request: The incoming request, for the audit record of a refusal.
        session: The authenticated session.

    Returns:
        The new database.

    Raises:
        DatabaseExistsError: When the database already exists.
        ValidationError: 400 when the application is not one the caller sees.
    """
    if request.app is not None and scoped_store().get_app(request.app.strip().lower()) is None:
        raise ValidationError(
            f"Application not found: {request.app}",
            details="Name an application you can see (GET /api/apps), or leave app out.",
            field="app",
        )
    target = resolve_target_for(
        http_request,
        session,
        team=request.team,
        project=request.project,
        environment=request.environment,
        permission=Permission.DATABASES_WRITE,
    )
    with ownership_target(target.project_id, target.environment_id):
        view = service(session).create(
            request.engine,
            request.name,
            owner=request.owner,
            encoding=request.encoding,
            domain=request.app,
        )
    return database_response(view)


@router.post("/databases/adopt", response_model=AdoptResponse)
def adopt_databases(
    request: AdoptRequest, session: Annotated[dict, Depends(get_current_session)]
) -> AdoptResponse:
    """
    Record every database the engines hold and Noust does not track.

    Args:
        request: Optionally, one engine.
        session: The authenticated session.

    Returns:
        The databases adopted.
    """
    return AdoptResponse(adopted=service(session).adopt(request.engine))


@router.post("/databases/{engine}/{name}/links/detected", response_model=LinkResponse)
def record_detected_link(
    engine: str,
    name: str,
    request: RecordLinkRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> LinkResponse:
    """
    Record as a link a use Noust found in an application's environment.

    Nothing in the application changes: its ``.env`` already names the
    database.

    Args:
        engine: The engine key.
        name: The database.
        request: The application.
        session: The authenticated session.

    Returns:
        The recorded link.
    """
    view = service(session).record_detected_link(engine, name, request.app)
    return LinkResponse(**view.to_dict())


@router.get("/provisioning/choices", response_model=EngineChoicesResponse)
def provisioning_choices(
    session: Annotated[dict, Depends(get_current_session)],
) -> EngineChoicesResponse:
    """
    List the engines and versions a database can be created on.

    For the new-database choice: each flavour says whether it is installed and
    in which version, and each version whether it is the one that runs,
    could be installed now, or cannot be had. Nothing is changed.

    Args:
        session: The authenticated session.

    Returns:
        One entry per flavour, in the install catalog's order.
    """
    return EngineChoicesResponse(
        choices=[EngineChoiceResponse(**entry) for entry in service(session).engine_choices()]
    )


@router.get("/provisioning/plan", response_model=ProvisioningPlanResponse)
def provisioning_plan(
    session: Annotated[dict, Depends(get_current_session)],
    domain: Annotated[str, Query(description="The application's domain")],
    engine: Annotated[str, Query(description="The engine")],
    version: Annotated[
        str | None, Query(max_length=16, description="The version the database is wanted on")
    ] = None,
) -> ProvisioningPlanResponse:
    """
    Say what creating a database for an application would write.

    For the New-application wizard's Database step, before the application
    exists: nothing is created. Once it is deployed, ``POST
    /api/apps/{domain}/databases`` creates the database and links it.

    Args:
        session: The authenticated session.
        domain: The application's domain.
        engine: The engine.

    Returns:
        The plan.
    """
    return ProvisioningPlanResponse(**service(session).plan_for_app(domain, engine, version))


@router.get("/databases/{engine}/{name}", response_model=DatabaseInfoResponse)
def get_database_info(
    engine: str, name: str, session: Annotated[dict, Depends(get_current_session)]
) -> DatabaseInfoResponse:
    """
    Describe one database.

    Args:
        engine: Engine name.
        name: Database name.
        session: The authenticated session.

    Returns:
        The database.

    Raises:
        DatabaseNotFoundError: When no such database exists.
    """
    return database_response(service(session).get(engine, name))


@router.get("/databases/{engine}/{name}/overview", response_model=DatabaseOverviewResponse)
def get_database_overview(
    engine: str, name: str, session: Annotated[dict, Depends(get_current_session)]
) -> DatabaseOverviewResponse:
    """
    Everything the database page's Overview shows.

    Args:
        engine: Engine name.
        name: Database name.
        session: The authenticated session.

    Returns:
        The overview.
    """
    data: dict[str, Any] = service(session).overview(engine, name)
    return DatabaseOverviewResponse(
        database=DatabaseInfoResponse(**data["database"]),
        display_name=data["display_name"],
        port=data["port"],
        service=data["service"],
        capabilities=data["capabilities"],
        support=SupportNoticeResponse(**data["support"]),
        warnings=data["warnings"],
        access=[AccessEntryResponse(**entry) for entry in data["access"]],
        links=[LinkResponse(**link) for link in data["links"]],
        backups=data["backups"],
    )


@router.delete("/databases/{engine}/{name}", response_model=JobAcceptedResponse, status_code=202)
def drop_database(
    engine: str,
    name: str,
    session: Annotated[dict, Depends(require_elevated)],
    force: Annotated[bool, Query(description="Disconnect clients first")] = False,
    keep_backup: Annotated[bool, Query(description="Dump it before dropping it")] = True,
    unlink: Annotated[
        bool, Query(description="Also remove its variables from the applications using it")
    ] = False,
) -> JobAcceptedResponse:
    """
    Queue dropping a database, after its last dump, with sudo mode.

    A job: the last dump can take longer than a request may, and a central's
    proxy cuts a request at 300 seconds. A database an application uses is
    refused by the job unless ``unlink`` is set.

    Args:
        engine: Engine name.
        name: Database name.
        session: The authenticated, elevated session.
        force: Disconnect open sessions first.
        keep_backup: Dump it first.
        unlink: Remove its variables from the applications using it.

    Returns:
        The queued job.
    """
    manager = service(session).running(engine)
    name = manager.validate_database_name(name)
    return queue(
        session,
        job_type=JobType.DATABASE,
        name=f"Drop {name}",
        description=f"Dropping the {manager.DISPLAY_NAME} database {name}",
        func=jobs.drop_job,
        kwargs={
            "engine": manager.ENGINE_NAME,
            "name": name,
            "force": force,
            "keep_backup": keep_backup,
            "unlink": unlink,
        },
        metadata={"engine": manager.ENGINE_NAME, "database": name},
        message=f"Dropping {name}",
    )


@router.post("/databases/{engine}/{name}/forget", response_model=ActionResponse)
def forget_database(
    engine: str, name: str, session: Annotated[dict, Depends(require_elevated)]
) -> ActionResponse:
    """
    Forget a tracked database the engine no longer has, with sudo mode.

    Args:
        engine: Engine name.
        name: Database name.
        session: The authenticated, elevated session.

    Returns:
        The action outcome.
    """
    removed = service(session).forget(engine, name)
    return ActionResponse(
        success=removed,
        message=f"'{name}' forgotten" if removed else f"Noust did not track '{name}'",
    )


@router.get("/databases/{engine}/{name}/fix-owner", response_model=OwnerPlanResponse)
def preview_fix_owner(
    engine: str,
    name: str,
    session: Annotated[dict, Depends(get_current_session)],
    owner: Annotated[str | None, Query(description="Role that must own it")] = None,
) -> OwnerPlanResponse:
    """
    Show what giving a PostgreSQL database to its application's role changes.

    Args:
        engine: Engine name.
        name: Database name.
        session: The authenticated session.
        owner: The role; the provisioned one by default.

    Returns:
        The plan, not applied.
    """
    return OwnerPlanResponse(**service(session).fix_owner(engine, name, owner=owner).to_dict())


@router.post("/databases/{engine}/{name}/fix-owner", response_model=OwnerPlanResponse)
def apply_fix_owner(
    engine: str,
    name: str,
    request: FixOwnerRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> OwnerPlanResponse:
    """
    Give a PostgreSQL database, and what its owner holds in it, to another role.

    Explicit and with sudo mode: nothing changes a database's owner on its
    own. Run it again with the previous owner to put it back.

    Args:
        engine: Engine name.
        name: Database name.
        request: The role.
        session: The authenticated, elevated session.

    Returns:
        The plan, applied.
    """
    plan = service(session).fix_owner(engine, name, owner=request.owner, apply=True)
    return OwnerPlanResponse(**plan.to_dict())
