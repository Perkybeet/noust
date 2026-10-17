# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Freezing deploys for a stretch of time or an incident (checklist B-26).

A client of :mod:`noust.core.deploy_freeze`, like ``noust deploy freeze``:

- ``GET /api/deploy-freezes`` lists the freezes that hold now or start later;
- ``POST`` freezes one application, or the whole server, with a reason, and
  optionally from a time to a time;
- ``DELETE /api/deploy-freezes/{id}`` lifts one before it ends.

What a freeze holds, and what it does not, is :mod:`noust.core.deploy_guard`'s.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from noust.core import deploy_freeze
from noust.core.teams import scoped_store
from noust.web.api.app_rollout import FreezeOut
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, strict_domain
from noust.web.api.scope import require_instance_scope
from noust.web.auth import actor_label
from noust.web.permissions import Permission

router = APIRouter(route_class=NoustErrorRoute)


class FreezesResponse(BaseModel):
    """The freezes that hold now or start later."""

    freezes: list[FreezeOut]


class FreezeRequest(BaseModel):
    """A freeze to make."""

    reason: str = Field(
        min_length=1,
        max_length=deploy_freeze.MAX_REASON,
        description="Why; whoever tries to deploy meanwhile is told it",
    )
    domain: str | None = Field(
        default=None, description="Freeze this application alone; omit to freeze the server"
    )
    starts_at: str | None = Field(
        default=None,
        description="When it starts, ISO 8601 (2026-12-24T18:00 or with an offset); now when omitted",
    )
    ends_at: str | None = Field(
        default=None, description="When it ends; omit to last until somebody lifts it"
    )
    hours: float | None = Field(
        default=None, gt=0, description="Instead of ends_at: how long from its start"
    )


class LiftedResponse(BaseModel):
    """What lifting did."""

    lifted: list[FreezeOut]


def _visible(freeze: deploy_freeze.Freeze) -> bool:
    """
    Args:
        freeze: A freeze.

    Returns:
        Whether the caller may know of it: the server's, and an application's it sees.
    """
    return freeze.domain is None or scoped_store().get_app(freeze.domain) is not None


@router.get("", response_model=FreezesResponse)
def list_freezes(
    session: Annotated[dict, Depends(get_current_session)],
    include_past: Annotated[
        bool, Query(description="Also the ones that ended or were lifted")
    ] = False,
) -> FreezesResponse:
    """
    List the freezes that hold deploys now or start later.

    A freeze of the whole server is shown to everyone: it explains why a deploy was
    refused. One of an application is shown to whoever sees the application.

    Args:
        session: The authenticated session.
        include_past: Also the freezes that ended or were lifted.

    Returns:
        The freezes, newest first.
    """
    freezes = deploy_freeze.list_freezes(include_past=include_past)
    return FreezesResponse(
        freezes=[FreezeOut(**f.to_dict()) for f in freezes if _visible(f)],
    )


@router.post("", response_model=FreezeOut, status_code=201)
def create_freeze(
    body: FreezeRequest, session: Annotated[dict, Depends(get_current_session)]
) -> FreezeOut:
    """
    Freeze deploys.

    One application with ``domain``, or the whole server without: that one is the
    instance's to decide, not a team's.

    Args:
        body: The reason, what to freeze and for how long.
        session: The authenticated session.

    Returns:
        The freeze.

    Raises:
        HTTPException: 404 when the application is unknown (or another team's), 403
            when a team member freezes the server.
        ValidationError: The reason, a time or the length is not acceptable (400).
    """
    domain: str | None = None
    if body.domain is None:
        require_instance_scope(Permission.APPS_MANAGE)
    else:
        domain = strict_domain(body.domain)
        if scoped_store().get_app(domain) is None:
            raise HTTPException(status_code=404, detail=f"Application not found: {domain}")
    freeze = deploy_freeze.create(
        body.reason,
        domain=domain,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        until_hours=body.hours,
        by=actor_label(session),
    )
    return FreezeOut(**freeze.to_dict())


@router.delete("/{freeze_id}", response_model=LiftedResponse)
def lift_freeze(
    freeze_id: int, session: Annotated[dict, Depends(get_current_session)]
) -> LiftedResponse:
    """
    Lift a freeze before it ends.

    Args:
        freeze_id: The freeze's number.
        session: The authenticated session.

    Returns:
        The freeze that was lifted.

    Raises:
        HTTPException: 404 when there is no such freeze (or its application is
            another team's); 403 when a team member lifts the server's.
    """
    freeze = deploy_freeze.get_freeze(freeze_id)
    if freeze is None or not _visible(freeze):
        raise HTTPException(status_code=404, detail=f"Freeze not found: {freeze_id}")
    if freeze.domain is None:
        require_instance_scope(Permission.APPS_MANAGE)
    lifted = deploy_freeze.lift(identifier=freeze_id, by=actor_label(session))
    return LiftedResponse(lifted=[FreezeOut(**f.to_dict()) for f in lifted])
