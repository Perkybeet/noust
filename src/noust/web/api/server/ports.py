# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``GET /api/server/ports``: who holds each port of this machine.

The console's "Network and ports" section asks it to answer "who uses port 5432?" and "may I open
this one?" in the words of the product (an application, a published port, a database, the web
server) and not only as a process name. It is a thin translation of
:class:`~noust.managers.port_claims.PortClaims`, the one registry every check of a port already
asks (rule 3); this adds no second reading of the machine. It only reads: opening a port is the
firewall's route and publishing one is ``/api/streams``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from noust.core.runner import get_runner
from noust.core.store import get_store
from noust.managers.port_claims import TCP, UDP, PortClaim, PortClaims
from noust.web.api.auth import get_current_session
from noust.web.api.server.common import ServerRoute

router = APIRouter(route_class=ServerRoute)


class PortClaimOut(BaseModel):
    """
    One thing that holds a port.

    Attributes:
        port: The port.
        protocol: ``tcp`` or ``udp``.
        kind: ``system`` (the web server, SSH, the console), ``app``, ``stack`` (a Compose
            stack's published port), ``stream`` (a port published through nginx), ``database``
            (made reachable), ``instance`` (a database engine Noust runs), ``dns`` or
            ``process`` (what the kernel reports, whoever it is).
        owner: Whose it is: the web server or SSH, an application's domain, the stream's name,
            or the process.
        address: The address it is bound to; None for every address.
        detail: Where the claim comes from ("its copy", "zero-downtime"), when that says more.
        processes: For a socket the kernel reports, the processes holding it.
    """

    port: int
    protocol: str
    kind: str
    owner: str
    address: str | None = None
    detail: str = ""
    processes: list[str] = Field(default_factory=list)


class PortsOut(BaseModel):
    """
    Every claim on this machine's ports, lowest port first.

    Attributes:
        claims: The claims over TCP and UDP, declared holders before the process that
            happens to listen, so a page names what Noust knows first.
    """

    claims: list[PortClaimOut]


#: A declared holder sorts before the kernel's own report of the same port.
_PROCESS_LAST = {"process": 1}


def _out(claim: PortClaim) -> PortClaimOut:
    return PortClaimOut(
        port=claim.port,
        protocol=claim.protocol,
        kind=claim.kind,
        owner=claim.owner,
        address=claim.address,
        detail=claim.detail,
        processes=list(claim.processes),
    )


@router.get("/ports", response_model=PortsOut)
def list_ports(session: Annotated[dict, Depends(get_current_session)]) -> PortsOut:
    """
    List who holds every port of this machine, over TCP and UDP.

    Args:
        session: The authenticated session.

    Returns:
        The claims, sorted by port, protocol and then declared before observed. It names
        applications because reading the server is the whole server's (a team's role never
        holds ``server.read``).
    """
    registry = PortClaims(runner=get_runner(), store=get_store())
    found = [claim for protocol in (TCP, UDP) for claim in registry.claims(protocol)]
    found.sort(key=lambda claim: (claim.port, claim.protocol, _PROCESS_LAST.get(claim.kind, 0)))
    return PortsOut(claims=[_out(claim) for claim in found])
