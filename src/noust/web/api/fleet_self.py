# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What this server lets its centrals do, published: ``GET /api/auth/fleet/self``.

A thin layer over :func:`noust.fleet.policy.current_access` (rule 3). A
central asks it through its tunnel with its own fleet token, to show the
ceiling and grey out what this server would refuse; the node's own console
asks it to show the same. It only reads: the ceiling is set on this server's
command line (``noust fleet access``), never over the API, so a central can
never raise the ceiling it is held to. The enforcement is elsewhere - where a
fleet token is admitted (:mod:`noust.web.auth`) - and does not depend on
anyone having asked.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from noust.fleet.policy import current_access
from noust.web.api.deps import NoustErrorRoute, require_auth

router = APIRouter(route_class=NoustErrorRoute)

#: A central's fleet token name: ``fleet-<central>`` or ``fleet-<central>.<n>``.
_FLEET_TOKEN = re.compile(r"fleet-([a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?)(?:\.[0-9]+)?")


class FleetSelfResponse(BaseModel):
    """
    The most a central may do on this server, and who is asking.

    Attributes:
        level: ``read``, ``deploy`` or ``admin``.
        host_access: Whether a central may also change how this server is
            reached (SSH keys, sshd, firewall, accounts); only with ``admin``.
        fleet: Whether the caller is a central (a fleet token).
        token_name: The caller's fleet token, when it is one.
        central: The central that token belongs to.
        updated_at: When this server's operator last set the ceiling; None
            when it never was (the default applies).
    """

    level: str
    host_access: bool
    fleet: bool = False
    token_name: str | None = None
    central: str | None = None
    updated_at: str | None = None


@router.get("/self", response_model=FleetSelfResponse)
def fleet_self(session: Annotated[dict[str, Any], Depends(require_auth)]) -> FleetSelfResponse:
    """
    Say how far a central may go on this server.

    Args:
        session: The authenticated caller.

    Returns:
        The ceiling in force and, for a central, its own token's name.
    """
    from noust.core.store import get_store

    access = current_access()
    row = get_store().get_fleet_access()
    fleet = bool(session.get("fleet"))
    token_name = session.get("token_name") if fleet else None
    match = _FLEET_TOKEN.fullmatch(str(token_name)) if token_name else None
    return FleetSelfResponse(
        level=access.level,
        host_access=access.host_access,
        fleet=fleet,
        token_name=str(token_name) if token_name else None,
        central=match.group(1) if match else None,
        updated_at=str(row["updated_at"]) if row else None,
    )
