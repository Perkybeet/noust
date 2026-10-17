# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Deploy from a pipeline: one URL per application, one call for several.

``POST /api/apps/{domain}/deploy`` is what CI calls after it built and pushed:
authenticated with a ``deploy``-scope token (``Authorization: Bearer ...``), no
code host involved, so it works for a forge Noust has no webhook for. It queues
the same update every other door queues (:func:`noust.web.api.jobs.queue_update`)
and answers with the job to follow. ``POST /api/deploy`` does it for several
applications in one call, and is the instance's: a token that sees every team.

A deploy can ask for more than "update":

- ``force`` rebuilds even when the branch has nothing new;
- ``no_cache`` builds from scratch;
- ``commit`` or ``tag`` deploys exactly that;
- ``image_tag`` deploys a container application at another tag of its image;
- ``pull_request`` rebuilds the preview of that pull request instead.

The shape is a POST on purpose: a deploy changes the server, and a GET that does
(Coolify's first webhook was one) is a link a crawler or a preview bot follows.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from noust.core import deploy_guard
from noust.core.container_store import ContainerStore
from noust.core.exceptions import DeploysFrozenError, QueueFullError, ValidationError
from noust.core.store import App
from noust.core.teams import scoped_store
from noust.deployers.helpers.container_settings import change_container
from noust.managers.previews import rebuild_preview
from noust.validators.container import METHOD_IMAGE, with_tag
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, strict_domain
from noust.web.api.jobs import queue_update, refuse_if_nothing_new
from noust.web.auth import API_TOKEN_SID_PREFIX, actor_label
from noust.web.pydantic_compat import dump_model

router = APIRouter(route_class=NoustErrorRoute)

#: Most applications one call deploys; a pipeline with more splits it.
MAX_BATCH = 20


class DeployOptions(BaseModel):
    """What a deploy asks for. Everything is optional: an empty body is an update."""

    force: bool = Field(
        default=False, description="Rebuild even when the branch has no commit the live build lacks"
    )
    no_cache: bool = Field(
        default=False,
        description="Build from scratch: no package manager cache, no dependencies taken "
        "from the release that serves, no image layer cache. Implies force",
    )
    commit: str | None = Field(
        default=None, description="Deploy exactly this commit (full or abbreviated)"
    )
    tag: str | None = Field(default=None, description="Deploy the commit this git tag points at")
    image_tag: str | None = Field(
        default=None,
        description="For a container application from a registry image: deploy this tag of "
        "the image it already runs",
    )
    pull_request: int | None = Field(
        default=None,
        description="Rebuild the preview of this pull request instead of the application",
    )
    frozen_ok: bool = Field(
        default=False,
        description="Deploy although deploys are frozen for the application: a person's "
        "decision, recorded in the audit trail",
    )


class BatchDeployRequest(DeployOptions):
    """Several applications at once, with the options they share."""

    apps: list[str] = Field(
        ..., description=f"The applications' domains, at most {MAX_BATCH}", min_length=1
    )


class DeployResult(BaseModel):
    """What came of one application."""

    domain: str
    status: str = Field(
        description="queued; joined (an update already waiting will do it); nothing_new; "
        "queue_full; frozen (deploys are frozen and frozen_ok was not given); paused (a "
        "token's deploy while the application's automatic deploys are paused; force "
        "deploys anyway); not_found; failed"
    )
    job_id: str | None = Field(default=None, description="The job to follow, when one was queued")
    detail: str | None = Field(default=None, description="Why, for anything but queued")


class DeployResponse(BaseModel):
    """What a deploy call did."""

    message: str
    results: list[DeployResult]


def check_options(options: DeployOptions) -> None:
    """
    Refuse a combination of options that asks for two things.

    Args:
        options: What was asked.

    Raises:
        ValidationError: Two options that exclude each other; ``field`` names one.
    """
    exact = [name for name in ("commit", "tag") if getattr(options, name)]
    if len(exact) > 1:
        raise ValidationError(
            "A commit and a tag both name what to deploy",
            details="Give one of them.",
            field="tag",
        )
    if options.pull_request is not None:
        if options.pull_request < 1:
            raise ValidationError(
                "A pull request number is 1 or more",
                details="Give the number the code host shows.",
                field="pull_request",
            )
        others = [
            name for name in ("commit", "tag", "image_tag") if getattr(options, name) is not None
        ]
        if others:
            raise ValidationError(
                f"A pull request preview is rebuilt from its branch: {others[0]} does not apply",
                details="Ask for the preview alone, or deploy the application.",
                field="pull_request",
            )
    if options.image_tag is not None and exact:
        raise ValidationError(
            "An image tag is the version to deploy: a commit or a tag does not apply",
            details="Deploy the image tag, or a commit of the repository, not both.",
            field="image_tag",
        )


def _queue_image_tag(app: App, tag: str, actor: str) -> None:
    """
    Point a registry image application at another tag of its image.

    Args:
        app: The application.
        tag: The tag to deploy.
        actor: Who asked, for the audit record.

    Raises:
        ValidationError: The application does not run a registry image, or the
            tag is not a tag.
    """
    settings = ContainerStore().get(app.domain)
    if settings is None or settings.method != METHOD_IMAGE or not settings.image:
        raise ValidationError(
            f"{app.domain} does not run an image from a registry",
            details="An image tag only means something to an application deployed with --image. "
            "Deploy the others by commit or tag.",
            field="image_tag",
        )
    change_container(app.domain, {"image": with_tag(settings.image, tag)}, actor=actor)


def _is_pipeline(session: dict[str, Any]) -> bool:
    """
    Args:
        session: The authenticated payload.

    Returns:
        Whether a token made the call, which is a machine and not a person at the
        console: what a pause of automatic deploys holds.
    """
    return session.get("type") == "api_token" or str(session.get("sid") or "").startswith(
        API_TOKEN_SID_PREFIX
    )


def deploy_one(
    domain: str, options: DeployOptions, actor: str, *, automatic: bool = False
) -> DeployResult:
    """
    Queue what a deploy asks for, for one application.

    Args:
        domain: A validated domain.
        options: What was asked.
        actor: Who asked.
        automatic: A pipeline asked, not a person: a pause of automatic deploys holds
            it, unless ``force`` says it is meant.

    Returns:
        What came of it. A nothing-to-do or a full queue is a result, not an
        error: a pipeline that deploys several applications wants to see all.

    Raises:
        ValidationError: The options do not apply to this application.
    """
    app = scoped_store().get_app(domain)
    if app is None:
        return DeployResult(domain=domain, status="not_found", detail="No such application")
    try:
        if options.pull_request is not None:
            job = rebuild_preview(domain, options.pull_request, actor=actor)
            return DeployResult(domain=domain, status="queued", job_id=str(job.id))
        gate = (
            deploy_guard.gate_for(domain, automatic=True)
            if automatic and not options.force
            else None
        )
        if gate is not None and gate.kind == "paused":
            return DeployResult(domain=domain, status="paused", detail=gate.message)
        if options.image_tag is not None:
            _queue_image_tag(app, options.image_tag, actor)
        explicit = options.commit is not None or options.tag is not None
        if not (options.force or options.no_cache or explicit or options.image_tag is not None):
            try:
                refuse_if_nothing_new(domain)
            except HTTPException as exc:
                if exc.status_code != 409 or not isinstance(exc.detail, dict):
                    raise
                return DeployResult(
                    domain=domain, status="nothing_new", detail=str(exc.detail.get("detail"))
                )
        job, joined = queue_update(
            domain,
            actor=actor,
            no_cache=options.no_cache,
            commit=options.commit,
            tag=options.tag,
            frozen_ok=options.frozen_ok,
        )
    except QueueFullError as exc:
        return DeployResult(domain=domain, status="queue_full", detail=exc.message)
    except DeploysFrozenError as exc:
        return DeployResult(domain=domain, status="frozen", detail=exc.message)
    return DeployResult(
        domain=domain,
        status="joined" if joined else "queued",
        job_id=str(job.id),
        detail="An update that waits will build it" if joined else None,
    )


def _message(results: list[DeployResult]) -> str:
    """
    Args:
        results: What came of each application.

    Returns:
        One sentence for a person reading a pipeline's log.
    """
    queued = sum(1 for result in results if result.status in ("queued", "joined"))
    if len(results) == 1:
        only = results[0]
        return {
            "queued": "Update job created",
            "joined": "An update of this application is already waiting; this request joined it",
            "nothing_new": "Nothing new to deploy",
        }.get(only.status, only.detail or only.status)
    return f"{queued} of {len(results)} applications queued"


@router.post("/{domain}/deploy", response_model=DeployResponse, status_code=202)
def deploy_app(
    domain: str,
    session: Annotated[dict, Depends(get_current_session)],
    body: DeployOptions | None = None,
) -> Any:
    """
    Deploy one application: the call a pipeline makes when it has built and pushed.

    Args:
        domain: Domain of the application.
        session: The authenticated session or token.
        body: What to deploy, when it is more than the head of the branch.

    Returns:
        202 with the job to follow (``GET /api/jobs/{job_id}`` until it ends);
        200 when there was nothing new to deploy.

    Raises:
        HTTPException: 404 when the application is unknown (or another team's).
        ValidationError: The options exclude each other or do not apply (400,
            with the field).
    """
    options = body or DeployOptions()
    check_options(options)
    validated = strict_domain(domain)
    result = deploy_one(validated, options, actor_label(session), automatic=_is_pipeline(session))
    if result.status == "not_found":
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    if result.status in ("frozen", "paused"):
        # Not a 2xx: a pipeline that reads success would think the deploy happened.
        raise HTTPException(
            status_code=409,
            detail={
                "error": f"deploys_{result.status}",
                "detail": result.detail or f"Deploys are {result.status}",
                "hint": "Lift the freeze (noust deploy unfreeze) or resume the application "
                "(noust deploy resume); a person can also deploy with frozen_ok, a pipeline "
                "with force once its application is paused.",
            },
        )
    if result.status == "queue_full":
        raise_queue_full(result)
    response = DeployResponse(message=_message([result]), results=[result])
    status = 200 if result.status == "nothing_new" else 202
    return JSONResponse(status_code=status, content=dump_model(response))


def raise_queue_full(result: DeployResult) -> None:
    """
    Answer a full deploy queue the way every door does.

    Args:
        result: The result that says it.

    Raises:
        HTTPException: 429, so the pipeline retries.
    """
    raise HTTPException(
        status_code=429,
        detail={
            "error": "queue_full",
            "detail": result.detail or "The deploy queue is full",
            "hint": "Wait for a build to finish, or raise deploy.queue_limit.",
        },
    )


batch_router = APIRouter(route_class=NoustErrorRoute)


@batch_router.post("/deploy", response_model=DeployResponse, status_code=202)
def deploy_apps(
    body: BatchDeployRequest, session: Annotated[dict, Depends(get_current_session)]
) -> Any:
    """
    Deploy several applications in one call.

    Each is its own job; one that has nothing new, is not found or finds the
    queue full is a result of its own and does not stop the others.

    Args:
        body: The applications and the options they share.
        session: The authenticated session or token.

    Returns:
        202 with a result per application; 200 when none was queued.

    Raises:
        ValidationError: The list is too long, or the options exclude each
            other (400, with the field).
    """
    options = DeployOptions(
        **{key: value for key, value in dump_model(body).items() if key != "apps"}
    )
    check_options(options)
    if len(body.apps) > MAX_BATCH:
        raise ValidationError(
            f"{len(body.apps)} applications is more than one call deploys ({MAX_BATCH})",
            details="Split the list over several calls.",
            field="apps",
        )
    domains = list(dict.fromkeys(strict_domain(item) for item in body.apps))
    actor = actor_label(session)
    automatic = _is_pipeline(session)
    results = [deploy_one(domain, options, actor, automatic=automatic) for domain in domains]
    queued = any(result.status in ("queued", "joined") for result in results)
    if all(result.status == "queue_full" for result in results):
        raise_queue_full(results[0])
    response = DeployResponse(message=_message(results), results=results)
    return JSONResponse(status_code=202 if queued else 200, content=dump_model(response))
