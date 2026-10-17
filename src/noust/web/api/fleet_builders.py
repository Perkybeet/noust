# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/fleet/builders``: which server builds an application for the others (checklist A-49).

A thin layer (rule 3). The record is :class:`noust.fleet.builders.Builders`; shipping the build is a
bulk action of the fleet engine (``POST /api/fleet/placements/{domain}/ship``,
:mod:`noust.web.fleet_ship_actions`), so one confirmation of sudo mode covers a job and every server
answers for the operator with its own permissions.

Choosing or forgetting a builder touches only the central: no server is asked, nothing is built.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.audit import record as record_audit
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute
from noust.web.auth import actor_label

router = APIRouter(route_class=NoustErrorRoute)

Session = Annotated[dict, Depends(get_current_session)]


class BuilderIn(BaseModel):
    """Choose the server that builds an application."""

    node: str = Field(..., min_length=1, max_length=64, description="A server of this central")


class BuilderOut(BaseModel):
    """The server that builds one application."""

    domain: str
    node: str
    created_at: str
    created_by: str | None = None
    updated_at: str


class BuilderListOut(BaseModel):
    """Every application that is built on a server of its own."""

    builders: list[BuilderOut]


@router.get("", response_model=BuilderListOut)
def list_builders(session: Session) -> BuilderListOut:
    """
    List the applications that are built on a server other than the ones that run them.

    Asks no server: it is the central's own record.

    Args:
        session: The authenticated session.

    Returns:
        Every builder, by application.
    """
    from noust.fleet.builders import Builders

    return BuilderListOut(
        builders=[BuilderOut(**builder.to_dict()) for builder in Builders().list()]
    )


@router.get("/{domain}", response_model=BuilderOut)
def get_builder(domain: str, session: Session) -> BuilderOut:
    """
    Read which server builds an application.

    Args:
        domain: The application.
        session: The authenticated session.

    Returns:
        The builder.

    Raises:
        HTTPException: 404 when the application is built where it runs.
    """
    from noust.fleet.builders import Builders
    from noust.fleet.placements import checked_domain

    builder = Builders().get(checked_domain(domain))
    if builder is None:
        raise HTTPException(status_code=404, detail=f"No builder is recorded for {domain}")
    return BuilderOut(**builder.to_dict())


@router.put("/{domain}", response_model=BuilderOut)
def set_builder(domain: str, body: BuilderIn, session: Session) -> BuilderOut:
    """
    Choose the server that builds an application. Touches no server.

    Args:
        domain: The application.
        body: The server.
        session: The authenticated session.

    Returns:
        The builder as it is now.

    Raises:
        ValidationError: The domain or the name is not one.
        NodeError: The server is not registered on this central.
    """
    from noust.fleet.builders import Builders
    from noust.fleet.placements import checked_domain

    saved = Builders().set(checked_domain(domain), body.node, actor=actor_label(session))
    record_audit("fleet.builder.set", target=f"app:{saved.domain}", details={"node": saved.node})
    return BuilderOut(**saved.to_dict())


@router.delete("/{domain}", response_model=BuilderOut)
def clear_builder(domain: str, session: Session) -> Any:
    """
    Go back to building an application where it runs. Nothing is touched on any server.

    Args:
        domain: The application.
        session: The authenticated session.

    Returns:
        The builder that was forgotten.

    Raises:
        HTTPException: 404 when no builder was recorded.
    """
    from noust.fleet.builders import Builders
    from noust.fleet.placements import checked_domain

    builders = Builders()
    key = checked_domain(domain)
    builder = builders.get(key)
    if builder is None:
        raise HTTPException(status_code=404, detail=f"No builder is recorded for {domain}")
    builders.clear(key)
    record_audit("fleet.builder.clear", target=f"app:{key}", details={"node": builder.node})
    return BuilderOut(**builder.to_dict())
