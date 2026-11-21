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
from noust.web.api.docker import Elevated, Session, _acted, _engine, _gate
from noust.web.api.docker_change_models import (
    DockerBringOut,
    DockerBringPreviewRequest,
    DockerBringRequest,
    bring_out,
)
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


def _refused(exc: AdoptionRefusedError) -> JSONResponse:
    """
    Args:
        exc: Compose's rehearsal said starting the stack the way Noust would changes it, or failed.

    Returns:
        A 409 with Compose's own words (already masked by the one that brings stacks) and what to do.
    """
    # Not a failure of the server: the stack is not in the state the request assumed, and Compose's
    # words say how.
    refused: JSONResponse = error_response(exc)
    refused.status_code = 409
    return refused


@router.post(
    "/stacks/{name}/bring/preview",
    response_model=DockerBringOut,
    responses={
        409: {"description": "It cannot be brought, or starting it as Noust would changes it"}
    },
)
def preview_bring(name: str, body: DockerBringPreviewRequest, session: Session) -> Any:
    """
    Say what bringing a stack that already runs to Noust would do, without doing it or asking to confirm.

    Looking changes nothing, so it is read with the permission to read the zone and asks no sudo mode:
    the person sees what would be registered before being asked who they are, and the confirmation is asked
    when it is applied (``POST .../bring``). The one thing that runs is Compose's rehearsal of starting the
    stack (``up --dry-run``), which only looks, and nothing is registered, written, started or stopped. The
    words of Compose and the address the checkout fetches from are shown with the credentials masked. What
    is already Noust's, what is not on this server and a stack started from several files are refused first
    with the reason as a code (409 ``docker_blocked``), and Compose's own words, when starting it the way
    Noust would changes something, are in ``output`` of a 409. A stack that runs with what gives this server to a
    container is refused with the lines and their digest (``host_access``, ``host_access_digest``); to show the
    plan with them accepted, the request says that digest back.

    Args:
        name: The Compose project's name.
        body: The name the application would have, whether to show the plan though the rehearsal changes
            something, and the digest of the lines that were listed.
        session: The authenticated session.

    Returns:
        What would be registered and what is not changed.

    Raises:
        DockerNotFoundError: 404 when there is no such stack.
        DockerOwnedError: 409 when an engine or Noust owns it.
        DockerBlockedError: 409 ``docker_blocked`` with the reason.
        DeploymentError: 409 when adopting is refused (Compose's rehearsal, a unit that exists).
    """
    gate = _gate()
    gate.require_usable(_engine(gate))
    try:
        return bring_out(
            StackBringer(gate).preview(
                name,
                name=body.name,
                accept_recreate=body.accept_recreate,
                accept_host_access=body.accept_host_access,
                host_access_digest=body.host_access_digest,
                accepted_by=audit_actor(session) if body.accept_host_access else "",
            )
        )
    except AdoptionRefusedError as exc:
        return _refused(exc)


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

    To only look, without sudo mode, use ``POST .../bring/preview``: it is the same preview, read with the
    permission to read the zone. ``preview`` here is kept for who already sends it, and asks sudo mode like
    the rest of this route.

    This is ``noust app adopt`` where the zone shows the stack: Noust registers it as it runs, and nothing
    that runs is recreated, restarted or changed, nor is its compose file rewritten. The folder and the file
    are what the stack's containers say; the name the application will have is the only thing typed. With
    ``preview`` nothing is registered and the answer is what would be: that the folder is on this server,
    what the rehearsed ``up`` says, the counts, what would not come back after a restart. Compose's own
    words, when starting it the way Noust would changes something, are in ``output`` of a 409 and are not
    paraphrased. What is already Noust's, what is not on this server and a stack started from several files
    are refused first with the reason as a code (409 ``docker_blocked``). Needs the permission to change
    Docker, the one to manage applications, and sudo mode.

    What a stack that runs with what gives this server to a container is accepted with is what the person was
    shown: ``accept_host_access`` goes with ``host_access_digest``, the digest the preview or the refusal that
    listed the lines gave. Without it, or when the lines are not those any more, the answer is a 409 with the
    lines as they are now and nothing is registered.

    Args:
        name: The Compose project's name.
        body: The name the application will have, whether to only preview, whether to accept a
            rehearsal that changes something, and the digest of the lines that were accepted.
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
    if body.accept_host_access:
        # What the stack runs with is root on this server: accepting it records the compose exception.
        check_permission(session, Permission.ROOT_EQUIVALENT)
    gate = _gate()
    gate.require_usable(_engine(gate))
    bringer = StackBringer(gate)
    accepted_by = audit_actor(session) if body.accept_host_access else ""
    try:
        if body.preview:
            return bring_out(
                bringer.preview(
                    name,
                    name=body.name,
                    accept_recreate=body.accept_recreate,
                    accept_host_access=body.accept_host_access,
                    host_access_digest=body.host_access_digest,
                    accepted_by=accepted_by,
                )
            )
        found = bringer.bring(
            name,
            name=body.name,
            accept_recreate=body.accept_recreate,
            accept_host_access=body.accept_host_access,
            host_access_digest=body.host_access_digest,
            accepted_by=accepted_by,
            trigger=DeploymentTrigger.PANEL.value,
        )
    except AdoptionRefusedError as exc:
        return _refused(exc)
    audit.record(
        "docker.bring",
        actor=audit_actor(session),
        target=f"app:{found.name}",
        details={"stack": found.stack, "folder": found.folder, "compose_file": found.compose_file},
    )
    return bring_out(found)
