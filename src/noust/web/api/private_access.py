# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application that answers a private network only, over the API.

A thin translation of :mod:`noust.deployers.private_access`, which ``noust app policy private``
calls too. Whether an application is private is derived from its site's allowed addresses, so this
is the policy's own view with the networks named; changing it is the policy's own change, held to
the same permission, with the same check of the caller's address against the list.

The certificate Tailscale issues for the server's name is kept with the server's uploaded
certificates, which belong to the whole server and not to a team: that call needs sudo mode and
the instance scope.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from noust.core.teams import scoped_store
from noust.deployers import private_access
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated, strict_domain
from noust.web.api.scope import require_instance_scope
from noust.web.auth import get_client_ip
from noust.web.permissions import Permission

#: Mounted at ``/apps``: the paths under ``/{domain}/private``.
router = APIRouter(route_class=NoustErrorRoute)

#: Mounted at ``/private-networks``: the server's own list.
networks_router = APIRouter(route_class=NoustErrorRoute)


class NetworkModel(BaseModel):
    """
    A private network this server is on.

    Attributes:
        name: What it is called here: ``tailscale``, or a WireGuard interface.
        kind: ``tailscale`` or ``wireguard``.
        interface: The network interface.
        cidrs: The ranges a device of it has an address in.
        addresses: This server's addresses on it.
        up: It is connected now.
        dns_name: What the tailnet calls this server.
        tailnet: The tailnet's name.
        cert_domains: Names Tailscale issues a certificate for.
        problem: Why it is not usable.
    """

    name: str
    kind: str
    interface: str = ""
    cidrs: list[str]
    addresses: list[str]
    up: bool
    dns_name: str = ""
    tailnet: str = ""
    cert_domains: list[str]
    problem: str = ""


class NetworksResponse(BaseModel):
    """The private networks this server is on."""

    networks: list[NetworkModel]


class PrivateModel(BaseModel):
    """
    Whether an application answers a private network only.

    Attributes:
        app: The application's primary domain.
        private: Its allowed addresses are not empty and all of them are private ranges.
        networks: Networks this server is on whose ranges are all allowed.
        allow_ips: The addresses and networks allowed.
        public_ranges: Allowed entries that are not private.
        applicable: Whether the application's site takes a policy.
        reason: Why not.
        available: The networks it could be published to.
        tailnet_name: What the tailnet calls this server.
        tailnet_certificate: The Tailscale certificate the application is served with.
    """

    app: str
    private: bool
    networks: list[str]
    allow_ips: list[str]
    public_ranges: list[str]
    applicable: bool
    reason: str = ""
    available: list[NetworkModel]
    tailnet_name: str = ""
    tailnet_certificate: str = ""


class PrivateRequest(BaseModel):
    """
    Make an application answer a private network only.

    Attributes:
        network: A network of the list: ``tailscale``, ``wg0``.
        cidrs: More private ranges to allow.
        accept_lockout: Go ahead although the list refuses the caller's own address.
    """

    network: str | None = None
    cidrs: list[str] = []
    accept_lockout: bool = False


class TailnetCertificateModel(BaseModel):
    """
    The certificate Tailscale issued for this server's name.

    Attributes:
        name: The uploaded certificate's name.
        hostname: The name it is for.
        not_after: When it expires.
        applications: The applications that serve it.
    """

    name: str
    hostname: str
    not_after: str
    applications: list[str]


def _require_app(domain: str) -> str:
    """
    Validate an application's domain and require that it is deployed.

    Args:
        domain: The domain from the path.

    Returns:
        The validated domain.

    Raises:
        HTTPException: 404 when nothing is deployed there.
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


def _model(status: private_access.PrivateStatus) -> PrivateModel:
    """
    Args:
        status: What the operation returned.

    Returns:
        Its response model.
    """
    data = status.to_dict()
    return PrivateModel(**{**data, "available": [NetworkModel(**n) for n in data["available"]]})


@networks_router.get("", response_model=NetworksResponse)
def list_networks(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> NetworksResponse:
    """
    List the private networks this server is on.

    Args:
        session: Authenticated session, injected.

    Returns:
        The tailnet, when there is one, and each WireGuard interface.
    """
    return NetworksResponse(
        networks=[NetworkModel(**network.to_dict()) for network in private_access.networks()]
    )


@router.get("/{domain}/private", response_model=PrivateModel)
def get_private(
    domain: str, session: Annotated[dict[str, Any], Depends(get_current_session)]
) -> PrivateModel:
    """
    Say whether an application answers a private network only, and which.

    Args:
        domain: The application's primary domain.
        session: Authenticated session, injected.

    Returns:
        The answer, with the networks it could be published to.
    """
    return _model(private_access.status(_require_app(domain)))


@router.put("/{domain}/private", response_model=PrivateModel)
def put_private(
    domain: str,
    request: Request,
    data: PrivateRequest,
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> PrivateModel:
    """
    Make an application answer a private network only.

    The site's allowed addresses become the network's ranges; the caller's own address is
    checked against them, and one that would be refused is rejected unless ``accept_lockout``.

    Args:
        domain: The application's primary domain.
        request: The request, for the caller's address.
        data: The network and ranges.
        session: Authenticated session, injected.

    Returns:
        The answer afterwards.

    Raises:
        ValidationError: Nothing was named, the network is down or a range is public (400).
    """
    status = private_access.enable(
        _require_app(domain),
        network=data.network,
        cidrs=data.cidrs,
        operator_addresses=[get_client_ip(request)],
        accept_lockout=data.accept_lockout,
    )
    return _model(status)


@router.delete("/{domain}/private", response_model=PrivateModel)
def delete_private(
    domain: str,
    request: Request,
    session: Annotated[dict[str, Any], Depends(get_current_session)],
    accept_lockout: bool = False,
) -> PrivateModel:
    """
    Let an application answer anybody again: the private ranges leave the allowed addresses.

    Args:
        domain: The application's primary domain.
        request: The request, for the caller's address.
        session: Authenticated session, injected.
        accept_lockout: Go ahead although what is left refuses the caller's own address.

    Returns:
        The answer afterwards.
    """
    status = private_access.disable(
        _require_app(domain),
        operator_addresses=[get_client_ip(request)],
        accept_lockout=accept_lockout,
    )
    return _model(status)


@router.post("/{domain}/private/certificate", response_model=TailnetCertificateModel)
def post_tailnet_certificate(
    domain: str, session: Annotated[dict[str, Any], Depends(require_elevated)]
) -> TailnetCertificateModel:
    """
    Serve an application with the certificate Tailscale issues for this server's name.

    Needs sudo mode and the instance scope: the certificate joins the server's uploaded ones.

    Args:
        domain: The application's primary domain.
        session: Authenticated and elevated session, injected.

    Returns:
        The certificate now served.

    Raises:
        ValidationError: The server is not on a tailnet, it cannot issue certificates, or the
            application answers on a name the certificate does not cover (400).
    """
    validated = _require_app(domain)
    require_instance_scope(Permission.APPS_MANAGE)
    issued = private_access.tailnet_certificate(validated)
    return TailnetCertificateModel(
        name=issued.name,
        hostname=issued.hostname,
        not_after=issued.not_after,
        applications=list(issued.applications),
    )
