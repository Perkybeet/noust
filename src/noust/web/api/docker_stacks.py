# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/docker/stacks/{name}``: what is done to a stack as a whole.

A client of :class:`~noust.core.docker.gate.DockerGate` (rule 3): the stack is found among the Compose
projects Docker's own containers name and judged as (owner, action, stack) before a process starts (rule 4).
What belongs to an application or to a database engine is changed through them, and the refusal says the
command that does. This module holds the HTTP around that: sudo mode, the permission, the audit trail, and
the stack's Compose document with its history, and bringing a stack to Noust.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from noust.core import audit
from noust.core.store import DeploymentTrigger
from noust.deployers.compose_adopt import AdoptionRefusedError
from noust.managers.docker_bring import StackBringer
from noust.web.api.deps import NoustErrorRoute, error_response
from noust.web.api.docker import Elevated, _acted, _engine, _gate
from noust.web.api.docker_change_models import DockerBringOut, DockerBringRequest, bring_out
from noust.web.api.docker_models import DockerActionOut
from noust.web.permissions import Permission
from noust.web.permissions.enforce import check_permission
from noust.web.permissions.principal import audit_actor

router = APIRouter(route_class=NoustErrorRoute)

__all__ = ["router"]


@router.post("/stacks/{name}/start", response_model=DockerActionOut)
def start_stack(name: str, session: Elevated) -> DockerActionOut:
    """
    Start every container of a stack nobody of Noust owns, in the order Compose gives.

    What belongs to an application, a database engine or Noust is refused (409 ``docker_owned``) with the
    door to use. Needs sudo mode and ``docker.manage``.

    Args:
        name: The Compose project's name.
        session: The authenticated session, in sudo mode.

    Returns:
        What was done.

    Raises:
        DockerNotFoundError: 404 when there is none.
        DockerOwnedError: 409 when it is somebody's.
    """
    return _acted(session, "docker.action", "stack", name, "start", lambda g: g.start_stack(name))


@router.post("/stacks/{name}/stop", response_model=DockerActionOut)
def stop_stack(name: str, session: Elevated) -> DockerActionOut:
    """
    Stop every container of a stack nobody of Noust owns, giving each 30 seconds before Docker kills it.

    Args:
        name: The Compose project's name.
        session: The authenticated session, in sudo mode.

    Returns:
        What was done.

    Raises:
        DockerNotFoundError: 404 when there is none.
        DockerOwnedError: 409 when it is somebody's.
    """
    return _acted(session, "docker.action", "stack", name, "stop", lambda g: g.stop_stack(name))


@router.post("/stacks/{name}/restart", response_model=DockerActionOut)
def restart_stack(name: str, session: Elevated) -> DockerActionOut:
    """
    Restart every container of a stack nobody of Noust owns.

    Args:
        name: The Compose project's name.
        session: The authenticated session, in sudo mode.

    Returns:
        What was done.

    Raises:
        DockerNotFoundError: 404 when there is none.
        DockerOwnedError: 409 when it is somebody's.
    """
    return _acted(
        session, "docker.action", "stack", name, "restart", lambda g: g.restart_stack(name)
    )


@router.post(
    "/stacks/{name}/bring",
    response_model=DockerBringOut,
    responses={
        409: {"description": "It cannot be brought, or starting it as Noust would changes it"}
    },
)
def bring_stack(name: str, body: DockerBringRequest, session: Elevated) -> Any:
    """
    Bring a stack that already runs to Noust, or say what that would do.

    This is ``noust app adopt`` where the zone shows the stack: Noust registers it as it runs, and nothing
    that runs is recreated, restarted or changed, nor is its compose file rewritten. The folder and the file
    are what the stack's containers say; the name the application will have is the only thing typed. With
    ``preview`` nothing is registered and the answer is what would be: that the folder is on this server,
    what the rehearsed ``up`` says, the counts, what would not come back after a restart. Compose's own
    words, when starting it the way Noust would changes something, are in ``output`` of a 409 and are not
    paraphrased. What is already Noust's, what is not on this server and a stack started from several files
    are refused first with the reason as a code (409 ``docker_blocked``). Needs the permission to change
    Docker, the one to manage applications, and sudo mode.

    Args:
        name: The Compose project's name.
        body: The name the application will have, whether to only preview, and whether to accept a
            rehearsal that changes something.
        session: The authenticated session, in sudo mode.

    Returns:
        What was, or would be, registered.

    Raises:
        DockerNotFoundError: 404 when there is no such stack.
        DockerOwnedError: 409 when an engine or Noust owns it.
        DockerBlockedError: 409 ``docker_blocked`` with the reason.
        DeploymentError: 409 when adopting is refused (Compose's rehearsal, a unit that exists).
    """
    check_permission(session, Permission.APPS_MANAGE)
    gate = _gate()
    gate.require_usable(_engine(gate))
    bringer = StackBringer(gate)
    try:
        if body.preview:
            return bring_out(
                bringer.preview(name, name=body.name, accept_recreate=body.accept_recreate)
            )
        found = bringer.bring(
            name,
            name=body.name,
            accept_recreate=body.accept_recreate,
            trigger=DeploymentTrigger.PANEL.value,
        )
    except AdoptionRefusedError as exc:
        # Not a failure of the server: the stack is not in the state the request assumed, and Compose's
        # words say how.
        refused: JSONResponse = error_response(exc)
        refused.status_code = 409
        return refused
    audit.record(
        "docker.bring",
        actor=audit_actor(session),
        target=f"app:{found.name}",
        details={"stack": found.stack, "folder": found.folder, "compose_file": found.compose_file},
    )
    return bring_out(found)
