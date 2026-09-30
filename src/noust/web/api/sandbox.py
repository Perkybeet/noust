# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application's build sandbox (backlog 44): see it, try it, turn it on or off.

A client of :mod:`noust.deployers.helpers.sandbox`, like ``noust app sandbox``:
``GET`` reads the regime and the warning the application page shows; ``POST
.../test`` queues a trial build of the current commit as a job, because a
build takes minutes; ``enable`` turns the sandbox on (after a passing trial,
unless forced); ``disable`` records the decision to build as root and, since
that hands root to the repository's install scripts, needs sudo mode; so does
allowing a compose stack privileged containers. The module records every
change in the audit trail, whoever made it.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.store import get_store
from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.recorder import CapturingLogger
from noust.web.api.auth import get_current_session
from noust.web.api.deps import (
    JobAcceptedResponse,
    NoustErrorRoute,
    require_elevated,
    strict_domain,
)
from noust.web.auth import actor_label
from noust.web.jobs import JobContext, JobType, get_job_manager

router = APIRouter(route_class=NoustErrorRoute)


class SandboxTrial(BaseModel):
    """The last trial build of an application in the sandbox."""

    tested_at: str = Field(description="When it ran, ISO 8601")
    commit: str | None = Field(default=None, description="The commit it built")
    passed: bool = Field(description="Whether it installed and built")
    detail: str | None = Field(
        default=None, description="The build's own output when it failed, verbatim"
    )


class ComposeExceptionModel(BaseModel):
    """A compose stack allowed privileged containers and the Docker socket."""

    reason: str = Field(description="Why, in the operator's words")
    allowed_by: str = Field(description="Who allowed it")
    allowed_at: str = Field(description="When, ISO 8601")


class SandboxResponse(BaseModel):
    """How an application builds."""

    domain: str
    mode: str = Field(
        description="on: in the sandbox as noust-build; off: as root by an operator's "
        "decision; legacy: as root, as applications from before 3.1"
    )
    enabled: bool = Field(description="Whether its builds run in the sandbox")
    network: str = Field(
        description="full, or strict: install without its variables, build without a network"
    )
    pty: bool = Field(description="Builds run on a terminal (the compatibility mode)")
    reason: str | None = Field(default=None, description="Why it builds as root, when off")
    changed_by: str | None = Field(default=None, description="Who last changed the regime")
    changed_at: str | None = Field(default=None, description="When, ISO 8601")
    trial: SandboxTrial | None = Field(default=None, description="The last trial build")
    warning: str | None = Field(
        default=None, description="What is unprotected about its builds, and what to do"
    )
    compose_exception: ComposeExceptionModel | None = Field(
        default=None, description="For a compose stack: what it was allowed, and why"
    )


class SandboxEnableRequest(BaseModel):
    """Turning the sandbox on."""

    force: bool = Field(default=False, description="Enable without a passing trial build")
    network: str | None = Field(
        default=None, description="full or strict; omitted keeps the application's"
    )
    pty: bool | None = Field(default=None, description="The compatibility mode; omitted keeps it")


class SandboxDisableRequest(BaseModel):
    """Building as root, and why."""

    reason: str = Field(min_length=1, max_length=500, description="Why, recorded and shown")


class ComposeExceptionRequest(BaseModel):
    """Allowing a compose stack what is root on the host, and why."""

    reason: str = Field(min_length=1, max_length=500, description="Why, recorded and shown")


def _response(domain: str) -> SandboxResponse:
    """
    Read an application's regime into its API model.

    Args:
        domain: A validated domain whose application exists.

    Returns:
        The model.
    """
    store = get_store()
    app = store.get_app(domain)
    state = build_sandbox.get_state(domain, store=store)
    exception = build_sandbox.get_compose_exception(domain, store=store)
    return SandboxResponse(
        domain=domain,
        mode=state.mode,
        enabled=state.enabled,
        network=state.network,
        pty=state.pty,
        reason=state.reason,
        changed_by=state.changed_by,
        changed_at=state.changed_at,
        trial=SandboxTrial(
            tested_at=state.tested_at,
            commit=state.tested_commit,
            passed=bool(state.test_passed),
            detail=state.test_detail,
        )
        if state.tested_at
        else None,
        warning=build_sandbox.sandbox_warning(app, state) if app is not None else None,
        compose_exception=ComposeExceptionModel(
            reason=exception.reason,
            allowed_by=exception.allowed_by,
            allowed_at=exception.allowed_at,
        )
        if exception is not None
        else None,
    )


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
    if get_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


@router.get("/{domain}/sandbox", response_model=SandboxResponse)
def get_sandbox(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> SandboxResponse:
    """
    Show how an application builds. Changes nothing.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        Its regime, its last trial and what is unprotected about it.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    return _response(_known(domain))


def sandbox_test_job(domain: str, job_context: JobContext | None = None) -> dict[str, Any]:
    """
    Build an application's current commit in the sandbox, as a background job.

    Args:
        domain: Domain of the application.
        job_context: Injected by the job manager.

    Returns:
        Whether it built, the commit, the build's output when it did not,
        what it built (``commit``, or the in-place ``working tree``) and the
        uncommitted files of that tree it built as they are.

    Raises:
        ValidationError: It cannot be tried in the sandbox.
        AppBusyError: Another operation is running on the application.
    """
    from noust.deployers.helpers.sandbox_trial import run_trial

    logger = CapturingLogger(verbose=False)
    if job_context is not None:
        job_context.set_metadata("domain", domain)
        job_context.update("Building the current commit in the sandbox", 10)
        logger.attach_sink(job_context.log)
    result = run_trial(domain, logger=logger)
    if job_context is not None:
        job_context.update("Done", 100)
    return {
        "domain": result.domain,
        "passed": result.passed,
        "commit": result.commit,
        "detail": result.detail,
        "source": result.source,
        "uncommitted": list(result.uncommitted),
    }


@router.post("/{domain}/sandbox/test", response_model=JobAcceptedResponse, status_code=202)
def post_sandbox_test(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> JobAcceptedResponse:
    """
    Queue a trial build of the application's current commit in the sandbox.

    Nothing is activated, restarted or recorded in the deployment history;
    the outcome is kept, and enabling the sandbox asks for a passing one.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The queued job.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: A compose stack (400).
    """
    validated = _known(domain)
    app = get_store().get_app(validated)
    if app is not None:
        build_sandbox.refuse_unsupported(app)
    job = get_job_manager().create_job(
        job_type=JobType.SANDBOX_TEST,
        name=f"Sandbox trial of {validated}",
        description=f"Building the current commit of {validated} in the sandbox",
        func=sandbox_test_job,
        kwargs={"domain": validated},
        metadata={"domain": validated},
        actor=actor_label(session),
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Sandbox trial of {validated} queued",
        job=job.to_dict(),
    )


@router.post("/{domain}/sandbox/enable", response_model=SandboxResponse)
def post_sandbox_enable(
    domain: str,
    body: SandboxEnableRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> SandboxResponse:
    """
    Build the application in the sandbox from its next deploy on.

    Args:
        domain: Domain of the application.
        body: Whether to skip the trial, and the profile.
        session: The authenticated session.

    Returns:
        The new regime.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: No passing trial and no ``force``, or a type whose
            builds do not run in the sandbox (400).
    """
    validated = _known(domain)
    build_sandbox.enable(
        validated,
        actor=actor_label(session),
        force=body.force,
        network=body.network,
        pty=body.pty,
    )
    return _response(validated)


@router.post("/{domain}/sandbox/disable", response_model=SandboxResponse)
def post_sandbox_disable(
    domain: str,
    body: SandboxDisableRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> SandboxResponse:
    """
    Build the application as root from now on: recorded, with the reason.

    Its dependencies' install scripts then run with root's access to the
    server, so this needs sudo mode.

    Args:
        domain: Domain of the application.
        body: Why.
        session: The authenticated, elevated session.

    Returns:
        The new regime.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known(domain)
    build_sandbox.disable(validated, actor=actor_label(session), reason=body.reason)
    return _response(validated)


@router.put("/{domain}/sandbox/compose-exception", response_model=SandboxResponse)
def put_compose_exception(
    domain: str,
    body: ComposeExceptionRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> SandboxResponse:
    """
    Allow a compose stack privileged containers and the Docker socket.

    Either is root on the server and the compose file comes from the
    repository, so this needs sudo mode and a reason.

    Args:
        domain: Domain of the application.
        body: Why.
        session: The authenticated, elevated session.

    Returns:
        The application's regime, with the exception.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known(domain)
    build_sandbox.set_compose_exception(
        validated, allowed=True, actor=actor_label(session), reason=body.reason
    )
    return _response(validated)


@router.delete("/{domain}/sandbox/compose-exception", response_model=SandboxResponse)
def delete_compose_exception(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> SandboxResponse:
    """
    Stop allowing a compose stack privileged containers and the Docker socket.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The application's regime, without the exception.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known(domain)
    build_sandbox.set_compose_exception(validated, allowed=False, actor=actor_label(session))
    return _response(validated)
