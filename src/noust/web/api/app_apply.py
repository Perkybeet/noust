# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application written as a declaration file, and set from one.

A client of :mod:`noust.deployers.project_apply`, like ``noust app export --as-yaml`` and
``noust app apply``. ``GET /api/apps/{domain}/declaration`` answers the file for an application
that is running; ``POST /api/apps/{domain}/apply`` checks a file against the application, says
what applying it changes, and (unless ``check``) applies it. What runs commands (the build
block, hooks, tasks, Compose arguments) must be confirmed by the caller, who has seen the plan
first, and the route is root-equivalent and needs sudo mode: a declaration is code the
application runs.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from noust.core.exceptions import ValidationError
from noust.core.teams import scoped_store
from noust.deployers.project_apply import (
    apply_declaration,
    export_declaration,
    parse_declaration,
    plan_declaration,
)
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated, strict_domain
from noust.web.auth import actor_label, ensure_scope

router = APIRouter(route_class=NoustErrorRoute)


class DeclarationResponse(BaseModel):
    """An application as a declaration file."""

    domain: str
    declaration: str = Field(description="The YAML text, in the keys noust.yaml uses")


class ApplyRequest(BaseModel):
    """A declaration to apply."""

    declaration: str = Field(description="The YAML text")
    prune: bool = Field(
        default=False,
        description="Also remove the aliases, redirects and tasks the file does not name",
    )
    check: bool = Field(default=False, description="Only say what would change")
    confirm: bool = Field(
        default=False,
        description="Agree to what runs commands: required when the plan lists any",
    )


class PlannedChange(BaseModel):
    """One thing applying would change."""

    section: str
    description: str
    root_equivalent: bool


class AppliedStep(BaseModel):
    """One change that was attempted."""

    part: str
    applied: bool
    detail: str


class ApplyPlanModel(BaseModel):
    """What applying a declaration changes."""

    domain: str
    changes: list[PlannedChange]
    unchanged: list[str]
    notes: list[str]
    confirm: list[str] = Field(description="What runs commands, which needs confirm")
    prune: bool


class ApplyResponse(BaseModel):
    """The plan, and what was done when it was applied."""

    plan: ApplyPlanModel
    applied: bool = Field(description="False when only checked")
    steps: list[AppliedStep]


def _known(domain: str) -> str:
    """
    Validate a domain and refuse one nothing is deployed at.

    Args:
        domain: The domain as the client sent it.

    Returns:
        The validated domain.

    Raises:
        HTTPException: 404.
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


@router.get("/{domain}/declaration", response_model=DeclarationResponse)
def get_declaration(
    domain: str, request: Request, session: Annotated[dict, Depends(get_current_session)]
) -> DeclarationResponse:
    """
    Write an application as a declaration file.

    Admin scope, like the export: the file names the variables, hooks and schedules. Secret
    variables are never in it.

    Args:
        domain: Domain of the application.
        request: The incoming request.
        session: The authenticated session.

    Returns:
        The file.

    Raises:
        HTTPException: 403 below admin scope; 404 when the application is unknown.
    """
    ensure_scope(request, session, "admin")
    validated = _known(domain)
    return DeclarationResponse(domain=validated, declaration=export_declaration(validated))


@router.post("/{domain}/apply", response_model=ApplyResponse)
def apply(
    domain: str,
    body: ApplyRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> ApplyResponse:
    """
    Check a declaration against an application and apply it.

    Args:
        domain: Domain of the application.
        body: The declaration and how to apply it.
        session: An elevated session: a declaration is code the application runs.

    Returns:
        The plan, and what was done unless it was only checked.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: The declaration is not valid (400, with the field), or the plan runs
            commands and ``confirm`` was not given.
        AppBusyError: Another operation is running on the application.
    """
    validated = _known(domain)
    plan = plan_declaration(validated, parse_declaration(body.declaration), prune=body.prune)
    model = ApplyPlanModel(**plan.to_dict())
    if body.check:
        return ApplyResponse(plan=model, applied=False, steps=[])
    if plan.confirm and not body.confirm:
        raise ValidationError(
            "The declaration runs commands and needs confirming",
            details="It sets " + "; ".join(plan.confirm) + ". Check it first, then send "
            "confirm: true.",
            field="confirm",
        )
    report = apply_declaration(plan, actor=actor_label(session))
    return ApplyResponse(
        plan=model,
        applied=True,
        steps=[
            AppliedStep(part=step.part, applied=step.applied, detail=step.detail)
            for step in report.steps
        ],
    )
