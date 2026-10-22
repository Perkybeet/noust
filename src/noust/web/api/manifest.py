# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Previewing and applying a manifest from the console (J-15).

The same engine the command line uses (:mod:`noust.manifest`), run on the
server as a client of its own API: every read and every change it makes is a
request to the console's routes carrying the caller's own credentials
(:mod:`noust.web.gateway_client`), so what a plan can see and what an apply can
do is what the caller can, and an application of another team is simply absent
from it. A preview changes nothing and carries no value of any variable.

``POST /api/manifest/apply`` queues the apply as a job
(:func:`noust.web.manifest_jobs.apply_manifest_job`) and answers at once. It
asks for sudo mode, because a manifest is several root-equivalent changes in
one, and it carries the digest of the preview its author saw: if the plan has
changed since, it is refused with the new one rather than applied. Which of the
calls it makes need more (the variables' sudo mode, an approval, a role in the
team an application is in) is decided by those routes, call by call, and a
refusal stops the run where it happened.

Variables that a manifest reads from the environment or from a file are refused
here: a server does not read its own environment on behalf of a document it was
sent. A manifest that has them is applied from the machine that has them
(``noust manifest apply``).
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from noust.client.errors import RemoteError
from noust.core.audit import record as audit_record
from noust.manifest import ManifestError, RefusingResolver, parse, plan
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated
from noust.web.auth import actor_label
from noust.web.gateway_client import ForwardedCaller, GatewayTransport
from noust.web.jobs import JobStatus, JobType, get_job_manager
from noust.web.manifest_jobs import apply_manifest_job
from noust.web.permissions.principal import actor_of

router = APIRouter(route_class=NoustErrorRoute)


class PlanRequest(BaseModel):
    """Request to preview a manifest."""

    manifest: str = Field(..., max_length=1024 * 1024, description="The manifest, as YAML or JSON")


class ActionOut(BaseModel):
    """
    One change a manifest asks for.

    Attributes:
        kind: ``project.create``, ``environment.create``, ``app.create``, ``app.move``,
            ``app.branch``, ``app.limits``, ``app.health``, ``app.domain``, ``app.database``,
            ``app.env``, ``app.backups``, ``cron.set``, ``cron.state``, ``server.team``,
            ``server.labels`` or ``placement.set``.
        target: What it is done to (``SERVER:target`` when it is on another server).
        summary: What it does, with no value of any variable and no cron command.
        elevated: Applying it needs sudo mode.
        unverified: Whether it is needed could not be checked.
        server: The server of the fleet it is done on, through this central; null for this one.
    """

    kind: str
    target: str
    summary: str
    elevated: bool = False
    unverified: bool = False
    server: str | None = None


class PlanResponse(BaseModel):
    """
    What applying a manifest would change.

    Attributes:
        empty: Nothing would.
        digest: What the preview says, in one hash: send it with the apply so
            it is refused if the plan has changed since.
        actions: The changes, in the order they would run.
        warnings: Differences that are not reconciled, and what could not be checked.
        unmanaged: Applications and cron jobs (``cron:NAME``) the caller sees that the
            manifest does not name.
        counts: How many actions of each kind.
    """

    empty: bool
    digest: str
    actions: list[ActionOut]
    warnings: list[str]
    unmanaged: list[str]
    counts: dict[str, int]


async def _read_plan(manifest: Any, caller: ForwardedCaller) -> Any:
    """
    Compare a manifest with the server, as the caller, reading only.

    Args:
        manifest: A parsed manifest.
        caller: Whose credentials the reads carry.

    Returns:
        The plan, with its variables listed unverified: comparing them is a
        change request to the server (a dry run) that needs sudo mode.

    Raises:
        HTTPException: In the words and with the status of the route that refused a read.
    """
    transport = GatewayTransport(caller)
    try:
        # The plan makes several reads, one after another, each a request to this very
        # application: they run in a worker thread so the loop stays free to answer them.
        return await run_in_threadpool(
            plan, manifest, transport, RefusingResolver(), compare_env=False
        )
    except RemoteError as exc:
        # The route the plan read refused the caller: say so in its own words and status.
        raise HTTPException(
            status_code=exc.status or 502,
            detail={
                "error": exc.code,
                "detail": exc.message,
                "hint": exc.details or None,
                "fields": None,
            },
        ) from exc


@router.post("/plan", response_model=PlanResponse)
async def preview(
    request: Request,
    body: PlanRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> PlanResponse:
    """
    Compare a manifest with the server, as the caller, and change nothing.

    Args:
        request: The request.
        body: The manifest.
        session: The authenticated session.

    Returns:
        The plan.

    Raises:
        ManifestError: 400, naming the field of the manifest that is not valid.
        RemoteError: When the caller may not read something the plan needs.
    """
    manifest = parse(body.manifest)
    caller = ForwardedCaller.of(request, asyncio.get_running_loop())
    result = await _read_plan(manifest, caller)
    return PlanResponse(**result.to_dict())


class ManifestApplyRequest(BaseModel):
    """
    Request to apply a manifest.

    Attributes:
        manifest: The manifest, as YAML or JSON, with every variable's value written in it.
        expect: The digest of the preview its author saw. When it is given and the
            plan is no longer the same, nothing is applied.
        restart: Restart the applications whose variables changed.
    """

    manifest: str = Field(..., max_length=1024 * 1024, description="The manifest, as YAML or JSON")
    expect: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        description="The digest of the preview that was read; refused if the plan has changed",
    )
    # Optional, not defaulted to False: a client that does not mean to restart leaves it out, and
    # the generated types then do not ask it to say so.
    restart: bool | None = Field(
        default=None, description="Restart the applications whose variables changed"
    )


class ManifestApplyResponse(BaseModel):
    """
    What asking to apply a manifest did.

    Attributes:
        queued: Whether a job was queued; false when the server already matches.
        message: One sentence.
        digest: The plan's digest.
        job_id: The job to follow (``GET /api/jobs/{id}``), when one was queued.
        status: The job's status when the request returned.
        job: The job's snapshot, the same shape the jobs API returns.
    """

    queued: bool
    message: str
    digest: str
    job_id: str | None = None
    status: str | None = None
    job: dict[str, Any] = Field(default_factory=dict)


def _refuse_changes_made_elsewhere(digest: str, expected: str | None) -> None:
    """
    Refuse to apply a plan its author did not see.

    Args:
        digest: The plan's digest now.
        expected: The digest the author previewed, if they said.

    Raises:
        HTTPException: 409 ``plan_changed``.
    """
    if expected is None or expected == digest:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "error": "plan_changed",
            "detail": "The plan is not the one you previewed: the server or the manifest changed.",
            "hint": "Preview it again, read what it would change now, and apply that.",
            "fields": None,
        },
    )


@router.post("/apply", response_model=ManifestApplyResponse, status_code=202)
async def apply(
    request: Request,
    response: Response,
    body: ManifestApplyRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> ManifestApplyResponse:
    """
    Queue the application of a manifest, made as the caller.

    Creates what is missing and brings what exists to what is written, with the
    calls the caller would make by hand; it never deletes anything. The calls
    are checked by the routes they reach, so the caller's role, team, sudo mode
    and approvals decide each step, and a refusal stops the run there. Follow
    the job: it waits for the deployments it queues.

    Args:
        request: The request.
        response: To answer 200 when there is nothing to apply.
        body: The manifest, the digest previewed, and whether to restart.
        session: The authenticated, elevated session.

    Returns:
        The job, or the news that the server already matches the manifest.

    Raises:
        ManifestError: 400, naming the field that is not valid, or because a
            variable is read from an environment or a file.
        HTTPException: 409 ``plan_changed`` when the plan is not the one previewed,
            or ``apply_running`` while another manifest is being applied; the
            status of the route that refused a read.
    """
    manifest = parse(body.manifest)
    unreadable = manifest.secrets()
    if unreadable:
        domain, name, secret = unreadable[0]
        raise ManifestError(
            f"{domain}: {name} is read from {secret.kind.removeprefix('from_')} "
            f"{secret.source}, which this server cannot do for a document it was sent",
            details="Write the value in the manifest, or apply it from the machine that has it: "
            "noust --context NAME manifest apply FILE.",
            field="env",
        )
    manager = get_job_manager()
    running = [
        job
        for job in manager.get_active_jobs()
        if job.type is JobType.MANIFEST and job.status in (JobStatus.PENDING, JobStatus.RUNNING)
    ]
    if running:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "apply_running",
                "detail": f"Another manifest is being applied (job {running[0].id}).",
                "hint": "Wait for it to finish, then apply again: the plan then holds what is left.",
                "fields": None,
            },
        )
    caller = ForwardedCaller.of(request, asyncio.get_running_loop())
    result = await _read_plan(manifest, caller)
    _refuse_changes_made_elsewhere(result.digest, body.expect)
    if result.empty:
        response.status_code = 200
        return ManifestApplyResponse(
            queued=False, message="The server already matches the manifest.", digest=result.digest
        )
    actor = actor_label(session)
    audit_record(
        "manifest.apply",
        actor=actor_of(session),
        target=f"manifest:{result.digest[:12]}",
        details={"actions": result.to_dict()["counts"], "restart": bool(body.restart)},
    )
    changes = len(result.actions)
    job = manager.create_job(
        job_type=JobType.MANIFEST,
        name="Apply a manifest",
        description=f"Applying a manifest: {changes} change{'' if changes == 1 else 's'}",
        func=apply_manifest_job,
        kwargs={"manifest": body.manifest, "caller": caller, "restart": bool(body.restart)},
        metadata={"digest": result.digest, "changes": changes},
        actor=actor,
    )
    return ManifestApplyResponse(
        queued=True,
        message=f"Applying {changes} change{'' if changes == 1 else 's'}",
        digest=result.digest,
        job_id=job.id,
        status=job.status.value,
        job=job.to_dict(),
    )
