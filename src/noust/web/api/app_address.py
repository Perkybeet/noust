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

from noust.deployers.helpers.edge import proxy_hint, reached
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, known_app_row

router = APIRouter(route_class=NoustErrorRoute)


class ProxyHeaderOut(BaseModel):
    """
    A header the NAS's reverse proxy sets on the request it passes to the application.

    Attributes:
        name: The header.
        meaning: What goes in it: ``public_host``, ``client_address``, ``client_chain``, ``public_scheme``,
            ``public_port``, ``upgrade_request`` or ``upgrade``.
        nginx: How nginx writes it, as a site of Noust's does.
    """

    name: str
    meaning: str
    nginx: str


class ProxyHintOut(BaseModel):
    """
    What the reverse proxy of the NAS itself needs to reach an application. Noust does not touch the
    NAS: it says what to paste.

    Attributes:
        target: Where the proxy sends the request.
        scheme: ``http``.
        host: The address of ``target``.
        port: The port of the machine the application is published on.
        headers: What the proxy sets on the request.
        websocket: Whether it must let a WebSocket through.
        read_timeout_seconds: How long it waits for the application's answer.
    """

    target: str
    scheme: str
    host: str
    port: int
    headers: list[ProxyHeaderOut]
    websocket: bool
    read_timeout_seconds: int


class AppAddress(BaseModel):
    """
    Where an application is reached from outside.

    Attributes:
        domain: The application.
        kind: ``site`` (by its domain, behind the web server), ``port`` (on a port of the machine, where
            there is no web server of Noust's) or ``none`` (it serves no web).
        url: The address; null for ``none``.
        port: The port of the machine it is published on, when it has one.
        proxy_hint: What to put in the NAS's own reverse proxy to reach it by a name and over HTTPS; null
            where a site of Noust's serves it and for what serves no web.
    """

    domain: str
    kind: str = Field(description="site, port or none")
    url: str | None = None
    port: int | None = None
    proxy_hint: ProxyHintOut | None = None


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
    hint = proxy_hint(app)
    return AppAddress(
        domain=app.domain,
        kind=found.kind,
        url=found.url,
        port=found.port,
        proxy_hint=None
        if hint is None
        else ProxyHintOut(
            target=hint.target,
            scheme=hint.scheme,
            host=hint.host,
            port=hint.port,
            headers=[ProxyHeaderOut(**vars(header)) for header in hint.headers],
            websocket=hint.websocket,
            read_timeout_seconds=hint.read_timeout_seconds,
        ),
    )
