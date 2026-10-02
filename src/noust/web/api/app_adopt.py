# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``POST /api/apps/adopt``: register a Docker Compose stack that already runs.

A client of :mod:`noust.deployers.compose_adopt`, like ``noust app adopt``.
``preview: true`` answers what the adoption would record and changes nothing,
which is what a console shows before asking; without it the stack is
adopted. A rehearsed ``up`` that would recreate something is refused with
409 and Compose's own output (``output``), unless ``accept_recreate`` says the
operator read it. It registers an application and installs a unit that runs
as root, so it needs sudo mode.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from noust.core.store import DeploymentTrigger
from noust.deployers import compose_adopt
from noust.deployers.compose_adopt import AdoptionRefusedError
from noust.web.api.deps import ErrorResponse, NoustErrorRoute, error_response, require_elevated

router = APIRouter(route_class=NoustErrorRoute)


class AdoptRequest(BaseModel):
    """The stack to adopt, and what is not found by looking."""

    domain: str = Field(description="The domain the stack serves")
    path: str = Field(description="The absolute directory the stack runs from")
    compose_file: str | None = Field(
        default=None, description="Its compose file, relative to path; found when omitted"
    )
    source: str | None = Field(
        default=None, description="Where updates fetch from; the checkout's origin when omitted"
    )
    branch: str | None = Field(
        default=None, description="The branch updates follow; the one checked out when omitted"
    )
    site: str | None = Field(
        default=None,
        description="The file in sites-available serving the domain; found when omitted",
    )
    port: int | None = Field(
        default=None, ge=1, le=65535, description="The port to register; the compose file's"
    )
    accept_recreate: bool = Field(
        default=False,
        description="Adopt even when starting the stack as Noust would recreate something",
    )
    preview: bool = Field(default=False, description="Answer what would be done; change nothing")


class AdoptResponse(BaseModel):
    """What an adoption found, and whether it was recorded."""

    domain: str
    app_name: str
    app_path: str
    compose_file: str
    project: str | None = Field(description="The Compose project every command passes with -p")
    project_from: str = Field(
        description="containers: their labels; compose: the name Compose derives; stack: "
        "the stack names it itself"
    )
    containers: list[str]
    running: bool
    source: str
    branch: str | None
    commit: str | None
    site: str | None = Field(description="The operator's site file serving the domain")
    site_name: str | None = Field(
        description="That file's name, recorded because it is not the domain"
    )
    ssl: bool
    port: int | None
    headless: bool
    dry_run: str = Field(description="docker compose up --dry-run's output, verbatim")
    changes: list[str] = Field(description="What starting the stack would change (accepted)")
    warnings: list[str]
    adopted: bool = Field(description="False for a preview or a rehearsal")
    unit: str
    deployment_id: int | None = Field(description="The adoption's history row")


@router.post(
    "/adopt",
    response_model=AdoptResponse,
    responses={409: {"model": ErrorResponse, "description": "Starting it would recreate"}},
)
def adopt_app(body: AdoptRequest, session: Annotated[dict, Depends(require_elevated)]) -> Any:
    """
    Adopt a Docker Compose stack that already runs, or preview the adoption.

    Args:
        body: The stack.
        session: The authenticated, elevated session.

    Returns:
        What was found and done; 409 with Compose's output when starting the
        stack as Noust would recreate something that was not accepted.

    Raises:
        DeploymentError: The domain is deployed, the directory is not a
            usable checkout, or the site is ambiguous.
    """
    try:
        plan = compose_adopt.plan_adoption(
            body.domain,
            Path(body.path),
            compose_file=body.compose_file,
            source=body.source,
            branch=body.branch,
            site=body.site,
            port=body.port,
            accept_recreate=body.accept_recreate,
            web=compose_adopt._default_web(),
        )
    except AdoptionRefusedError as exc:
        # Not a failure of the server: the stack is not in the state the
        # request assumed, which the operator resolves or accepts.
        refused: JSONResponse = error_response(exc)
        refused.status_code = 409
        return refused
    if body.preview:
        return AdoptResponse(
            **plan.to_dict(), adopted=False, unit=plan.app_name, deployment_id=None
        )
    result = compose_adopt.adopt(plan, trigger=DeploymentTrigger.PANEL.value)
    return AdoptResponse(**result.to_dict())
