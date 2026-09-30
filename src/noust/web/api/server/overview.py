# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``GET /api/server/summary`` and ``/capabilities``: the server in one look.

What the overview page and a central's server list read. Both are cheap by
construction: the slow facts are computed in the background and come back with
their age (:mod:`noust.managers.server.summary`), so a first look answers at once
with what is not yet known marked as such.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from noust.managers.server.summary import build_summary
from noust.web.api.auth import get_current_session
from noust.web.api.server.common import ServerRoute, get_server_context
from noust.web.api.server.models import CapabilitiesOut, SummaryOut

router = APIRouter(route_class=ServerRoute)


@router.get("/summary", response_model=SummaryOut)
def get_summary(session: Annotated[dict, Depends(get_current_session)]) -> dict[str, Any]:
    """
    Describe how the server is, in one cheap answer.

    Args:
        session: The authenticated session.

    Returns:
        The operating system and its end of life, pending updates, whether a
        reboot is due, the automatic updates, the disks by their worst, the
        clock, swap, what is scheduled, whether systemd is well and what this
        machine can do. A section whose probe has not finished says so with
        nulls and its ``checked_at``; one whose probe failed carries ``error``.
    """
    return build_summary(get_server_context())


@router.get("/capabilities", response_model=CapabilitiesOut)
def get_capabilities(session: Annotated[dict, Depends(get_current_session)]) -> dict[str, Any]:
    """
    Say what this machine can do, so a tab degrades with a message.

    Args:
        session: The authenticated session.

    Returns:
        Which package manager drives updates, whether updates and their
        security subset are managed here, whether the system is transactional or
        a container, whether systemd runs, whether swap can be made and whether
        Docker is installed.
    """
    capabilities: dict[str, Any] = build_summary(get_server_context())["capabilities"]
    return capabilities
