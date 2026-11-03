# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/fleet/software``: the software of every server of the fleet (item 118).

A thin layer (rule 3): the fleet's view of each server's own ``GET /api/server/software``. Nothing
here installs or removes anything; that is done on the server, with the permission it asks for and
the ceiling its central was given, through the server's own console or the central's proxy to it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from noust.web.api.deps import NoustErrorRoute
from noust.web.api.fleet import FleetViewOut, NodeFilter, Refresh, Session, ask_view

router = APIRouter(route_class=NoustErrorRoute)


@router.get("", response_model=FleetViewOut)
async def fleet_software(
    request: Request, session: Session, node: NodeFilter = None, refresh: Refresh = False
) -> dict[str, Any]:
    """
    The software of every server: how much of what Noust manages is installed, and how much of
    that Noust installed.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.

    Returns:
        One row per server: ``installed``, ``total``, ``by_noust``, ``managed``, ``family`` and
        its page on that server's console. A server that runs a Noust of before 4.2 is
        ``unsupported``: it will count its software when it is updated.
    """
    _, body = await ask_view("software", request, session, node, refresh)
    return body
