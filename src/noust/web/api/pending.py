# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application has been told that it does not run with yet (3.4, checklist B-09).

A client of :mod:`noust.deployers.pending_changes`, like ``noust app pending``:
``GET`` lists every setting that differs from the configuration the application
was last put to work with, and what applying each takes (a restart, a rebuild, or
only the next deploy). It changes nothing and shows no value of a secret setting.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.teams import scoped_store
from noust.deployers import pending_changes
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, strict_domain

router = APIRouter(route_class=NoustErrorRoute)


class PendingItemOut(BaseModel):
    """One setting that differs from what the application runs with."""

    area: str = Field(
        description="environment, limits, sandbox, build, container, health, hooks..."
    )
    key: str = Field(description="The setting inside the area; empty for an area compared whole")
    change: str = Field(description="added, changed or removed")
    needs: str = Field(description="restart, rebuild or next_deploy")
    before: str | None = Field(
        default=None, description="What it was, for a setting that is not secret"
    )
    after: str | None = Field(
        default=None, description="What it is now, for a setting that is not secret"
    )


class PendingChangesResponse(BaseModel):
    """What an application has been told that it does not run with yet."""

    domain: str
    pending: bool
    needs: str | None = Field(
        default=None,
        description="The heaviest thing applying them takes: restart, rebuild or next_deploy",
    )
    since: str | None = Field(
        default=None, description="When the configuration it runs with was taken"
    )
    deployment_id: int | None = Field(default=None, description="The deployment that took it")
    baseline_created: bool = Field(
        default=False,
        description="The application had no baseline: it was given one just now, as it is",
    )
    items: list[PendingItemOut] = Field(default_factory=list)
    counts: dict[str, int] = Field(
        default_factory=dict,
        description="How many changes need each of restart, rebuild, next_deploy",
    )


@router.get("/{domain}/pending", response_model=PendingChangesResponse)
def get_pending(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> PendingChangesResponse:
    """
    Show what an application is set to that it does not run with yet.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The differences and what applying them takes.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    changes = pending_changes.pending(validated)
    body = changes.to_dict()
    return PendingChangesResponse(**body, counts=pending_changes.summary(changes))
