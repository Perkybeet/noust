# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where an application is reached: its domain behind a site, or a port of the machine without one.

A client of :mod:`noust.deployers.helpers.edge`, like ``noust status``. On a server an application is
reached by its domain, over HTTPS when it has a certificate. On a container host (the central of a NAS
that was told to deploy containers on its machine's Docker) it has no site and is reached at
``http://<the machine>:<port>``, where the machine is the name its own Docker has. The console shows the
link from this, and the line to paste into the NAS's own proxy, without working out what the machine has.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from noust.deployers.helpers.edge import reached
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, known_app_row

router = APIRouter(route_class=NoustErrorRoute)


class AppAddress(BaseModel):
    """
    Where an application is reached from outside.

    Attributes:
        domain: The application.
        kind: ``site`` (by its domain, behind the web server), ``port`` (on a port of the machine, where
            there is no web server of Noust's) or ``none`` (it serves no web).
        url: The address; null for ``none``.
        port: The port of the machine it is published on, when it has one.
    """

    domain: str
    kind: str = Field(description="site, port or none")
    url: str | None = None
    port: int | None = None


@router.get("/{domain}/address", response_model=AppAddress)
def get_app_address(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> AppAddress:
    """
    Say where an application is reached from outside. Changes nothing.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The address: its domain (``site``), a port of the machine (``port``) or nothing (``none``).

    Raises:
        HTTPException: 404 when the application is unknown, or outside the caller's teams.
    """
    app = known_app_row(domain)
    found = reached(app)
    return AppAddress(domain=app.domain, kind=found.kind, url=found.url, port=found.port)
