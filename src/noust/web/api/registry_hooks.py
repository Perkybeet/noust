# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A container registry's webhook: ``POST /hooks/registry/{domain}`` (checklist A-14).

A CI that only pushes an image to GitHub Container Registry or Docker Hub has no way to tell
Noust to deploy it, and an application that runs a registry image would stay on the old one until
someone updated it. The registry says what it pushed, and this route reads it:

- **GitHub Container Registry** sends a ``registry_package`` event, a GitHub webhook signed with
  the application's webhook secret exactly like a push (``X-Hub-Signature-256``);
- **Docker Hub** signs nothing and cannot send a header, so its address carries a token only the
  secret's owner can make (``/hooks/registry/{domain}/{token}``,
  :func:`noust.core.registry_events.registry_hook_token`): one-way from the secret, so the
  address says nothing of it, and gone when the secret is rotated.

A delivery whose image is not the one the application runs, or whose tag is not its tag, is
acknowledged and ignored. One that is queues the update the way a push does, behind the
application's other operations, merged with an update that already waits, held by a pause or a
freeze of deploys, and (this is the difference) asking first whether the tag now points at a
digest that is not running, so a CI that pushes the same image twice deploys once.

The refusals are the forge hook's (:mod:`noust.web.api.hooks`): a domain without a secret answers
the generic 404 an unknown domain does, wrong credentials are counted against the application
and lock it for a while, and every outcome is in the application's delivery log and the audit log.
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from noust.core import webhook_deliveries as deliveries_log
from noust.core.exceptions import DomainError, NoustError, QueueFullError
from noust.core.registry_events import (
    DOCKER_HUB_PROVIDER,
    GHCR,
    GITHUB_EVENT,
    RegistryEvent,
    parse_dockerhub,
    parse_ghcr,
    token_matches,
    why_not_for,
)
from noust.core.store import App, DeploymentTrigger, get_store
from noust.deployers.container import pulls_image
from noust.web.api import hooks as forge
from noust.web.api.deps import NoustErrorRoute, error_response, strict_domain
from noust.web.jobs import JobContext, JobType, get_job_manager, run_update
from noust.web.server import get_webhook_failures

#: The unauthenticated delivery surface of the registries, mounted at ``/hooks``.
router = APIRouter(route_class=NoustErrorRoute)

_GITHUB_PREFIX = "sha256="


def registry_update_job(domain: str, job_context: JobContext | None = None) -> dict[str, Any]:
    """
    Update an application whose registry says its image moved, if it really did.

    Args:
        domain: The application.
        job_context: Injected by the job manager.

    Returns:
        Summary of the update; ``status`` is ``ignored`` (with a ``reason``) when a pause or a
        freeze began while the update waited its turn, or when the tag points at the digest that
        already runs.

    Raises:
        NoustError: The application is unknown or a step fails.
    """
    from noust.managers import image_updates

    trigger = DeploymentTrigger.WEBHOOK.value
    gate = forge._held(domain)
    if gate is not None:
        if job_context is not None:
            job_context.log(f"Not deploying: {gate.message}")
        return {"domain": domain, "status": "ignored", "trigger": trigger, "reason": gate.message}
    try:
        looked = image_updates.check_image(domain)
    except NoustError as exc:
        # The registry could not be asked from here: the update will say why, with its own words.
        if job_context is not None:
            job_context.log(f"The registry could not be asked ({exc.message}); updating anyway")
    else:
        if job_context is not None:
            job_context.log(looked.reason)
        if not looked.changed:
            return {
                "domain": domain,
                "status": "ignored",
                "trigger": trigger,
                "reason": looked.reason,
            }
    return run_update(domain, trigger=trigger, job_context=job_context)


def _signed_by_github(secret: str, body: bytes, request: Request) -> bool:
    """
    Tell whether a delivery carries GitHub's signature made with the application's secret.

    Args:
        secret: The application's webhook secret.
        body: The raw body.
        request: The delivery.

    Returns:
        Whether ``X-Hub-Signature-256`` is the HMAC of the body, in constant time.
    """
    header = request.headers.get("X-Hub-Signature-256") or ""
    if not header.startswith(_GITHUB_PREFIX):
        return False
    presented = header[len(_GITHUB_PREFIX) :].strip().lower()
    return hmac.compare_digest(forge._hmac_hex(secret, body), presented)


def _queue(app: App, domain: str, event: RegistryEvent, delivery: str | None) -> Any:
    """
    Queue the update a pushed image asks for.

    Args:
        app: The application.
        domain: Its domain.
        event: What the registry said was pushed.
        delivery: The registry's id for the delivery.

    Returns:
        The queued job; one that waits already when the update is merged into it.

    Raises:
        QueueFullError: The deploy queue has no room.
    """
    joined: list[str] = []
    job = get_job_manager().create_job(
        job_type=JobType.UPDATE,
        name=f"Update {domain}",
        description=f"Registry-triggered update of {domain}: {event.reference} was pushed",
        func=registry_update_job,
        kwargs={"domain": domain},
        metadata={
            "domain": domain,
            "trigger": DeploymentTrigger.WEBHOOK.value,
            "provider": event.provider,
            "image": event.reference,
            "delivery": delivery,
        },
        actor="webhook",
        coalesce_key=f"update:{domain}",
        note=f"Another push of {event.reference} arrived: merged, one update pulls what is there",
        on_merge=lambda waiting: joined.append(waiting.id),
    )
    return job, bool(joined)


@router.post("/registry/{domain}")
@router.post("/registry/{domain}/{token}")
async def deliver_registry(domain: str, request: Request, token: str | None = None) -> JSONResponse:
    """
    Accept a registry's notification that an image was pushed and queue the update it asks for.

    Async because the raw body has to be awaited before anything can be verified: GitHub's HMAC
    covers the bytes on the wire.

    Args:
        domain: Domain of the application.
        request: The incoming delivery.
        token: The token of the address Docker Hub is given; GitHub's deliveries carry a signature
            instead.

    Returns:
        202 with the queued job's id; 200 when the delivery is authentic but there is nothing to
        do (a ping, another image, another tag, a digest that already runs, an application
        that does not run a registry image).

    Raises:
        HTTPException: A generic 404 when the domain has no webhook configured or does not
            exist, and 401 with no details when no credential verifies.
    """
    body = await request.body()
    try:
        validated = strict_domain(domain)
    except DomainError:
        forge._record(request, domain, "denied", "not a domain")
        raise HTTPException(status_code=404, detail="Not found") from None

    secret = get_store().get_webhook_secret(validated)
    if not secret:
        forge._record(request, validated, "denied", "unknown domain or webhooks not configured")
        raise HTTPException(status_code=404, detail="Not found")
    app = get_store().get_app(validated)

    failures = get_webhook_failures()
    if failures.is_locked(validated):
        remaining = failures.get_lockout_remaining(validated)
        forge._record(request, validated, "locked", f"refused for {remaining} more seconds")
        forge._log(app, deliveries_log.LOCKED, detail=f"refused for {remaining} more seconds")
        return JSONResponse(
            status_code=429,
            content={
                "error": "locked_out",
                "detail": "Too many deliveries with a wrong signature for this application.",
                "hint": "Check the webhook secret configured at the registry.",
                "fields": None,
            },
            headers={"Retry-After": str(remaining)},
        )

    if _signed_by_github(secret, body, request):
        provider = GHCR
    elif token is not None and token_matches(secret, validated, token):
        provider = DOCKER_HUB_PROVIDER
    else:
        forge._record(request, validated, "denied", "signature or token did not verify")
        forge._log(app, deliveries_log.BAD_SIGNATURE, detail="signature or token did not verify")
        failures.record_failure(validated)
        raise HTTPException(status_code=401, detail="Unauthorized")

    delivery = forge._delivery_id(request)
    if delivery and forge._deliveries.seen(f"{validated}:{delivery}"):
        forge._record(request, validated, "ignored", f"duplicate delivery {delivery} ({provider})")
        forge._log(
            app,
            deliveries_log.DUPLICATE,
            provider=provider,
            detail="the registry's retry of a delivery already handled",
            delivery=delivery,
        )
        return JSONResponse(status_code=200, content={"status": "ignored", "reason": "duplicate"})

    payload = forge._payload(body)
    github_event = (request.headers.get("X-GitHub-Event") or "").strip().lower()
    if provider == GHCR and github_event == "ping":
        forge._log(app, deliveries_log.PING, provider=provider, event="ping", delivery=delivery)
        return JSONResponse(status_code=200, content={"status": "ok", "event": "ping"})
    if provider == GHCR and github_event != GITHUB_EVENT:
        forge._record(request, validated, "ignored", f"{github_event or 'no'} event ({provider})")
        forge._log(
            app,
            deliveries_log.IGNORED_EVENT,
            provider=provider,
            event=github_event or None,
            detail="only registry_package events are acted on here",
            delivery=delivery,
        )
        return JSONResponse(
            status_code=202,
            content={"status": "ignored", "reason": "event", "event": github_event},
        )

    event = parse_ghcr(payload) if provider == GHCR else parse_dockerhub(payload)
    if event is None:
        forge._record(
            request, validated, "ignored", f"no image pushed in the delivery ({provider})"
        )
        forge._log(
            app,
            deliveries_log.IGNORED_EVENT,
            provider=provider,
            event=GITHUB_EVENT if provider == GHCR else "push",
            detail="the delivery names no tagged container image that was published",
            delivery=delivery,
        )
        return JSONResponse(status_code=200, content={"status": "ignored", "reason": "payload"})

    if app is None or not await run_in_threadpool(pulls_image, app):
        forge._record(
            request, validated, "ignored", f"not a registry image application ({provider})"
        )
        forge._log(
            app,
            deliveries_log.IGNORED_EVENT,
            provider=provider,
            event="push",
            detail="the application does not run an image from a registry",
            delivery=delivery,
        )
        return JSONResponse(
            status_code=200, content={"status": "ignored", "reason": "not_an_image_application"}
        )

    from noust.core.container_store import ContainerStore

    settings = await run_in_threadpool(ContainerStore().get, validated)
    reason = why_not_for(settings.image or "", event) if settings is not None else "no settings"
    if reason is not None:
        forge._record(request, validated, "ignored", f"{event.reference}: {reason} ({provider})")
        forge._log(
            app,
            deliveries_log.IGNORED_BRANCH,
            provider=provider,
            event="push",
            branch=event.tag,
            detail=f"{event.reference} was pushed; {reason}",
            delivery=delivery,
        )
        return JSONResponse(
            status_code=200, content={"status": "ignored", "reason": "image", "detail": reason}
        )

    if settings is not None and event.digest is not None and event.digest == settings.digest:
        detail = f"{event.digest} is already running"
        forge._record(request, validated, "ignored", f"{detail} ({provider})")
        forge._log(
            app,
            deliveries_log.IGNORED_TAG,
            provider=provider,
            event="push",
            branch=event.tag,
            detail=detail,
            delivery=delivery,
        )
        return JSONResponse(
            status_code=200, content={"status": "ignored", "reason": "digest", "detail": detail}
        )

    gate = forge._held(validated)
    if gate is not None:
        outcome = forge._held_outcome(gate)
        forge._record(request, validated, "ignored", f"{gate.message} ({provider})")
        forge._log(
            app,
            outcome,
            provider=provider,
            event="push",
            branch=event.tag,
            detail=gate.message,
            delivery=delivery,
        )
        return JSONResponse(
            status_code=200,
            content={"status": "ignored", "reason": "held", "detail": gate.message},
        )

    try:
        job, merged = await run_in_threadpool(_queue, app, validated, event, delivery)
    except QueueFullError as exc:
        detail = f"the deploy queue is full ({exc.limit} waiting); the registry retries"
        forge._log(
            app,
            deliveries_log.QUEUE_FULL,
            provider=provider,
            event="push",
            branch=event.tag,
            detail=detail,
            delivery=delivery,
        )
        forge._record(request, validated, "refused", f"{detail} ({provider})")
        return error_response(exc)
    detail = (
        f"joined the update already waiting (job {job.id}): it pulls what is there"
        if merged
        else f"queued the update ({event.reference} was pushed)"
    )
    forge._log(
        app,
        deliveries_log.DEPLOY_STARTED,
        provider=provider,
        event="push",
        branch=event.tag,
        detail=detail,
        job_id=job.id,
        delivery=delivery,
    )
    forge._record(
        request, validated, "accepted", f"queued job {job.id} ({provider}, {event.reference})"
    )
    return JSONResponse(
        status_code=202, content={"job_id": job.id, "status": "pending", "merged": merged}
    )
