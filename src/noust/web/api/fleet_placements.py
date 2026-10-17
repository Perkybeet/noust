# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/fleet/placements``: an application that runs on several servers.

A thin layer (rule 3). The record is :class:`noust.fleet.placements.Placements`, how the application
looks across its servers is :func:`noust.fleet.placement_status.placement_status`, what a load
balancer in front of them needs is :mod:`noust.fleet.placement_upstreams`, and rolling out and copying are
bulk actions of the fleet engine (:mod:`noust.web.fleet_placement_actions`): they are planned and
queued exactly as ``POST /api/fleet/actions`` plans and queues the others, so one confirmation of sudo mode
covers a job and every server answers for the operator with its own permissions.

Nothing here changes a server except by queueing one of those actions: recording where an application
runs, or forgetting it, touches only the central.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from noust.core.audit import record as record_audit
from noust.web.api.deps import NoustErrorRoute
from noust.web.api.fleet import ActionOut, Session, _plan_and_start, asker_for
from noust.web.auth import actor_label

router = APIRouter(route_class=NoustErrorRoute)


class PlacementIn(BaseModel):
    """
    Where an application runs, and how an update goes out over its servers.

    The servers (``nodes`` and ``labels``) are kept when not sent; the way an update goes out is
    what is sent, so a form that shows all of it can also clear a field.

    Attributes:
        nodes: The servers it is named on; keeps the current ones when absent.
        labels: A label selector: every server carrying all of its pairs is a member too;
            keeps the current one when absent.
        serial: Servers an update goes to at a time (a number or ``"25%"``); one at a time
            when null.
        max_failures: Failures that stop an update (0 stops at the first); the first when null.
        canary: The server that goes first and alone; the first member when null.
        rollback_on_failure: Put the servers already moved back when an update stops.
    """

    nodes: list[str] | None = None
    labels: dict[str, str] | None = None
    serial: int | str | None = None
    max_failures: int | None = None
    canary: str | None = None
    rollback_on_failure: bool = True


class PlacementOut(BaseModel):
    """An application placed on several servers."""

    domain: str
    nodes: list[str]
    labels: dict[str, str]
    members: list[str] = Field(description="Every server it runs on now: named, or by label")
    serial: int | str | None = None
    max_failures: int | None = None
    canary: str | None = None
    rollback_on_failure: bool
    created_at: str
    created_by: str | None = None
    updated_at: str


class PlacementListOut(BaseModel):
    """Every placement."""

    placements: list[PlacementOut]


class ServerStatusOut(BaseModel):
    """How the application looks on one of its servers."""

    node: str
    reachable: bool
    present: bool
    status: str | None = None
    commit: str | None = None
    deployed_at: str | None = None
    branch: str | None = None
    layout: str | None = None
    sync: str = Field(
        description="current, differs, unknown (no deployment to compare), missing or unreachable"
    )
    stale: bool = False
    message: str | None = None
    href: str


class PlacementStatusOut(BaseModel):
    """The application compared across its servers."""

    domain: str
    members: list[str]
    reference_commit: str | None = Field(
        description="The commit most servers serve; on a tie, the one deployed last"
    )
    in_sync: bool = Field(description="Every server there, answering, running and on the reference")
    servers: list[ServerStatusOut]


class PlacementDetailOut(BaseModel):
    """A placement and how its application looks now."""

    placement: PlacementOut
    status: PlacementStatusOut


class UpstreamServerOut(BaseModel):
    """One server, as a load balancer sees it."""

    node: str
    address: str | None = None
    serving: bool
    sync: str | None = None
    commit: str | None = None
    note: str | None = None


class UpstreamsOut(BaseModel):
    """The servers an application runs on, written for the load balancer in front of them."""

    domain: str
    format: str
    port: int
    text: str
    servers: list[UpstreamServerOut]


class RolloutIn(BaseModel):
    """
    Roll the application out over its servers.

    Attributes:
        commit: Deploy exactly this commit everywhere (7 to 40 hex characters).
        tag: Deploy the commit this tag points at.
        image_tag: For an application that runs a registry image: the tag every server pulls.
        force: Rebuild even a server already on it.
        rollback_on_failure: Put the servers already moved back if it stops; the
            placement's own setting when absent.
        serial: Servers at a time; the placement's own when absent.
        max_failures: Failures that stop it; the placement's own when absent.
        canary: The server that goes first; the placement's own when absent.
        plan: Only say what would happen.
    """

    commit: str | None = None
    tag: str | None = None
    image_tag: str | None = None
    force: bool = False
    rollback_on_failure: bool | None = None
    serial: int | str | None = None
    max_failures: int | None = None
    canary: str | None = None
    plan: bool = False


class CopyIn(BaseModel):
    """
    Put the application on servers that do not have it.

    Attributes:
        from_node: The server to copy it from; a member that serves it, by default.
        to: The servers to copy it to; the members that lack it, by default.
        new_domain: Make the copy under another domain.
        secrets: Carry the secret values with it.
        match_commit: Bring each copy to the commit the source serves.
        plan: Only say what would happen.
    """

    from_node: str | None = None
    to: list[str] | None = None
    new_domain: str | None = None
    secrets: bool = True
    match_commit: bool = True
    plan: bool = False


class ShipIn(BaseModel):
    """
    Build the application on one server and run that build on the placement's servers.

    Attributes:
        builder: The server that builds; the one recorded for the application when absent.
        to: The servers that receive the release; every member of the placement when absent.
        commit: Build exactly this commit (7 to 40 hex characters) instead of the branch head.
        keep: Keep the release the build made on the builder.
        schema_changed_ok: Confirm taking a release older than a deployment that changed the
            database's schema, on the servers that would refuse it.
        frozen_ok: Confirm switching although deploys are frozen.
        force: Send it even to a server already on its commit.
        rollback_on_failure: Put the servers already moved back if it stops; the placement's own
            setting when absent.
        serial: Servers at a time; the placement's own when absent.
        max_failures: Failures that stop it; the placement's own when absent.
        canary: The server that goes first; the placement's own when absent.
        plan: Only say what would happen.
    """

    builder: str | None = None
    to: list[str] | None = None
    commit: str | None = None
    keep: bool = False
    schema_changed_ok: bool = False
    frozen_ok: bool = False
    force: bool = False
    rollback_on_failure: bool | None = None
    serial: int | str | None = None
    max_failures: int | None = None
    canary: str | None = None
    plan: bool = False


def _describe(placement: Any, members: list[str]) -> PlacementOut:
    """
    Describe a placement for the API.

    Args:
        placement: The placement.
        members: The servers it runs on now.

    Returns:
        Its description.
    """
    data = placement.to_dict()
    return PlacementOut(
        domain=data["domain"],
        nodes=data["nodes"],
        labels=data["selector"],
        members=members,
        serial=data["serial"],
        max_failures=data["max_failures"],
        canary=data["canary"],
        rollback_on_failure=data["rollback_on_failure"],
        created_at=data["created_at"],
        created_by=data["created_by"],
        updated_at=data["updated_at"],
    )


def _known(domain: str) -> tuple[Any, Any]:
    """
    Find a placement by its domain, or answer 404.

    Args:
        domain: The application.

    Returns:
        The placement and the node registry.

    Raises:
        HTTPException: 404 when the application is not placed.
    """
    from noust.fleet.nodes import NodeManager
    from noust.fleet.placements import Placements, checked_domain

    manager = NodeManager()
    placement = Placements(manager.store).get(checked_domain(domain))
    if placement is None:
        raise HTTPException(status_code=404, detail=f"No placement for {domain}")
    return placement, manager


@router.get("", response_model=PlacementListOut)
def list_placements(session: Session) -> PlacementListOut:
    """
    List the applications that run on several servers.

    Asks no server: it is the central's own record. Use the status of one for how it looks.

    Args:
        session: The authenticated session.

    Returns:
        Every placement, with the servers it runs on now.
    """
    from noust.fleet.nodes import NodeManager
    from noust.fleet.placements import Placements

    manager = NodeManager()
    placements = Placements(manager.store)
    records = manager.list()
    return PlacementListOut(
        placements=[_describe(p, placements.members(p, records)) for p in placements.list()]
    )


@router.put("/{domain}", response_model=PlacementOut)
def save_placement(domain: str, body: PlacementIn, session: Session) -> PlacementOut:
    """
    Record where an application runs, or change it; what is not sent is kept.

    Touches no server.

    Args:
        domain: The application.
        body: The servers, the label and the way an update goes out.
        session: The authenticated session.

    Returns:
        The placement as it is now.

    Raises:
        ValidationError: Nothing says where it runs, or a value is invalid.
        NodeError: A named server is not registered.
    """
    from noust.fleet.nodes import NodeManager
    from noust.fleet.placements import Placements, checked_domain

    manager = NodeManager()
    placements = Placements(manager.store)
    saved = placements.save(
        checked_domain(domain),
        nodes=body.nodes,
        selector=body.labels,
        serial=body.serial,
        max_failures=body.max_failures,
        canary=body.canary,
        rollback_on_failure=body.rollback_on_failure,
        actor=actor_label(session),
    )
    record_audit(
        "fleet.placement.save",
        target=f"app:{saved.domain}",
        details={"nodes": list(saved.nodes), "labels": saved.selector},
    )
    return _describe(saved, placements.members(saved, manager.list()))


@router.delete("/{domain}", response_model=PlacementOut)
def remove_placement(domain: str, session: Session) -> PlacementOut:
    """
    Forget where an application runs. Nothing is touched on any server.

    Args:
        domain: The application.
        session: The authenticated session.

    Returns:
        The placement that was forgotten.

    Raises:
        HTTPException: 404 when the application is not placed.
    """
    placement, manager = _known(domain)
    members = _members(placement, manager)
    from noust.fleet.placements import Placements

    Placements(manager.store).remove(placement.domain)
    record_audit("fleet.placement.remove", target=f"app:{placement.domain}")
    return _describe(placement, members)


def _members(placement: Any, manager: Any) -> list[str]:
    """
    Name the servers a placement runs on now.

    Args:
        placement: The placement.
        manager: The node registry.

    Returns:
        Their names.
    """
    from noust.fleet.placements import Placements

    return Placements(manager.store).members(placement, manager.list())


def _status(
    placement: Any, manager: Any, session: Mapping[str, Any], refresh: bool
) -> dict[str, Any]:
    """
    Compare the application across its servers.

    Args:
        placement: The placement.
        manager: The node registry.
        session: The authenticated payload.
        refresh: Ask even the servers whose answer is fresh.

    Returns:
        :func:`~noust.fleet.placement_status.placement_status`'s answer.
    """
    from noust.fleet.placement_status import placement_status

    return placement_status(placement, manager=manager, asker=asker_for(session), refresh=refresh)


@router.get("/{domain}", response_model=PlacementDetailOut)
async def get_placement(
    domain: str,
    session: Session,
    refresh: Annotated[bool, Query(description="Ask the servers again")] = False,
) -> dict[str, Any]:
    """
    Show an application across its servers: which commit each serves and who differs.

    Args:
        domain: The application.
        session: The authenticated session.
        refresh: Ask even the servers whose answer is fresh.

    Returns:
        ``placement`` and ``status``: the reference commit, whether they are in sync, and a
        row per server.

    Raises:
        HTTPException: 404 when the application is not placed.
    """
    placement, manager = _known(domain)
    status = await run_in_threadpool(_status, placement, manager, session, refresh)
    return {"placement": _describe(placement, status["members"]), "status": status}


@router.get("/{domain}/upstreams", response_model=UpstreamsOut)
async def get_upstreams(
    domain: str,
    session: Session,
    format: Annotated[str, Query(description="nginx, list or json")] = "nginx",
    port: Annotated[int, Query(ge=1, le=65535, description="The servers' web port")] = 443,
) -> dict[str, Any]:
    """
    Write the servers an application runs on for the load balancer in front of them.

    Noust routes no traffic between servers: the balancer is the operator's. This is the list of
    servers to put behind it, with the ones that do not serve right now marked down.

    Args:
        domain: The application.
        session: The authenticated session.
        format: ``nginx`` (an ``upstream`` block), ``list`` (``address:port`` per line) or ``json``.
        port: The port the servers' web server answers on.

    Returns:
        The text, and the servers it was written from.

    Raises:
        HTTPException: 404 when the application is not placed.
        ValidationError: An unknown format.
    """
    from noust.fleet.placement_upstreams import render_upstreams, upstream_servers

    placement, manager = _known(domain)
    status = await run_in_threadpool(_status, placement, manager, session, False)
    addresses = {record.name: record.ssh_host for record in manager.list()}
    servers = upstream_servers(status, addresses, port=port)
    text = render_upstreams(placement.domain, servers, format)
    return {
        "domain": placement.domain,
        "format": format,
        "port": port,
        "text": text,
        "servers": servers,
    }


@router.post("/{domain}/rollout", response_model=ActionOut)
async def rollout(
    domain: str, request: Request, body: RolloutIn, session: Session
) -> dict[str, Any]:
    """
    Roll the application out over its servers: one commit everywhere, put back if it stops.

    The first server goes alone and builds the head of the branch (or the commit or tag asked
    for); the commit it ends on is what the others are asked for. Planned and queued like a
    bulk action: sudo mode when a server's schema asks for it, a job to follow, a record per server.

    Args:
        domain: The application.
        request: The request.
        body: What to deploy, and how; the placement's own strategy where absent.
        session: The authenticated session.

    Returns:
        The plan and, unless ``plan``, the job.

    Raises:
        HTTPException: 404 when the application is not placed.
        ValidationError: A server does not run the application, or a value is invalid.
    """
    from noust.web.fleet_placement_actions import rollout_request

    placement, manager = _known(domain)
    fleet_request = rollout_request(
        placement,
        _members(placement, manager),
        commit=body.commit,
        tag=body.tag,
        image_tag=body.image_tag,
        force=body.force,
        rollback_on_failure=body.rollback_on_failure,
        serial=body.serial,
        max_failures=body.max_failures,
        canary=body.canary,
    )
    return await _plan_and_start(request, session, fleet_request, body.plan)


@router.post("/{domain}/ship", response_model=ActionOut)
async def ship(domain: str, request: Request, body: ShipIn, session: Session) -> dict[str, Any]:
    """
    Build the application on one server and run that build on the placement's servers.

    The builder builds the release once, in its sandbox, without activating it; the central carries
    the bundle through the tunnels, in chunks, to each server, which checks it against the digest
    the builder gave and activates it behind the health check, after the application's own hooks.
    The first server goes alone; the ones already moved are put back if it stops. Planned and
    queued like a bulk action: a job to follow, a record per server.

    Args:
        domain: The application.
        request: The request.
        body: Who builds, who receives, and how; the placement's own strategy where absent.
        session: The authenticated session.

    Returns:
        The plan and, unless ``plan``, the job.

    Raises:
        HTTPException: 404 when the application is not placed.
        ValidationError: There is no builder, a server is not ready for it, or a value is invalid.
    """
    from noust.web.fleet_ship_actions import ship_request

    placement, manager = _known(domain)
    members = body.to if body.to is not None else _members(placement, manager)
    chosen = body.canary or (placement.canary if placement.canary in members else None)
    fleet_request = ship_request(
        placement.domain,
        list(members),
        builder=body.builder,
        commit=body.commit,
        keep=body.keep,
        schema_changed_ok=body.schema_changed_ok,
        frozen_ok=body.frozen_ok,
        force=body.force,
        rollback_on_failure=(
            placement.rollback_on_failure
            if body.rollback_on_failure is None
            else body.rollback_on_failure
        ),
        serial=body.serial if body.serial is not None else placement.serial,
        max_failures=body.max_failures if body.max_failures is not None else placement.max_failures,
        canary=chosen,
    )
    return await _plan_and_start(request, session, fleet_request, body.plan)


@router.post("/{domain}/copy", response_model=ActionOut)
async def copy_application(
    domain: str, request: Request, body: CopyIn, session: Session
) -> dict[str, Any]:
    """
    Put the application on servers that do not have it, from one that does.

    The source's own export (with the secret values unless told not to) is imported on each
    destination, then each is brought to the commit the source serves. The document passes through the
    central and is kept nowhere. Needs sudo mode: an import deploys as root.

    Args:
        domain: The application.
        request: The request.
        body: The source, the destinations (the members that lack it by default) and options.
        session: The authenticated session.

    Returns:
        The plan and, unless ``plan``, the job.

    Raises:
        HTTPException: 404 when the application is not placed.
        ValidationError: There is no server to copy it from or to.
    """
    from noust.web.fleet_placement_actions import copy_request

    placement, manager = _known(domain)
    status = (
        await run_in_threadpool(_status, placement, manager, session, False)
        if body.from_node is None or body.to is None
        else None
    )
    fleet_request = copy_request(
        placement,
        status,
        from_node=body.from_node,
        to=body.to,
        new_domain=body.new_domain,
        secrets=body.secrets,
        match_commit=body.match_commit,
    )
    return await _plan_and_start(request, session, fleet_request, body.plan)
