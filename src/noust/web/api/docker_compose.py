# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/docker/stacks/{name}/compose``: edit the Compose of a stack with a net.

A client of :class:`~noust.deployers.compose_stack_edit.StackEditor` (rule 3), which judges a document by the
model Compose resolves it to, says what it would change, keeps every saved text as a version with who saved it
and why, and applies it behind the application's health gate, putting the document before back when the stack
does not come up. What the routes add is HTTP:

- **Reading is a reveal.** A document can hold a value somebody typed into it, so reading it, or a version
  of it, needs the permission that reveals secrets and is recorded. The list of versions has no text.
- **Judging writes nothing.** The text goes to Compose on its standard input; the answer is what it found,
  and what applying it would do, rehearsed.
- **Applying is a job** (the application's update, which takes minutes), queued after the document passes
  and was kept as a version. The job is given the version, not the text, so a document that holds a secret
  is not in the job's record. Changing needs the permission to change Docker, the one to manage
  applications and sudo mode.
- **A stack whose document is somebody else's is not edited here**: a repository's, a recipe's, a container
  application's generated file, or one Noust did not make. The answer says whose it is and where it is
  changed (``editable: false``, with a code), and a change is refused with the same code (409
  ``docker_blocked``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from noust.core import deploy_guard
from noust.deployers.compose_stack_edit import StackEditor
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute
from noust.web.api.docker import Elevated, Session, _engine, _gate
from noust.web.api.docker_change_models import (
    DockerComposeCheckOut,
    DockerComposeCheckRequest,
    DockerComposeOut,
    DockerComposeRestoreRequest,
    DockerComposeSaveRequest,
    DockerComposeVersionDetailOut,
    DockerComposeVersionOut,
    DockerComposeVersionsOut,
    check_out,
    compose_out,
    version_detail_out,
)
from noust.web.auth import actor_label
from noust.web.jobs import JobContext, JobType, get_job_manager
from noust.web.permissions import Permission
from noust.web.permissions.enforce import check_permission

router = APIRouter(route_class=NoustErrorRoute)

__all__ = ["docker_compose_job", "router"]


def docker_compose_job(
    domain: str,
    stack: str,
    version: int,
    actor: str,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Apply a version of a stack's document: the application's update behind its health gate.

    Args:
        domain: The application, which is what the job takes the lock of.
        stack: The Compose project.
        version: The version to apply.
        actor: Who asked, for the history and the audit trail.
        job_context: Injected by the job manager.

    Returns:
        What was done.

    Raises:
        ValueError: The job was run without its context.
    """
    if job_context is None:
        raise ValueError("A job runs with its context")
    job_context.set_metadata("domain", domain)
    done = StackEditor(_gate()).apply(
        stack,
        version,
        actor=actor,
        trigger="panel",
        job_id=job_context.job_id,
        on_phase=lambda index, total, message: job_context.update(
            message, 100 * (index - 1) // max(total, 1)
        ),
        on_step=job_context.log,
    )
    job_context.update("Done", 100)
    return done.to_dict()


def _queue(stack: str, domain: str, version: int, session: dict[str, Any]) -> JobAcceptedResponse:
    """
    Queue the application of a version.

    Args:
        stack: The Compose project.
        domain: The application.
        version: The version to apply.
        session: The authenticated payload.

    Returns:
        The queued job.
    """
    who = actor_label(session)
    job = get_job_manager().create_job(
        job_type=JobType.UPDATE,
        name=f"Update {domain} with a new Compose document",
        description=f"Applying version {version} of the Compose document of {domain}",
        func=docker_compose_job,
        kwargs={"domain": domain, "stack": stack, "version": version, "actor": who},
        metadata={"domain": domain, "action": "docker_compose", "version": version},
        actor=who,
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message="Update queued",
        job=job.to_dict(),
    )


@router.get("/stacks/{name}/compose", response_model=DockerComposeOut)
def get_compose(name: str, session: Session) -> DockerComposeOut:
    """
    Read the document of a stack, or say why it is not edited here.

    Needs the permission that reveals secrets: a document can hold a value somebody typed into it. The read
    is recorded.

    Args:
        name: The Compose project's name.
        session: The authenticated session.

    Returns:
        The document and where it is; or ``editable: false`` with a code and a sentence that says whose it is
        and where it is changed.

    Raises:
        DockerNotFoundError: 404 when there is no such stack.
        DockerOwnedError: 409 when a database engine or Noust owns it.
    """
    gate = _gate()
    gate.require_usable(_engine(gate))
    return compose_out(StackEditor(gate).view(name, actor=actor_label(session)))


@router.post("/stacks/{name}/compose/check", response_model=DockerComposeCheckOut)
def check_compose(
    name: str, body: DockerComposeCheckRequest, session: Session
) -> DockerComposeCheckOut:
    """
    Judge a document for a stack without writing anything.

    Compose is asked for the model the document resolves to (``include``, ``extends`` and ``volumes_from``
    count), and that model is what the guard of privileges reads. A document Compose does not read answers
    with Compose's own words and the line. Then what the change would do: the services, whether the images it
    brings in exist, the variables the stack lacks, the difference with what runs and what starting the stack
    from it would recreate, create or remove, from Compose's own rehearsal. The text is not written anywhere.
    Needs the permission to change Docker and the one to reveal secrets: Compose resolves the document with
    the stack's own variables, so an answer can carry the value of one (``image: ${DB_PASSWORD}``), which is
    what reading the document already asks for.

    Args:
        name: The Compose project's name.
        body: The document as it would be saved.
        session: The authenticated session.

    Returns:
        The judgement.

    Raises:
        DockerNotFoundError: 404 when there is no such stack.
        DockerBlockedError: 409 ``docker_blocked`` when its document is not edited here.
        PermissionDenied: 403 for whoever may not reveal secrets.
    """
    check_permission(session, Permission.SECRETS_REVEAL)
    gate = _gate()
    gate.require_usable(_engine(gate))
    return check_out(StackEditor(gate).check(name, body.text))


@router.put("/stacks/{name}/compose", response_model=JobAcceptedResponse, status_code=202)
def save_compose(
    name: str, body: DockerComposeSaveRequest, session: Elevated
) -> JobAcceptedResponse:
    """
    Apply a document to a stack: judged again, kept as a version, then the application's update behind its
    health gate.

    A document that does not pass is refused (409 ``docker_blocked``, ``reason`` ``invalid``) and nothing is
    kept. One that passes is kept as a version with who saved it and why, and a job runs the update with it;
    when the stack does not come up the document before is put back and the job fails saying so, and the
    history has that too. Needs the permission to change Docker, the one to manage applications and sudo mode.

    Args:
        name: The Compose project's name.
        body: The document and why.
        session: The authenticated session, in sudo mode.

    Returns:
        The queued update.

    Raises:
        DockerNotFoundError: 404 when there is no such stack.
        DockerBlockedError: 409 ``docker_blocked``: its document is not edited here, it does not pass, or it
            is what the stack has.
        DeploysFrozenError: 409 while deploys are frozen for the application.
    """
    check_permission(session, Permission.APPS_MANAGE)
    gate = _gate()
    gate.require_usable(_engine(gate))
    editor = StackEditor(gate)
    # A freeze is the first thing said, before anything is kept.
    deploy_guard.require_open(editor.application_of(name), override=False, record=False)
    staged = editor.stage(name, body.text, reason=body.reason, actor=actor_label(session))
    return _queue(name, staged.domain, staged.version, session)


@router.get("/stacks/{name}/compose/versions", response_model=DockerComposeVersionsOut)
def list_compose_versions(name: str, session: Session) -> DockerComposeVersionsOut:
    """
    List the versions of a stack's document, newest first: who saved each, when and why. There is no text.

    Args:
        name: The Compose project's name.
        session: The authenticated session.

    Returns:
        The versions.

    Raises:
        DockerNotFoundError: 404 when there is no such stack.
        DockerBlockedError: 409 ``docker_blocked`` when its document is not edited here.
    """
    gate = _gate()
    gate.require_usable(_engine(gate))
    return DockerComposeVersionsOut(
        items=[
            DockerComposeVersionOut(**item.to_dict()) for item in StackEditor(gate).versions(name)
        ]
    )


@router.get(
    "/stacks/{name}/compose/versions/{version}", response_model=DockerComposeVersionDetailOut
)
def get_compose_version(name: str, version: int, session: Session) -> DockerComposeVersionDetailOut:
    """
    Read one version of a stack's document, with what putting it back would change in what runs now.

    Needs the permission that reveals secrets; the read is recorded.

    Args:
        name: The Compose project's name.
        version: The version's number.
        session: The authenticated session.

    Returns:
        The version, its text and the difference with what runs.

    Raises:
        DockerNotFoundError: 404 when there is no such stack or version.
    """
    gate = _gate()
    gate.require_usable(_engine(gate))
    return version_detail_out(StackEditor(gate).version(name, version))


@router.post(
    "/stacks/{name}/compose/versions/{version}/restore",
    response_model=JobAcceptedResponse,
    status_code=202,
)
def restore_compose_version(
    name: str,
    version: int,
    session: Elevated,
    body: DockerComposeRestoreRequest | None = None,
) -> JobAcceptedResponse:
    """
    Put an earlier version of a stack's document back, by the same road as an edit.

    It is judged like any edit (what no longer passes is refused), kept as a version of its own (``restore``)
    and applied behind the health gate. Needs the permission to change Docker, the one to manage applications
    and sudo mode.

    Args:
        name: The Compose project's name.
        version: The version to go back to.
        session: The authenticated session, in sudo mode.
        body: Why.

    Returns:
        The queued update.

    Raises:
        DockerNotFoundError: 404 when there is no such stack or version.
        DockerBlockedError: 409 ``docker_blocked``: it does not pass, or it is what the stack has.
    """
    check_permission(session, Permission.APPS_MANAGE)
    gate = _gate()
    gate.require_usable(_engine(gate))
    editor = StackEditor(gate)
    reason = (body.reason if body is not None else "") or f"Put back version {version}"
    staged = editor.stage_restore(name, version, reason=reason, actor=actor_label(session))
    return _queue(name, staged.domain, staged.version, session)
