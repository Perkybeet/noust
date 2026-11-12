# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/views/docker``: the Docker zone of every server at once, with the server as a column.

A thin layer (rule 3) over :mod:`noust.fleet.docker`: each server answers its own
``/api/docker/...`` as the operator, with its own permissions, its ceiling for centrals and its own idea of
whose each object is, and the rows come back labelled with the server they are on. The envelope is the
one of every view (:class:`~noust.web.api.views.ViewOut`): ``fleet`` (there are other servers to tell
apart), ``servers``, how each server answered in ``nodes`` and the servers that refused the operator in
``refusals``.

A server that runs a Noust before 4.3 does not count Docker: its outcome is ``unsupported`` with the
code ``not_offered`` and its version, and the page says so in a sentence; it never fails the others. A
server with no Docker has no rows and a note in its outcome's ``warnings``.

The zone is the machine's, so a person of a team is refused all of it on the central (see ``scopes.py``).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from noust.fleet import docker as fleet_docker
from noust.web.api.deps import NoustErrorRoute
from noust.web.api.fleet import ask_view
from noust.web.api.views import NodeFilter, Refresh, Session, ViewOut

router = APIRouter(route_class=NoustErrorRoute)

_Limit = Annotated[int, Query(ge=1, le=fleet_docker.MAX_LIMIT, description="Rows per server")]
_Owner = Annotated[
    str | None,
    Query(description="Only rows of this kind of owner: app, database, noust, nobody"),
]
_Search = Annotated[str | None, Query(max_length=200, description="Only rows containing this text")]


async def _docker_view(
    kind: str,
    request: Request,
    session: Any,
    node: list[str] | None,
    refresh: bool,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Answer one view of the zone.

    Args:
        kind: The aggregator's view without its ``docker_`` prefix.
        request: The request.
        session: The authenticated payload.
        node: Only these servers.
        refresh: Ask again.
        params: The view's parameters.

    Returns:
        The envelope and the rows.
    """
    fleet_docker.register()
    _, body = await ask_view(f"docker_{kind}", request, session, node, refresh, params)
    return body


@router.get("/summary", response_model=ViewOut)
async def view_docker_summary(
    request: Request, session: Session, node: NodeFilter = None, refresh: Refresh = False
) -> dict[str, Any]:
    """
    One row per server: its Docker, its counters, its disk and what asks for attention there.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.

    Returns:
        One row per server: ``outcome``, ``available`` (null when the server did not answer),
        ``engine`` (``state``, ``version``, ``api``, ``compose``...), ``counts``, ``attention``, ``disk``
        and ``can_manage``.
    """
    return await _docker_view("summary", request, session, node, refresh)


@router.get("/attention", response_model=ViewOut)
async def view_docker_attention(
    request: Request, session: Session, node: NodeFilter = None, refresh: Refresh = False
) -> dict[str, Any]:
    """
    What asks for attention on every server, in one list, worst first.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.

    Returns:
        One row per thing: ``kind`` (``loop``, ``stopped``, ``update``, ``major``, ``disk``), ``subject``,
        ``owner``, ``facts`` as the server said them, and the server and its page.
    """
    return await _docker_view("attention", request, session, node, refresh)


@router.get("/engine", response_model=ViewOut)
async def view_docker_engine(
    request: Request, session: Session, node: NodeFilter = None, refresh: Refresh = False
) -> dict[str, Any]:
    """
    One row per server: which Docker it has and what does not fit in it.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.

    Returns:
        One row per server: ``outcome``, ``engine`` (its state and versions), and the doctor's ``facts``
        and ``limits`` as sentences with a code.
    """
    return await _docker_view("engine", request, session, node, refresh)


@router.get("/stacks", response_model=ViewOut)
async def view_docker_stacks(
    request: Request,
    session: Session,
    node: NodeFilter = None,
    refresh: Refresh = False,
    limit: _Limit = fleet_docker.DEFAULT_LIMIT,
    owner: _Owner = None,
    search: _Search = None,
) -> dict[str, Any]:
    """
    The Compose projects of every server, with their state and whose each is.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.
        limit: Rows per server.
        owner: Only this kind of owner.
        search: Only what contains this text.

    Returns:
        One row per stack, with its server.
    """
    return await _docker_view(
        "stacks",
        request,
        session,
        node,
        refresh,
        {"limit": limit, "owner": owner, "search": search},
    )


@router.get("/containers", response_model=ViewOut)
async def view_docker_containers(
    request: Request,
    session: Session,
    node: NodeFilter = None,
    refresh: Refresh = False,
    limit: _Limit = fleet_docker.DEFAULT_LIMIT,
    owner: _Owner = None,
    app: Annotated[str | None, Query(description="Only the containers of this application")] = None,
    state: Annotated[str | None, Query(description="Only this Docker state")] = None,
    project: Annotated[str | None, Query(description="Only this Compose project")] = None,
    search: _Search = None,
) -> dict[str, Any]:
    """
    The containers of every server, each with its owner, its state as one word and its server.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.
        limit: Rows per server.
        owner: Only this kind of owner.
        app: Only the containers of this application.
        state: Only this Docker state.
        project: Only this Compose project.
        search: Only what contains this text.

    Returns:
        One row per container, with its server.
    """
    return await _docker_view(
        "containers",
        request,
        session,
        node,
        refresh,
        {
            "limit": limit,
            "owner": owner,
            "app": app,
            "state": state,
            "project": project,
            "search": search,
        },
    )


@router.get("/images", response_model=ViewOut)
async def view_docker_images(
    request: Request,
    session: Session,
    node: NodeFilter = None,
    refresh: Refresh = False,
    limit: _Limit = fleet_docker.DEFAULT_LIMIT,
    owner: _Owner = None,
    search: _Search = None,
) -> dict[str, Any]:
    """
    The images of every server, each once with every name it has, who runs it and its server.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.
        limit: Rows per server.
        owner: Only this kind of owner.
        search: Only what contains this text.

    Returns:
        One row per image, with its server.
    """
    return await _docker_view(
        "images",
        request,
        session,
        node,
        refresh,
        {"limit": limit, "owner": owner, "search": search},
    )


@router.get("/volumes", response_model=ViewOut)
async def view_docker_volumes(
    request: Request,
    session: Session,
    node: NodeFilter = None,
    refresh: Refresh = False,
    limit: _Limit = fleet_docker.DEFAULT_LIMIT,
    owner: _Owner = None,
    search: _Search = None,
) -> dict[str, Any]:
    """
    The volumes of every server, with who owns and who mounts each, not measured, and its server.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.
        limit: Rows per server.
        owner: Only this kind of owner.
        search: Only what contains this text.

    Returns:
        One row per volume, with its server.
    """
    return await _docker_view(
        "volumes",
        request,
        session,
        node,
        refresh,
        {"limit": limit, "owner": owner, "search": search},
    )


@router.get("/networks", response_model=ViewOut)
async def view_docker_networks(
    request: Request,
    session: Session,
    node: NodeFilter = None,
    refresh: Refresh = False,
    limit: _Limit = fleet_docker.DEFAULT_LIMIT,
    owner: _Owner = None,
    search: _Search = None,
) -> dict[str, Any]:
    """
    The networks of every server, with who is on each and its server.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.
        limit: Rows per server.
        owner: Only this kind of owner.
        search: Only what contains this text.

    Returns:
        One row per network, with its server.
    """
    return await _docker_view(
        "networks",
        request,
        session,
        node,
        refresh,
        {"limit": limit, "owner": owner, "search": search},
    )
