# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/views``: one route per resource, whether the Noust has a fleet or not.

A page that lists applications, databases, sites, certificates, backups, scheduled jobs, logs,
alert rules or activity asks one route and gets the rows of every server it may see, each with the
server it is on and its page there; the central is one more server (``@central``). Naming a server
(``node``) filters the rows and never moves the page. A Noust with no servers of its own to manage
answers the rows of itself and says ``fleet: false``, so a page draws no column and no filter for
it.

A thin layer (rule 3): the rows are :func:`noust.fleet.aggregate.gather`, which asks each node's
own API as the operator and the central's own API in-process; a node applies its own permissions,
ceiling and sudo mode, and nothing here can disagree with it. A server that refuses the operator
never fails the view: it is an outcome, and the servers that refused the same thing come grouped
(:mod:`noust.web.fleet_refusals`) with what is missing, as a sentence with a code.

The routes of ``/api/fleet`` that read the same things (``apps``, ``certificates``, ``backups``,
``activity``, ``logs``) are the same views, announced as going away.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from noust.core.exceptions import ValidationError
from noust.fleet.search import MAX_LIMIT, search
from noust.web.api.deps import CodedText, NoustErrorRoute
from noust.web.api.fleet import NodeOutcomeOut, ask_view
from noust.web.auth import require_auth

router = APIRouter(route_class=NoustErrorRoute)

Session = Annotated[dict, Depends(require_auth)]

NodeFilter = Annotated[
    list[str] | None,
    Query(
        description=(
            "Only these servers, repeated; '@central' is this central itself. Leave it out for "
            "every server"
        )
    ),
]
Refresh = Annotated[bool, Query(description="Ask every server again, ignoring the cache")]


class ViewServerOut(BaseModel):
    """
    One server the operator may filter by.

    Attributes:
        name: What the server is called (this central's own name for itself).
        selector: What to send as ``node`` to ask for it alone: ``@central`` for the central
            itself, the server's name for a node.
        local: It is this central.
    """

    name: str
    selector: str
    local: bool = False


class ViewNodeOut(NodeOutcomeOut):
    """
    How one server answered a view.

    Attributes:
        permission: The permission the server refused, when it refused one and the central's own
            map of routes names it.
        refusal: Why it refused, when it did: ``permission`` or ``other``.
    """

    permission: str | None = None
    refusal: str | None = None


class RefusalGroupOut(BaseModel):
    """
    The servers that refused the operator the same thing.

    Attributes:
        reason: ``permission`` (the operator's role does not hold one) or ``other`` (anything a
            server refuses for: its own words say what).
        code: The code the servers answered with, such as ``permission_denied``.
        permission: What is missing, for a refusal about a permission.
        resource: The view that was asked.
        servers: Their names, in the order they were asked.
        count: How many they are.
        includes_central: This central is one of them.
        message: What is missing, in English.
        hint: How to give it, in English.
        coded: The same sentence as a code and its values, for the console to translate; None
            when the words are the server's own.
    """

    reason: str
    code: str
    permission: str | None = None
    resource: str
    servers: list[str]
    count: int
    includes_central: bool = False
    message: str
    hint: str | None = None
    coded: CodedText | None = None


class ViewEnvelope(BaseModel):
    """
    What every view says besides its rows.

    Attributes:
        resource: Which view.
        generated_at: When it was put together.
        partial: Some server's answer is not fresh and complete.
        fleet: There are other servers to tell apart: a page draws the server column and the
            filter only when this is true.
        servers: Every server the operator may filter by, whatever the filter asked; empty
            without a fleet.
        nodes: How each server asked answered.
        refusals: The servers that refused the operator, grouped by what they refused.
    """

    resource: str
    generated_at: str
    partial: bool
    fleet: bool
    servers: list[ViewServerOut]
    nodes: list[ViewNodeOut]
    refusals: list[RefusalGroupOut]


class ViewOut(ViewEnvelope):
    """
    One view: the rows of every server asked.

    Attributes:
        items: The rows, as each server lists the resource, each with ``node`` (the server),
            ``local`` (it is this central), ``page`` (its page on that server's console) and
            ``href`` (the same page through this central's console).
    """

    items: list[dict[str, Any]]


class SearchHitOut(BaseModel):
    """
    One thing a search found, on one server.

    Attributes:
        kind: ``server``, ``app``, ``database``, ``site``, ``certificate`` or ``cron``.
        name: What it is called.
        detail: A second identifier worth showing beside the name (an engine, a web server, a
            version); a value, never a sentence.
        node: The server it is on.
        local: That server is this central.
        page: Its page on that server's console.
        href: The same page through this central's console.
        labels: A server's labels, for a hit that is a server.
    """

    kind: str
    name: str
    detail: str | None = None
    node: str
    local: bool = False
    page: str
    href: str
    labels: dict[str, str] | None = None


class SearchOut(ViewEnvelope):
    """
    What a search found across the fleet, best first.

    Attributes:
        query: What was asked.
        total: How many things matched.
        truncated: There are more matches than ``items`` carries.
        items: The hits.
    """

    query: str
    total: int
    truncated: bool
    items: list[SearchHitOut] = Field(default_factory=list)


async def _view(
    resource: str,
    request: Request,
    session: Mapping[str, Any],
    node: list[str] | None,
    refresh: bool,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Answer one view.

    Args:
        resource: The aggregator's view.
        request: The request.
        session: The authenticated payload.
        node: Only these servers.
        refresh: Ask again.
        params: The view's parameters.

    Returns:
        The envelope and the rows.
    """
    _, body = await ask_view(resource, request, session, node, refresh, params)
    return body


@router.get("/apps", response_model=ViewOut)
async def view_apps(
    request: Request, session: Session, node: NodeFilter = None, refresh: Refresh = False
) -> dict[str, Any]:
    """
    The applications of every server, as each server lists them.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.

    Returns:
        One row per application, with its server and its page there.
    """
    return await _view("apps", request, session, node, refresh)


@router.get("/databases", response_model=ViewOut)
async def view_databases(
    request: Request, session: Session, node: NodeFilter = None, refresh: Refresh = False
) -> dict[str, Any]:
    """
    The databases of every server, as each server lists them.

    An engine a server could not read is a warning of that server in ``nodes``, not a missing
    row: a database that is not listed and an engine nobody could ask are not the same thing.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.

    Returns:
        One row per database, with its server and its page there.
    """
    return await _view("databases", request, session, node, refresh)


@router.get("/sites", response_model=ViewOut)
async def view_sites(
    request: Request, session: Session, node: NodeFilter = None, refresh: Refresh = False
) -> dict[str, Any]:
    """
    The sites of every server's web server: the names each answers on.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.

    Returns:
        One row per site, with its server and its page there.
    """
    return await _view("sites", request, session, node, refresh)


@router.get("/certificates", response_model=ViewOut)
async def view_certificates(
    request: Request, session: Session, node: NodeFilter = None, refresh: Refresh = False
) -> dict[str, Any]:
    """
    The certificates of every server, the soonest to expire first.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.

    Returns:
        One row per certificate.
    """
    return await _view("certificates", request, session, node, refresh)


@router.get("/backups", response_model=ViewOut)
async def view_backups(
    request: Request, session: Session, node: NodeFilter = None, refresh: Refresh = False
) -> dict[str, Any]:
    """
    Every application's backups, the gaps first: none, old, unverified, unscheduled.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.

    Returns:
        One row per application: its newest backup, whether it verified, its schedule.
    """
    return await _view("backups", request, session, node, refresh)


@router.get("/cron", response_model=ViewOut)
async def view_cron(
    request: Request, session: Session, node: NodeFilter = None, refresh: Refresh = False
) -> dict[str, Any]:
    """
    The scheduled jobs of every server, with their next run and last result.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.

    Returns:
        One row per job.
    """
    return await _view("cron", request, session, node, refresh)


@router.get("/logs", response_model=ViewOut)
async def view_logs(
    request: Request,
    session: Session,
    node: NodeFilter = None,
    refresh: Refresh = False,
    q: Annotated[
        str, Query(max_length=1000, description="The search: words, fields, -excluded, a OR b")
    ] = "",
    since: Annotated[
        str | None, Query(description="Start: 15m, 2h, 7d, a date, epoch seconds")
    ] = None,
    until: Annotated[str | None, Query(description="End, in the same forms")] = None,
    level: Annotated[str | None, Query(description="The worst level kept")] = None,
    limit: Annotated[int, Query(ge=1, le=500, description="Lines per server")] = 100,
) -> dict[str, Any]:
    """
    Search the logs of the applications of every server at once, newest first.

    Each server decides what this operator may read of its logs and judges the query and the
    window itself; a server that cannot search is a note on the view, not an error.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.
        q: The search.
        since: Start of the window.
        until: End of the window.
        level: The worst level kept.
        limit: Lines per server.

    Returns:
        One row per line (``type: line``) and one per note (``type: note``).
    """
    params: dict[str, Any] = {
        "q": q,
        "since": since,
        "until": until,
        "level": level,
        "limit": limit,
    }
    return await _view("logs", request, session, node, refresh, params)


@router.get("/alerts", response_model=ViewOut)
async def view_alerts(
    request: Request, session: Session, node: NodeFilter = None, refresh: Refresh = False
) -> dict[str, Any]:
    """
    The alert rules of every server, the ones firing first.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.

    Returns:
        One row per rule, with ``firing`` and where each of its subjects stands.
    """
    return await _view("alerts", request, session, node, refresh)


@router.get("/activity", response_model=ViewOut)
async def view_activity(
    request: Request,
    session: Session,
    node: NodeFilter = None,
    refresh: Refresh = False,
    limit: Annotated[int, Query(ge=1, le=200, description="Events per server")] = 50,
) -> dict[str, Any]:
    """
    What happened lately on every server, newest first.

    Each server's audit log decides what this operator may read of it. The servers that do not
    let the operator's role read it come grouped in ``refusals`` (how many, which, what is
    missing, how to give it) and are ``forbidden`` in ``nodes``.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.
        limit: Events per server.

    Returns:
        One row per event.
    """
    return await _view("activity", request, session, node, refresh, {"limit": limit})


@router.get("/search", response_model=SearchOut)
async def view_search(
    request: Request,
    session: Session,
    q: Annotated[
        str, Query(min_length=1, max_length=200, description="Words to find, all of them")
    ],
    node: NodeFilter = None,
    refresh: Refresh = False,
    kind: Annotated[
        list[Literal["server", "app", "database", "site", "certificate", "cron"]] | None,
        Query(description="Only these kinds of thing, repeated"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT, description="The most things to return")] = 50,
) -> dict[str, Any]:
    """
    Find a server, application, database, site, certificate or scheduled job on any server.

    Every word has to be found in the name, in what else the record says (its owner, its source,
    its labels, the names a site answers on) or in the name of the server it is on; a name that is
    the query comes first. A server that is down is still found by its name.

    Args:
        request: The request.
        session: The authenticated session.
        q: The words to find.
        node: Only these servers.
        refresh: Ask again.
        kind: Only these kinds of thing.
        limit: The most things to return.

    Returns:
        The hits, best first, and how many things matched.

    Raises:
        ValidationError: 400 when ``q`` is only spaces.
    """
    if not q.strip():
        raise ValidationError(
            "Give something to search for",
            details="Send one or more words in q.",
            field="q",
        )
    result, body = await ask_view("index", request, session, node, refresh)
    hits, total, truncated = search(result.items, q, kinds=kind, limit=limit)
    body.update({"query": q, "total": total, "truncated": truncated, "items": hits})
    return body
