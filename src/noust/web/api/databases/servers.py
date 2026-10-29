# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/servers``: where databases run, as one list (4.1, item 103).

A **database server** is an engine in a version running somewhere on this server: the system's
own PostgreSQL, MySQL or MariaDB, Redis or Valkey, MongoDB; a container Noust runs beside it
(another version, or an engine the system lacks); or a container an application's stack runs.
This is the model the console draws, in two concepts: the databases an application uses
(``/api/databases/databases``) and the servers they run on (here). What is done *to* a server
stays with the routes that did it: ``/engines/{key}/...`` for the system's, ``/instances/...``
for the containers Noust runs. What this adds is what none of them says: one list, the default
of each engine, when each version's support ends, and how many databases each holds.

The count is the caller's: a member of a team counts the databases its teams own, never the
others'. Choosing the default of an engine is the server's decision (it decides where every
team's next database goes), so it asks for managing databases and is not a team's.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from noust.core.teams import scoped_store
from noust.managers.database.servers import DatabaseServers
from noust.managers.database.service import DatabaseView
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import service
from noust.web.api.databases.databases import ListingProblemResponse
from noust.web.api.deps import NoustErrorRoute

router = APIRouter(route_class=NoustErrorRoute)


class ServerResponse(BaseModel):
    """
    One database server.

    Attributes:
        key: What it is addressed by wherever an engine is named (``mysql`` for the system's,
            ``mysql@legacy`` for a container Noust runs, ``postgresql@shop.db`` for one an
            application's stack runs). It is the ``engine`` of every database on it.
        engine: The engine it speaks: ``postgresql``, ``mysql``, ``redis``, ``mongodb`` or
            ``clickhouse``.
        flavour: What it is: ``mariadb`` is an engine ``mysql``, ``valkey`` an engine ``redis``.
        display_name: The flavour for a person.
        version: Its version: what the engine reports for the system's, the tag it was made
            with for a container Noust runs, null for an application's (its image says it).
        runs_in: ``system``, ``noust_container`` (Noust starts, stops, upgrades and removes it)
            or ``app_container`` (an application's stack does; it is listed and nothing more).
        name: The instance's name for a container Noust runs (``legacy``); null for the others.
            ``/api/databases/instances/{name}`` takes it.
        state: ``running``, ``stopped``, ``creating``, ``upgrading``, ``removing``, or Docker's
            own word for a container that exists and is not running.
        running: It answers.
        port: The port it listens on: 127.0.0.1's for a container Noust runs.
        memory_limit_mb: A container Noust runs is held to this.
        image: The image of a container; null for the system's.
        digest: The digest a container Noust runs is pinned to.
        custom_image: The image is the operator's own, not the catalog's.
        app: The application whose stack runs it, for ``app_container``.
        support: Where its version stands in its project's support: ``supported``,
            ``ending_soon``, ``ended`` or ``unknown`` (no date is published).
        end_of_life: When the support ends or ended; null when none is published.
        created_at: When Noust made it (a container Noust runs).
        databases: How many databases it holds that the caller may see; null when it is not
            running or could not be read (``problems`` says why).
        default: It is the server a new database of its engine is made on.
        default_source: Why: ``chosen`` (an operator did), ``system`` (nobody chose and the
            system has the engine), ``only`` (the one there is) or ``oldest``; null when it is
            not the default.
        actions: What can be done to it now: ``start``, ``stop``, ``set_default``,
            ``clear_default`` (the choice can be taken back), ``other_version`` (make another
            version of its engine in a container: ``GET .../instances/catalog?engine=...``),
            ``upgrade`` and ``remove``. Empty for an application's container and for one being
            made or removed.
    """

    key: str
    engine: str
    flavour: str
    display_name: str
    version: str | None = None
    runs_in: str
    name: str | None = None
    state: str
    running: bool
    port: int | None = None
    memory_limit_mb: int | None = None
    image: str | None = None
    digest: str | None = None
    custom_image: bool = False
    app: str | None = None
    support: str | None = None
    end_of_life: str | None = None
    created_at: str | None = None
    databases: int | None = None
    default: bool = False
    default_source: str | None = None
    actions: list[str] = Field(default_factory=list)


class ServerListResponse(BaseModel):
    """
    Every database server of this server.

    Attributes:
        servers: The servers: the system's engines, then the containers by key.
        problems: What could not be asked (Docker not answering, an engine that refused Noust),
            so a short list is never taken for a server with nothing in it.
    """

    servers: list[ServerResponse]
    problems: list[ListingProblemResponse] = Field(default_factory=list)


def _servers(session: dict[str, Any]) -> DatabaseServers:
    """
    Args:
        session: The authenticated session, whose actor the audit trail names.

    Returns:
        The servers over the request's database service.
    """
    return DatabaseServers(service(session))


def _visible() -> Callable[[DatabaseView], bool] | None:
    """
    Returns:
        Which databases count towards what a server holds for this caller: all of them for
        someone who sees every team, else the ones its teams own (a database an engine holds and
        Noust never recorded belongs to nobody).
    """
    scoped = scoped_store()
    if not scoped.scope.restricted:
        return None
    owned = {(record.name, record.engine) for record in scoped.list_databases()}
    return lambda view: (view.name, view.engine) in owned


@router.get("/servers", response_model=ServerListResponse)
def list_servers(
    session: Annotated[dict, Depends(get_current_session)],
    engine: Annotated[
        str | None,
        Query(description="Only this engine (a flavour, an alias, or an engine's family)"),
    ] = None,
) -> ServerListResponse:
    """
    List the database servers: the system's engines and the containers, in one list.

    Args:
        session: The authenticated session.
        engine: Only the servers of this engine: ``mysql`` is MySQL and MariaDB, ``mariadb``
            only MariaDB.

    Returns:
        The servers with their default and their support, and what could not be read.
    """
    listing = _servers(session).all(engine=engine, visible=_visible())
    return ServerListResponse(
        servers=[ServerResponse(**server) for server in listing.servers],
        problems=[ListingProblemResponse(**problem.to_dict()) for problem in listing.problems],
    )


@router.get("/servers/{key}", response_model=ServerResponse)
def get_server(key: str, session: Annotated[dict, Depends(get_current_session)]) -> ServerResponse:
    """
    Describe one database server.

    Args:
        key: Its key (``mysql``, ``mysql@legacy``).
        session: The authenticated session.

    Returns:
        The server.

    Raises:
        DatabaseNotFoundError: 404 ``databaseerror`` with the code ``database_servers.unknown``
            when no server has that key.
        ValidationError: 400 when the key cannot be a server's.
    """
    return ServerResponse(**_servers(session).get(key, visible=_visible()))


@router.put("/servers/{key}/default", response_model=ServerResponse)
def set_default_server(
    key: str, session: Annotated[dict, Depends(get_current_session)]
) -> ServerResponse:
    """
    Make a server the one new databases of its engine are made on.

    The system's engine and the containers Noust runs can be it; an application's container
    cannot, nor one still being made. The choice replaces the one before it and is audited.

    Args:
        key: The server's key.
        session: The authenticated session.

    Returns:
        The server, now the default of its engine.

    Raises:
        DatabaseNotFoundError: 404 when no server has that key.
        ValidationError: 400 (``database_servers.default_app``,
            ``database_servers.default_not_ready``) when it cannot be the default.
    """
    return ServerResponse(**_servers(session).set_default(key))


@router.delete("/servers/{key}/default", response_model=ServerResponse)
def clear_default_server(
    key: str, session: Annotated[dict, Depends(get_current_session)]
) -> ServerResponse:
    """
    Take back the choice of a server as the default: its engine returns to the automatic one.

    A server that was not the chosen one is left as it is.

    Args:
        key: The server's key.
        session: The authenticated session.

    Returns:
        The server as it is now.

    Raises:
        DatabaseNotFoundError: 404 when no server has that key.
    """
    return ServerResponse(**_servers(session).clear_default(key))
