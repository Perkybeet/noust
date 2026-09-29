# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
GitHub App webhook deliveries: ``POST /hooks/github`` (2.2).

One endpoint for every application, authenticated by the App's own webhook
secret (HMAC-SHA256 of the raw body, compared in constant time), with the
same defences the per-application hook has (:mod:`noust.web.api.hooks`): a
delivery - by its id and by its signed body - is honoured once, wrong
signatures are refused for a while without locking out the forge's address
or ever refusing a right one, and every outcome is audited without the
signature or the secret.

- ``ping``: 200; GitHub sends it when the webhook is switched on, which is
  how the console learns that it is.
- ``push``: the update the per-application webhook queues, for every
  application that deploys the pushed branch of the pushed repository.
- ``pull_request``: handed to the previews.
- ``installation`` and ``installation_repositories``: the stored
  installations follow.
- anything else: 202, ignored.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from noust.core.exceptions import NoustError
from noust.core.forge_events import PushEvent, parse_pull_request
from noust.core.secrets import SecretStore
from noust.core.store import DeploymentTrigger, get_store
from noust.integrations.github import webhooks
from noust.integrations.github.app import WEBHOOK_SECRET, read_meta, write_meta
from noust.integrations.github.service import github_hooks_url
from noust.web.api.deps import NoustErrorRoute
from noust.web.api.hooks import DeliveryCache, webhook_update_job
from noust.web.auth import get_audit_logger, get_client_ip
from noust.web.jobs import JobType, get_job_manager
from noust.web.server import get_webhook_failures

logger = logging.getLogger(__name__)

router = APIRouter(route_class=NoustErrorRoute)

#: The key wrong signatures are counted under: the App is one "application"
#: as far as the lockout is concerned.
LOCKOUT_KEY = "github-app"

#: What is audited as the resource.
RESOURCE = "/hooks/github"

_deliveries = DeliveryCache()


def _record(request: Request, result: str, detail: str) -> None:
    """
    Write one delivery outcome to the audit log.

    Args:
        request: The delivery.
        result: ``accepted``, ``ignored``, ``denied`` or ``locked``.
        detail: Context; never a secret or a signature.
    """
    audit = get_audit_logger()
    if audit:
        audit.record(
            action="hooks.github",
            result=result,
            client_ip=get_client_ip(request),
            resource=RESOURCE,
            detail=detail,
        )


def _replay_key(body: bytes, signature: str) -> str:
    """
    Name a signed delivery by what its signature covers.

    Args:
        body: The raw body.
        signature: ``X-Hub-Signature-256``, already verified.

    Returns:
        A key for :data:`_deliveries` that no delivery id can collide with.
    """
    digest = hashlib.sha256(signature.encode() + b"\n" + body).hexdigest()
    return f"body:{digest}"


def _note_webhook_works() -> None:
    """
    Remember that GitHub delivers to this server's public hooks URL.

    A verified delivery is the proof: an App created before the server had a
    public URL only gets its webhook switched on by hand, on GitHub.
    """
    url = github_hooks_url()
    meta = read_meta()
    if url and (not meta.get("webhook_active") or meta.get("webhook_url") != url):
        write_meta(webhook_active=True, webhook_url=url)


def _queue_updates(
    push: PushEvent, default_branch: str | None, delivery: str | None
) -> list[dict[str, Any]]:
    """
    Queue the update of every application that follows a push.

    Args:
        push: The push.
        default_branch: The repository's default branch.
        delivery: GitHub's delivery id, for the job's metadata.

    Returns:
        ``domain`` and ``job_id`` of each job queued.
    """
    queued = []
    for app in webhooks.apps_following(push, default_branch):
        job = get_job_manager().create_job(
            job_type=JobType.UPDATE,
            name=f"Update {app.domain}",
            description=f"GitHub push to {push.repository}@{push.branch}",
            func=webhook_update_job,
            kwargs={"domain": app.domain},
            metadata={
                "domain": app.domain,
                "trigger": DeploymentTrigger.WEBHOOK.value,
                "provider": "github-app",
                "branch": push.branch,
                "commit": push.head_sha,
                "delivery": delivery,
            },
            actor="webhook",
        )
        queued.append({"domain": app.domain, "job_id": job.id})
    return queued


@router.post("/github")
async def deliver(request: Request) -> JSONResponse:
    """
    Accept a delivery of this server's GitHub App.

    Args:
        request: The delivery.

    Returns:
        What was done: ``{"status": "queued", "jobs": [{"domain", "job_id"}]}``
        (202) for a push that updates applications, ``{"status": "accepted",
        "jobs": [...]}`` (202) for a pull request, ``{"status": "ignored",
        "reason": ...}`` otherwise, ``{"status": "ok"}`` for a ping or an
        installation change.

    Raises:
        HTTPException: 404 when this server has no App or its webhook has no
            secret; 401 when the signature does not verify.
    """
    body = await request.body()

    secret = SecretStore().read(WEBHOOK_SECRET)
    if get_store().get_github_app() is None or not secret:
        _record(request, "denied", "no GitHub App or no webhook secret")
        raise HTTPException(status_code=404, detail="Not found")

    signature = request.headers.get("X-Hub-Signature-256")
    # The signature is checked before the lockout: a right one is always
    # accepted, so strangers posting bad signatures (the URL is public) can
    # never stop GitHub's own deliveries. Only wrong ones are counted.
    if not webhooks.verify_signature(secret, body, signature):
        failures = get_webhook_failures()
        if failures.is_locked(LOCKOUT_KEY):
            remaining = failures.get_lockout_remaining(LOCKOUT_KEY)
            _record(request, "locked", f"refused for {remaining} more seconds")
            return JSONResponse(
                status_code=429,
                content={
                    "error": "locked_out",
                    "detail": "Too many deliveries with a wrong signature.",
                    "hint": "Check the webhook secret of the GitHub App.",
                    "fields": None,
                },
                headers={"Retry-After": str(remaining)},
            )
        _record(request, "denied", "signature verification failed")
        failures.record_failure(LOCKOUT_KEY)
        raise HTTPException(status_code=401, detail="Unauthorized")

    delivery = request.headers.get("X-GitHub-Delivery")
    # X-GitHub-Delivery is not covered by the signature, so a captured
    # delivery replayed under a fresh id would pass the id check alone; the
    # signed body and its signature are what cannot change.
    if (delivery and _deliveries.seen(delivery)) or _deliveries.seen(
        _replay_key(body, signature or "")
    ):
        _record(request, "ignored", f"duplicate delivery {delivery}")
        return JSONResponse(status_code=200, content={"status": "ignored", "reason": "duplicate"})

    _note_webhook_works()
    event = request.headers.get("X-GitHub-Event", "")
    try:
        payload = json.loads(body) if body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = None
    if not isinstance(payload, dict):
        _record(request, "ignored", f"{event}: body is not a JSON object")
        return JSONResponse(status_code=400, content={"status": "ignored", "reason": "not_json"})

    if event == "ping":
        _record(request, "accepted", "ping")
        return JSONResponse(status_code=200, content={"status": "ok", "event": "ping"})

    if event == "push":
        push = webhooks.parse_push(payload)
        if push is None:
            _record(request, "ignored", "push to no branch")
            return JSONResponse(
                status_code=200, content={"status": "ignored", "reason": "not_a_branch"}
            )
        repository = payload.get("repository")
        default = repository.get("default_branch") if isinstance(repository, dict) else None
        jobs = await run_in_threadpool(_queue_updates, push, default, delivery)
        if not jobs:
            _record(request, "ignored", f"push to {push.repository}@{push.branch}: no application")
            return JSONResponse(
                status_code=200, content={"status": "ignored", "reason": "no_application"}
            )
        domains = ", ".join(job["domain"] for job in jobs)
        _record(request, "accepted", f"push to {push.repository}@{push.branch}: {domains}")
        return JSONResponse(status_code=202, content={"status": "queued", "jobs": jobs})

    if event == "pull_request":
        pull = parse_pull_request("github", payload)
        if pull is None:
            _record(request, "ignored", f"pull_request {payload.get('action')}")
            return JSONResponse(status_code=200, content={"status": "ignored", "reason": "action"})
        # Imported here: the previews manager imports the deployers, which
        # this router has no other reason to load at start-up.
        from noust.managers.previews import handle_pull_request

        job_ids = await run_in_threadpool(handle_pull_request, pull)
        _record(
            request,
            "accepted",
            f"pull_request {pull.action.value} {pull.repository}#{pull.number}: "
            f"{len(job_ids)} job(s)",
        )
        return JSONResponse(
            status_code=202,
            content={"status": "accepted", "jobs": [{"job_id": job_id} for job_id in job_ids]},
        )

    if event in ("installation", "installation_repositories"):
        try:
            done = await run_in_threadpool(webhooks.apply_installation_event, event, payload)
        except NoustError as exc:
            _record(request, "ignored", f"{event}: {exc.message}")
            return JSONResponse(status_code=200, content={"status": "ignored", "reason": "payload"})
        _record(request, "accepted", f"{event} {payload.get('action')}: {done}")
        return JSONResponse(status_code=200, content={"status": "ok", "installation": done})

    _record(request, "ignored", f"event {event or 'none'}")
    return JSONResponse(status_code=202, content={"status": "ignored", "reason": "event"})
