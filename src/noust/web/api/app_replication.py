# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``GET /api/apps/{domain}/replication``: could this application run on more than one server?

A client of :mod:`noust.managers.replication`, like ``noust app replication``. It reads this
server's records of the application and answers what a second server would get wrong or do twice;
it places nothing and reaches no other server. Putting an application on several servers is the
central's (``/api/fleet/placements``).
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.teams import scoped_store
from noust.managers import replication
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, strict_domain

router = APIRouter(route_class=NoustErrorRoute)


class FindingModel(BaseModel):
    """One thing a second server would get wrong or do twice."""

    code: str = Field(description="persistent_paths, local_database, scheduled_tasks...")
    severity: Literal["blocks", "warns"] = Field(
        description="blocks: a copy would be wrong; warns: it would work, with a cost"
    )
    message: str
    fix: str
    items: list[str] = Field(description="What it is about: paths, databases, variables, tasks")


class ReplicationResponse(BaseModel):
    """Whether an application could run on several servers."""

    domain: str
    ready: bool = Field(description="True when nothing blocks it; warnings do not")
    findings: list[FindingModel]
    by_image: bool = Field(
        description="It runs a registry image, which a rollout can name by tag so no server builds it"
    )


@router.get("/{domain}/replication", response_model=ReplicationResponse)
def get_replication(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> ReplicationResponse:
    """
    Say whether an application could run on more than one server. Changes nothing.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The verdict and every finding.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = strict_domain(domain)
    app = scoped_store().get_app(validated)
    if app is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return ReplicationResponse(**replication.assess(app).to_dict())
