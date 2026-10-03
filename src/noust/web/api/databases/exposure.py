# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where databases listen, which ports are open to the network, and how to connect.

The Connect tab never offers to open a port: it builds the SSH tunnel an
operator runs from their own computer, from the server's own facts (the port
the engine listens on, sshd's port, the server's address). A central passes
the node's address in ``server``: the page must never use the address the
browser happens to be on, which is the central's.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import LinkResponse, service
from noust.web.api.deps import NoustErrorRoute

router = APIRouter(route_class=NoustErrorRoute)


class ExposedPortResponse(BaseModel):
    """
    A database port open beyond this machine, or one the firewall closes.

    Attributes:
        source: ``engine`` for a server on the host, ``docker`` for a port
            Docker publishes for a container.
        advice: How to close it, in English.
        container_port: The port inside the container, for ``docker``.
        firewalled: Something keeps the Internet out of this port: the
            ``DOCKER-USER`` chain refuses it on the public interface, or it is
            an IPv6 publication with no IPv6 route to the server. Only
            ``ExposureResponse.firewalled`` carries such entries.
        closed_by: What closes it, when ``firewalled``.
        rule: The ``DOCKER-USER`` rule that refuses it, verbatim, when there
            is one.
    """

    engine: str
    port: int
    address: str
    process: str | None = None
    source: str
    container: str | None = None
    image: str | None = None
    advice: str = ""
    container_port: int | None = None
    firewalled: bool = False
    closed_by: str = ""
    rule: str = ""


class ExposureResponse(BaseModel):
    """
    Every database port beyond this machine, and the ones the firewall closes.

    Attributes:
        exposed: The ports a stranger on the Internet can reach. This is the
            list every alarm (the console's notice, the CLI's exit code) reads.
        firewalled: Ports Docker publishes on every address that the firewall
            keeps the Internet out of (``firewalled`` true, ``closed_by`` and
            ``rule`` say how). Shown as information, never as a finding.
    """

    exposed: list[ExposedPortResponse]
    firewalled: list[ExposedPortResponse] = Field(default_factory=list)


class ListenResponse(BaseModel):
    """
    Where an engine's server says it listens.

    Attributes:
        setting: The engine's own name for the setting.
        loopback_only: Every address is a loopback one.
    """

    setting: str
    addresses: list[str]
    loopback_only: bool


class EngineExposureResponse(BaseModel):
    """One engine's listen setting and its open ports."""

    engine: str
    port: int
    listen: ListenResponse | None = None
    exposed: list[ExposedPortResponse]


class TunnelResponse(BaseModel):
    """
    The SSH tunnel to reach a database from the operator's computer.

    Attributes:
        command: The complete ``ssh -N -L`` command.
        url: The connection string through the tunnel, password masked.
        clients: Ready command lines, by client (``psql``, ``mysql``,
            ``redis-cli``, ``mongosh``, ``jdbc``).
    """

    server: str
    ssh_port: int
    ssh_user: str
    local_port: int
    remote_port: int
    command: str
    url: str
    clients: dict[str, str] = Field(default_factory=dict)


class ConnectResponse(BaseModel):
    """
    Everything the Connect tab shows.

    Attributes:
        password_known: Noust keeps the account's password, so "Show" works
            (sudo mode); otherwise the console offers a rotation.
        apps: The applications' variables that carry the database.
    """

    engine: str
    database: str
    username: str | None = None
    password_known: bool
    port: int
    listen: ListenResponse | None = None
    apps: list[LinkResponse]
    tunnel: TunnelResponse
    exposed: list[ExposedPortResponse]


@router.get("/exposure", response_model=ExposureResponse)
def get_exposure(session: Annotated[dict, Depends(get_current_session)]) -> ExposureResponse:
    """
    List every database port reachable from beyond this machine.

    Args:
        session: The authenticated session.

    Returns:
        The exposed ports, Docker's published ones included, and apart from
        them the published ones the firewall closes.
    """
    found = service(session).exposure(include_firewalled=True)
    return ExposureResponse(
        exposed=[ExposedPortResponse(**e.to_dict()) for e in found if not e.firewalled],
        firewalled=[ExposedPortResponse(**e.to_dict()) for e in found if e.firewalled],
    )


@router.get("/engines/{engine}/exposure", response_model=EngineExposureResponse)
def get_engine_exposure(
    engine: str, session: Annotated[dict, Depends(get_current_session)]
) -> EngineExposureResponse:
    """
    Report where one engine listens and whether its port is open.

    Args:
        engine: Engine name.
        session: The authenticated session.

    Returns:
        The engine's listen setting and exposed ports.
    """
    databases = service(session)
    manager = databases.running(engine)
    port = manager.server_port()
    listen = manager.listen_addresses()
    exposed = [
        ExposedPortResponse(**entry.to_dict()) for entry in databases.engine_exposure(manager)
    ]
    return EngineExposureResponse(
        engine=manager.ENGINE_NAME,
        port=port,
        listen=ListenResponse(**listen.to_dict()) if listen else None,
        exposed=exposed,
    )


@router.get("/databases/{engine}/{name}/connect", response_model=ConnectResponse)
def get_connect(
    engine: str,
    name: str,
    session: Annotated[dict, Depends(get_current_session)],
    username: Annotated[str | None, Query(description="Account to connect as")] = None,
    server: Annotated[
        str | None, Query(description="Address the operator reaches this server at")
    ] = None,
    ssh_user: Annotated[str | None, Query(description="Account to sign in to the server")] = None,
    local_port: Annotated[
        int | None, Query(ge=1, le=65535, description="Port to open locally")
    ] = None,
) -> ConnectResponse:
    """
    Build the Connect tab: from the application, from a computer, exposure.

    Args:
        engine: Engine name.
        name: Database name.
        session: The authenticated session.
        username: The account; the provisioned one by default.
        server: This server's address as the operator reaches it.
        ssh_user: The SSH account.
        local_port: The local end of the tunnel.

    Returns:
        The connection information. No password is in it.
    """
    data = service(session).connection_info(
        engine,
        name,
        username=username,
        server=server,
        ssh_user=ssh_user,
        local_port=local_port,
    )
    return ConnectResponse(
        engine=data["engine"],
        database=data["database"],
        username=data["username"],
        password_known=data["password_known"],
        port=data["port"],
        listen=ListenResponse(**data["listen"]) if data["listen"] else None,
        apps=[LinkResponse(**link) for link in data["apps"]],
        tunnel=TunnelResponse(**data["tunnel"]),
        exposed=[ExposedPortResponse(**entry) for entry in data["exposed"]],
    )
