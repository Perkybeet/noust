# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Cloudflare Tunnels as an application's way in, over the API.

A thin translation of :mod:`noust.managers.tunnels`, which ``noust tunnel`` calls too. A
tunnel is the server's, not an application's: it runs a connector as a unit, holds a token
and edits DNS, so creating, routing and deleting one asks for sudo mode, while looking at
what exists is a read. No token is ever in a response.

The manager answers every question (whether the connector is installed, whether a name
is the application's, whether the record in the way is somebody else's); a conflicting
record is not an error here but an outcome of the route, so the console can offer the
operator to confirm replacing exactly that record and ask again.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from noust.core.tunnel_store import Tunnel, TunnelRoute
from noust.managers.tunnels import (
    RouteResult,
    TunnelManager,
    TunnelResult,
    TunnelSupport,
)
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated
from noust.web.auth import actor_label

router = APIRouter(route_class=NoustErrorRoute)


class TunnelSupportModel(BaseModel):
    """
    Whether this server can run a tunnel, and what is missing.

    Attributes:
        available: Everything needed is in place.
        cloudflared: Where the connector is, when it is installed.
        sealed: The secret store is sealed, so a connector could not read its token at boot.
        accounts: The Cloudflare accounts that can manage a tunnel.
        problem: What is missing, when something is.
        hint: How to fix it.
    """

    available: bool
    cloudflared: str | None = None
    sealed: bool = False
    accounts: list[str] = Field(default_factory=list)
    problem: str | None = None
    hint: str | None = None


class TunnelRouteModel(BaseModel):
    """
    A public name a tunnel carries.

    Attributes:
        tunnel: The tunnel.
        hostname: The public name.
        service: Where the connector sends it.
        app_domain: The application it is for, when it is for one.
        created_at: When it was recorded.
    """

    tunnel: str
    hostname: str
    service: str
    app_domain: str | None = None
    created_at: str = ""


class TunnelModel(BaseModel):
    """
    A Cloudflare Tunnel.

    Attributes:
        name: What it is called.
        provider: The DNS account whose token manages it.
        account_id: The Cloudflare account it lives in.
        tunnel_id: Its id at Cloudflare.
        unit: The systemd unit that runs its connector.
        target: The name a public hostname's CNAME points at.
        state: ``creating``, ``ready`` or ``removing``.
        active: What systemd says of the connector.
        routes: The public names it carries.
        created_at: When it was recorded.
        created_by: Who asked.
    """

    name: str
    provider: str
    account_id: str
    tunnel_id: str
    unit: str
    target: str = ""
    state: str
    active: str = ""
    routes: list[TunnelRouteModel] = Field(default_factory=list)
    created_at: str = ""
    created_by: str | None = None


class TunnelListResponse(BaseModel):
    """
    Every tunnel, and whether another can be created.

    Attributes:
        tunnels: The tunnels, by name.
        support: What this server offers.
    """

    tunnels: list[TunnelModel]
    support: TunnelSupportModel


class CreateTunnelRequest(BaseModel):
    """
    A tunnel to create.

    Attributes:
        name: What to call it: lower case letters, digits and dashes.
        account: The DNS account to create it through; the only Cloudflare one by default.
        cloudflare_account: The Cloudflare account id, when the token reaches several.
    """

    name: str
    account: str | None = None
    cloudflare_account: str | None = None


class TunnelResultResponse(BaseModel):
    """
    What creating or deleting a tunnel did.

    Attributes:
        tunnel: The tunnel.
        warnings: Things the operator should know.
        released: What became of the records of its routes, when it was deleted.
    """

    tunnel: TunnelModel
    warnings: list[str]
    released: list[dict[str, Any]]


class AddRouteRequest(BaseModel):
    """
    A public name to carry through a tunnel.

    Attributes:
        hostname: The public name.
        app_domain: The application it is for: its site is reached through nginx.
        service: Where the connector sends it, when it is not an application's site.
        dns: Point the name's CNAME at the tunnel in the account that holds its zone.
        replace_record: The id of the record in the way the operator confirmed replacing.
        replace_current: What that record held when they confirmed.
    """

    hostname: str
    app_domain: str | None = None
    service: str | None = None
    dns: bool = True
    replace_record: str | None = None
    replace_current: str | None = None


class RouteResultResponse(BaseModel):
    """
    What adding or removing a route did.

    Attributes:
        tunnel: The tunnel.
        route: The route.
        ingress: What Cloudflare now holds for the tunnel.
        dns: What became of the name's CNAME (``outcome``, and for a ``conflict`` the
            ``record_id`` and ``current`` the operator confirms replacing).
    """

    tunnel: TunnelModel
    route: TunnelRouteModel
    ingress: list[dict[str, Any]]
    dns: dict[str, Any]


def _route(route: TunnelRoute) -> TunnelRouteModel:
    """
    Args:
        route: A route.

    Returns:
        The response model.
    """
    return TunnelRouteModel(**route.to_dict())


def _tunnel(manager: TunnelManager, tunnel: Tunnel, *, live: bool = True) -> TunnelModel:
    """
    Args:
        manager: The tunnel manager.
        tunnel: A tunnel.
        live: Ask systemd whether its connector runs.

    Returns:
        The response model, with its routes.
    """
    data = tunnel.to_dict()
    data["active"] = manager.active(tunnel) if live else ""
    data["routes"] = [_route(route) for route in manager.records.routes(tunnel.name)]
    return TunnelModel(**data)


def _support(support: TunnelSupport) -> TunnelSupportModel:
    """
    Args:
        support: What this server offers.

    Returns:
        The response model.
    """
    return TunnelSupportModel(**support.to_dict())


def _result(manager: TunnelManager, result: TunnelResult, *, live: bool) -> TunnelResultResponse:
    """
    Args:
        manager: The tunnel manager.
        result: What was done.
        live: Ask systemd whether the connector runs.

    Returns:
        The response model.
    """
    return TunnelResultResponse(
        tunnel=_tunnel(manager, result.tunnel, live=live),
        warnings=list(result.warnings),
        released=list(result.released),
    )


def _route_result(manager: TunnelManager, result: RouteResult) -> RouteResultResponse:
    """
    Args:
        manager: The tunnel manager.
        result: What was done.

    Returns:
        The response model.
    """
    return RouteResultResponse(
        tunnel=_tunnel(manager, result.tunnel, live=False),
        route=_route(result.route),
        ingress=list(result.ingress),
        dns=result.dns,
    )


@router.get("", response_model=TunnelListResponse)
def list_tunnels(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> TunnelListResponse:
    """
    List the tunnels, their routes and whether their connectors run.

    Args:
        session: Authenticated session, injected.

    Returns:
        The tunnels and what this server offers.
    """
    manager = TunnelManager()
    return TunnelListResponse(
        tunnels=[_tunnel(manager, tunnel) for tunnel in manager.all()],
        support=_support(manager.support()),
    )


@router.post("", response_model=TunnelResultResponse, status_code=201)
def create_tunnel(
    data: CreateTunnelRequest, session: Annotated[dict[str, Any], Depends(require_elevated)]
) -> TunnelResultResponse:
    """
    Create a tunnel at Cloudflare and run its connector here.

    Needs sudo mode: it installs a service and keeps a token.

    Args:
        data: The tunnel.
        session: Authenticated and elevated session, injected.

    Returns:
        The tunnel, ready.

    Raises:
        PrerequisiteError: ``cloudflared`` is not installed, or the store is sealed (409).
        DnsProviderError: Cloudflare refused it (502, with its words).
    """
    manager = TunnelManager()
    result = manager.create(
        data.name,
        account=data.account,
        cloudflare_account=data.cloudflare_account,
        actor=actor_label(session),
    )
    return _result(manager, result, live=True)


@router.delete("/{name}", response_model=TunnelResultResponse)
def delete_tunnel(
    name: str, session: Annotated[dict[str, Any], Depends(require_elevated)]
) -> TunnelResultResponse:
    """
    Stop a tunnel's connector, delete the tunnel at Cloudflare, and let go of its records.

    Needs sudo mode. Asking again after a failure finishes it.

    Args:
        name: The tunnel.
        session: Authenticated and elevated session, injected.

    Returns:
        The tunnel that was removed and what became of the records of its routes.

    Raises:
        ResourceNotFoundError: There is no such tunnel (404).
        DnsProviderError: Cloudflare refused to delete it (502); the same call finishes it.
    """
    manager = TunnelManager()
    return _result(manager, manager.delete(name, actor=actor_label(session)), live=False)


@router.post("/{name}/routes", response_model=RouteResultResponse, status_code=201)
def add_route(
    name: str,
    data: AddRouteRequest,
    session: Annotated[dict[str, Any], Depends(require_elevated)],
) -> RouteResultResponse:
    """
    Carry a public name through a tunnel to a service on this machine.

    Needs sudo mode: it exposes a service to the internet. Asking again for a route that
    exists changes where it goes and makes its record.

    Args:
        name: The tunnel.
        data: The route.
        session: Authenticated and elevated session, injected.

    Returns:
        The route, the ingress Cloudflare now holds and what became of the CNAME.

    Raises:
        ResourceNotFoundError: The tunnel or the application does not exist (404).
        ValidationError: The name or the service is not acceptable (400).
        DomainConflictError: Another tunnel carries the name (409).
    """
    manager = TunnelManager()
    result = manager.add_route(
        name,
        data.hostname,
        app_domain=data.app_domain,
        service=data.service,
        dns=data.dns,
        replace_record=data.replace_record,
        replace_current=data.replace_current,
        actor=actor_label(session),
    )
    return _route_result(manager, result)


@router.delete("/{name}/routes/{hostname}", response_model=RouteResultResponse)
def remove_route(
    name: str,
    hostname: str,
    session: Annotated[dict[str, Any], Depends(require_elevated)],
    keep_dns: bool = False,
) -> RouteResultResponse:
    """
    Stop carrying a public name, and let go of its record.

    Needs sudo mode.

    Args:
        name: The tunnel.
        hostname: The public name.
        session: Authenticated and elevated session, injected.
        keep_dns: Do not touch the name's CNAME.

    Returns:
        The route that was removed and what became of its record.

    Raises:
        ResourceNotFoundError: The tunnel does not exist or does not carry the name (404).
    """
    manager = TunnelManager()
    result = manager.remove_route(name, hostname, keep_dns=keep_dns, actor=actor_label(session))
    return _route_result(manager, result)
