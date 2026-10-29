# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/fleet/dns``: Noust DNS on the servers of the fleet (item 104).

A thin layer (rule 3). Setting a cluster up is a bulk action of the fleet engine
(:mod:`noust.web.fleet_dns_actions`), planned and queued exactly as ``POST /api/fleet/actions``
plans and queues the others, so one confirmation of sudo mode covers the job and each server
answers for the operator with its own permissions and its own ceiling. Looking at the cluster is
the fleet's view of each server's ``GET /api/dns/noust/server``. Nothing here installs a program,
writes a configuration or holds a key.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from noust.web.api.deps import NoustErrorRoute
from noust.web.api.fleet import (
    ActionOut,
    FleetViewOut,
    NodeFilter,
    Refresh,
    Session,
    _plan_and_start,
    ask_view,
)

router = APIRouter(route_class=NoustErrorRoute)


class AuthDnsClusterServerIn(BaseModel):
    """
    One server of the cluster.

    Attributes:
        node: The server, as the central knows it.
        hostname: The name it has as a name server (``ns1.example.net``).
        listen: The addresses it answers on; its public addresses by default.
    """

    node: str = Field(max_length=64)
    hostname: str = Field(max_length=253)
    listen: list[str] | None = None


class AuthDnsClusterIn(BaseModel):
    """
    Set Noust DNS up on several servers.

    Attributes:
        primary: The server that holds the zones.
        secondaries: The servers that copy them, in other networks.
        epel: Agree to enable EPEL where Knot comes from it (RHEL and its rebuilds).
        plan: Only say what would happen.
    """

    primary: AuthDnsClusterServerIn
    secondaries: list[AuthDnsClusterServerIn] = Field(default_factory=list)
    epel: bool = False
    plan: bool = False


@router.post("/cluster", response_model=ActionOut)
async def set_up_cluster(
    request: Request, body: AuthDnsClusterIn, session: Session
) -> dict[str, Any]:
    """
    Install and set up Noust DNS on a primary and its secondaries.

    The primary goes first and alone; each secondary then reads the primary's address and key,
    sets itself up and tells the primary where it answers. A server that runs a Noust without
    Noust DNS, or whose ceiling does not let this central change its host, is shown as unable to
    take part in the plan. Planned and queued like a bulk action: sudo mode when a server's schema
    asks for it, a job to follow, a record per server. Once it is set up the servers keep each other
    in step with DNS itself; the central does not stay in the path.

    Args:
        request: The request.
        body: The primary and the secondaries.
        session: The authenticated session.

    Returns:
        The plan and, unless ``plan``, the job.

    Raises:
        ValidationError: A server is not usable, two have one name, or nothing can run (400).
    """
    from noust.core.exceptions import ValidationError
    from noust.web.fleet_dns_actions import cluster_request

    servers: dict[str, dict[str, Any]] = {}
    for item in [body.primary, *body.secondaries]:
        if item.node in servers:
            raise ValidationError(f"{item.node} is named twice", field="secondaries")
        servers[item.node] = {"hostname": item.hostname, "listen": item.listen}
    fleet_request = cluster_request(body.primary.node, servers, epel=body.epel)
    return await _plan_and_start(request, session, fleet_request, body.plan)


@router.get("", response_model=FleetViewOut)
async def fleet_dns(
    request: Request, session: Session, node: NodeFilter = None, refresh: Refresh = False
) -> dict[str, Any]:
    """
    Noust DNS on every server: whether it is installed, its role, its addresses and its zones.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.

    Returns:
        One row per server: ``role`` (primary, secondary, or null where it is not set up),
        ``hostname``, ``listen``, ``zones``, ``running`` and its page on that server's console.
        A server that runs a Noust without Noust DNS is ``unsupported``.
    """
    _, body = await ask_view("dns", request, session, node, refresh)
    return body
