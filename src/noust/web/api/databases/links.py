# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/apps/{domain}/databases``: the databases an application uses.

What the application's Database tab reads and does: list its databases,
create one for it, link an existing one, unlink one (and drop it, with sudo
mode), and show a connection string (sudo mode, audited). Linking,
unlinking and creating restart the application behind its health gate, so
they are jobs; an application that does not come up on the new variables gets
its previous environment back and the job fails with the gate's evidence.

Mounted under ``/api/apps`` beside the applications router, which owns no path
under ``/{domain}/databases``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from noust.web.api.auth import get_current_session
from noust.web.api.databases import jobs
from noust.web.api.databases.common import LinkResponse, queue, service
from noust.web.api.deps import (
    JobAcceptedResponse,
    NoustErrorRoute,
    ensure_elevated,
    require_elevated,
    strict_domain,
)
from noust.web.jobs import JobType

router = APIRouter(route_class=NoustErrorRoute)


class AppDatabasesResponse(BaseModel):
    """
    The databases an application uses.

    A database provisioned for it before 3.1 has no recorded variable: its
    ``env_var`` is empty and its ``url`` null.
    """

    domain: str
    databases: list[LinkResponse]


class ProvisionRequest(BaseModel):
    """
    Request to create a database for an application and link it.

    Attributes:
        name: The database; derived from the application when omitted
            (``<app>_db``), a slot number for Redis (0 by default).
        env_var: The variable; ``DATABASE_URL``, or ``REDIS_URL`` for Redis.
        extra_vars: Also write ``DB_HOST``, ``DB_PORT``, ``DB_NAME``,
            ``DB_USER`` and ``DB_PASSWORD``.
        restart: Restart the application on the new variables.
    """

    engine: str = Field(..., description="Database engine")
    name: str | None = Field(default=None, description="Database name")
    env_var: str | None = Field(default=None, description="Variable to write")
    extra_vars: bool = Field(default=False, description="Also write the DB_* variables")
    restart: bool = Field(default=True, description="Restart the application behind its gate")


class LinkRequest(BaseModel):
    """
    Request to link an existing database to an application.

    Attributes:
        username: The account to sign in as. Noust must know its password;
            when omitted, the provisioned account, or a new one for the
            application made the database's owner.
    """

    engine: str = Field(..., description="Database engine")
    database: str = Field(..., description="Database name")
    username: str | None = Field(default=None, description="Account to sign in as")
    env_var: str | None = Field(default=None, description="Variable to write")
    extra_vars: bool = Field(default=False, description="Also write the DB_* variables")
    restart: bool = Field(default=True, description="Restart the application behind its gate")


class ConnectionUrlResponse(BaseModel):
    """A linked database's connection string, with its password."""

    domain: str
    engine: str
    database: str
    url: str


@router.get("/{domain}/databases", response_model=AppDatabasesResponse)
def list_app_databases(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> AppDatabasesResponse:
    """
    List the databases an application uses, connection strings masked.

    Args:
        domain: The application.
        session: The authenticated session.

    Returns:
        Its databases.
    """
    domain = strict_domain(domain)
    return AppDatabasesResponse(
        domain=domain,
        databases=[
            LinkResponse(**view.to_dict()) for view in service(session).app_databases(domain)
        ],
    )


@router.post("/{domain}/databases", response_model=JobAcceptedResponse, status_code=202)
def provision_app_database(
    domain: str,
    request: ProvisionRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> JobAcceptedResponse:
    """
    Queue creating a database for an application and linking it.

    Args:
        domain: The application.
        request: What to create.
        session: The authenticated session.

    Returns:
        The queued job.
    """
    domain = strict_domain(domain)
    manager = service(session).running(request.engine)
    return queue(
        session,
        job_type=JobType.DATABASE,
        name=f"Create a {manager.DISPLAY_NAME} database for {domain}",
        description=f"Creating and linking a {manager.DISPLAY_NAME} database for {domain}",
        func=jobs.provision_job,
        kwargs={
            "domain": domain,
            "engine": manager.ENGINE_NAME,
            "name": request.name,
            "env_var": request.env_var,
            "extra_vars": request.extra_vars,
            "restart": request.restart,
        },
        metadata={"domain": domain, "engine": manager.ENGINE_NAME},
        message=f"Creating a {manager.DISPLAY_NAME} database for {domain}",
    )


@router.post("/{domain}/databases/link", response_model=JobAcceptedResponse, status_code=202)
def link_app_database(
    domain: str,
    request: LinkRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> JobAcceptedResponse:
    """
    Queue linking an existing database to an application.

    Args:
        domain: The application.
        request: What to link.
        session: The authenticated session.

    Returns:
        The queued job.
    """
    domain = strict_domain(domain)
    manager = service(session).running(request.engine)
    database = manager.validate_database_name(request.database)
    return queue(
        session,
        job_type=JobType.DATABASE,
        name=f"Link {database} to {domain}",
        description=f"Linking the {manager.DISPLAY_NAME} database {database} to {domain}",
        func=jobs.link_job,
        kwargs={
            "domain": domain,
            "engine": manager.ENGINE_NAME,
            "database": database,
            "username": request.username,
            "env_var": request.env_var,
            "extra_vars": request.extra_vars,
            "restart": request.restart,
        },
        metadata={"domain": domain, "engine": manager.ENGINE_NAME, "database": database},
        message=f"Linking {database} to {domain}",
    )


@router.delete(
    "/{domain}/databases/{engine}/{name}", response_model=JobAcceptedResponse, status_code=202
)
def unlink_app_database(
    domain: str,
    engine: str,
    name: str,
    http_request: Request,
    session: Annotated[dict, Depends(get_current_session)],
    drop: Annotated[bool, Query(description="Also drop the database, after its last dump")] = False,
    restart: Annotated[bool, Query(description="Restart the application behind its gate")] = True,
) -> JobAcceptedResponse:
    """
    Queue unlinking a database from an application; dropping it needs sudo mode.

    Args:
        domain: The application.
        engine: Engine name.
        name: Database name.
        http_request: The request, for the elevation check's audit record.
        session: The authenticated session.
        drop: Drop the database too.
        restart: Restart the application without the variables.

    Returns:
        The queued job.
    """
    if drop:
        ensure_elevated(http_request, session)
    domain = strict_domain(domain)
    manager = service(session).manager(engine)
    name = manager.validate_database_name(name)
    return queue(
        session,
        job_type=JobType.DATABASE,
        name=f"Unlink {name} from {domain}",
        description=f"Unlinking the {manager.DISPLAY_NAME} database {name} from {domain}"
        + (" and dropping it" if drop else ""),
        func=jobs.unlink_job,
        kwargs={
            "domain": domain,
            "engine": manager.ENGINE_NAME,
            "database": name,
            "drop": drop,
            "restart": restart,
        },
        metadata={"domain": domain, "engine": manager.ENGINE_NAME, "database": name},
        message=f"Unlinking {name} from {domain}",
    )


@router.post("/{domain}/databases/{engine}/{name}/url", response_model=ConnectionUrlResponse)
def reveal_app_database_url(
    domain: str,
    engine: str,
    name: str,
    session: Annotated[dict, Depends(require_elevated)],
) -> ConnectionUrlResponse:
    """
    Show a linked database's connection string with its password.

    Sudo mode, and audited as a secret shown.

    Args:
        domain: The application.
        engine: Engine name.
        name: Database name.
        session: The authenticated, elevated session.

    Returns:
        The connection string.
    """
    domain = strict_domain(domain)
    url = service(session).reveal_url(domain, engine, name)
    return ConnectionUrlResponse(domain=domain, engine=engine, database=name, url=url)
