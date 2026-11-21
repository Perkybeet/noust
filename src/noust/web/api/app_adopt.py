# Copyright (c) 2024-2026 Yago López Prado
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
from noust.web.api.deps import (
    CodedText,
    ErrorResponse,
    NoustErrorRoute,
    error_response,
    require_elevated,
)
from noust.web.api.docker_change_models import DockerBringRestartOut
from noust.web.auth import actor_label
from noust.web.permissions import Permission
from noust.web.permissions.enforce import PermissionDenied, has_permission

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
    accept_host_access: bool = Field(
        default=False,
        description="Adopt although the stack runs with what gives this server to a container "
        "(a device, the Docker socket, a path of the server). It is recorded as the "
        "application's compose exception, with who accepted it, and needs the root-equivalent "
        "permission",
    )
    host_access_digest: str = Field(
        default="",
        max_length=128,
        description="The digest of the lines that give this server to a container, as the refusal or the "
        "preview that listed them said it (`host_access_digest`). Accepting them is accepting those "
        "lines: without it, or with another, the answer is a 409 with the lines as they are now",
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
    warnings_coded: list[CodedText | None] = Field(
        default_factory=list,
        description="warnings as sentences with a code, in the same order; null where a line has "
        "no sentence of its own",
    )
    adopted: bool = Field(description="False for a preview or a rehearsal")
    unit: str = Field(description="The unit created for it; empty where Docker keeps it alive")
    deployment_id: int | None = Field(description="The adoption's history row")
    kept_by: str = Field(
        default="unit",
        description="unit: a unit of systemd is created; docker: the central deploys with the "
        "machine's Docker and the restart policy of the containers keeps the stack alive",
    )
    address: str | None = Field(
        default=None,
        description="Where it is reached when no site serves it: http://<machine>:<port>",
    )
    restart_loss: list[DockerBringRestartOut] = Field(
        default_factory=list,
        description="What a restart of the machine would not bring back, said where nothing but "
        "Docker brings it back",
    )
    host_access: list[str] = Field(
        default_factory=list,
        description="The lines of the compose file that give this server to a container and that "
        "adopting recorded as accepted: the application's compose exception",
    )
    host_access_digest: str = Field(
        default="",
        description="Their digest: what accepting them says (`host_access_digest` of the request)",
    )
    accepted_by: str = Field(default="", description="Who accepted them")


@router.post(
    "/adopt",
    response_model=AdoptResponse,
    responses={409: {"model": ErrorResponse, "description": "Starting it would recreate"}},
)
def adopt_app(body: AdoptRequest, session: Annotated[dict, Depends(require_elevated)]) -> Any:
    """
    Adopt a Docker Compose stack that already runs, or preview the adoption.

    A stack that runs with what gives this server to a container is refused with the lines and their digest
    (``host_access``, ``host_access_digest``). Accepting them is accepting those lines: ``accept_host_access``
    goes with ``host_access_digest``, and when it is missing or the lines are not those any more the answer
    is a 409 with the lines as they are now and nothing is registered.

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
    if body.accept_host_access and not has_permission(session, Permission.ROOT_EQUIVALENT):
        # What the stack runs with is root on this server: accepting it is the exception of the
        # compose guard, which no team role and no central without host access can record.
        raise PermissionDenied(
            "permission_denied",
            Permission.ROOT_EQUIVALENT,
            "Adopting a stack that runs with what gives this server to a container is root-equivalent",
            "Ask an administrator of the server.",
        )
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
            accept_host_access=body.accept_host_access,
            host_access_digest=body.host_access_digest,
            accepted_by=actor_label(session) if body.accept_host_access else "",
            web=compose_adopt._default_web(),
        )
    except AdoptionRefusedError as exc:
        # Not a failure of the server: the stack is not in the state the
        # request assumed, which the operator resolves or accepts.
        refused: JSONResponse = error_response(exc)
        refused.status_code = 409
        return refused
    if body.preview:
        return AdoptResponse(**plan.to_dict(), adopted=False, unit=plan.unit, deployment_id=None)
    result = compose_adopt.adopt(plan, trigger=DeploymentTrigger.PANEL.value)
    return AdoptResponse(**result.to_dict())
