# Copyright (c) 2024-2026 Yago López Prado
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
  application that deploys the pushed branch of the pushed repository; each
  one is kept in that application's delivery log
  (:mod:`noust.core.webhook_deliveries`) too.
- ``release`` (published, not a draft or a pre-release) and ``push`` of a tag:
  the update to that tag, for every application of that repository that
  follows tags (:func:`noust.web.api.hooks.queue_tag_update` decides for each);
  a push to a branch does not update them.
- ``pull_request``: handed to the previews.
- ``installation`` and ``installation_repositories``: the stored
  installations follow.
- anything else: 202, ignored.

A central that manages servers does the same for theirs (item 90): a ``push``, a tag and a
``pull_request`` are also written down for each server that deploys the repository
(:mod:`noust.fleet.sources.forward`), and the answer lists them as ``forwarded``. The App is the
central's, and a central that is a ``hub`` has no application of its own, so it only forwards. What
cannot be written down is logged and never changes what GitHub is answered.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from noust.core.exceptions import NoustError, QueueFullError
from noust.core.forge_events import (
    PullRequestEvent,
    PushEvent,
    TagEvent,
    event_kind,
    parse_pull_request,
    parse_tag_event,
)
from noust.core.github_host import configured_host
from noust.core.secrets import SecretStore
from noust.core.store import get_store
from noust.fleet.sources.worker import get_worker
from noust.integrations.github import webhooks
from noust.integrations.github.app import WEBHOOK_SECRET, read_meta, write_meta
from noust.integrations.github.service import github_hooks_url
from noust.web.api.deps import NoustErrorRoute, error_response
from noust.web.api.hooks import DeliveryCache, queue_push_update, queue_tag_update
from noust.web.auth import get_audit_logger, get_client_ip
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


def queue_updates(
    push: PushEvent,
    default_branch: str | None,
    delivery: str | None,
    *,
    hosts: Sequence[str] | None = None,
    through: str = "the GitHub App",
) -> tuple[list[dict[str, Any]], list[QueueFullError], list[dict[str, Any]]]:
    """
    Queue the update of every application that follows a push.

    The one way a push is queued, the application's own webhook's
    (:func:`noust.web.api.hooks.queue_push_update`): behind a build that is
    running, merged into an update that already waits, or refused when the
    queue has no room. A push that a central forwarded (``/api/sources/events``) is queued by
    this very function.

    Args:
        push: The push.
        default_branch: The repository's default branch.
        delivery: The repository's default branch, and GitHub's delivery id,
            for the job's metadata.
        hosts: The hosts whose repositories count; this server's GitHub integration's by default
            (a central names the host its App is on).
        through: What the delivery log says delivered it.

    Returns:
        ``domain`` and ``job_id`` of each job queued (or joined), why the
        queue had no room for each that it had not, and the ``domain``,
        ``reason`` and ``detail`` of each application that declined the push
        (skip markers, watch paths).
    """
    queued: list[dict[str, Any]] = []
    refused: list[QueueFullError] = []
    declined: list[dict[str, Any]] = []
    for app in webhooks.apps_following(push, default_branch, hosts):
        pushed = queue_push_update(
            app,
            app.domain,
            provider="github-app",
            branch=push.branch,
            delivery=delivery,
            description=f"GitHub push to {push.repository}@{push.branch}",
            started=f"push to {push.repository}@{push.branch} through {through}",
            commit=push.head_sha,
            change=push.change,
        )
        if pushed.refusal is not None:
            refused.append(pushed.refusal)
        elif pushed.job_id is not None:
            queued.append({"domain": app.domain, "job_id": pushed.job_id, "merged": pushed.merged})
        else:
            declined.append(
                {
                    "domain": app.domain,
                    "reason": pushed.reason,
                    "detail": pushed.detail,
                }
            )
    return queued, refused, declined


def queue_tag_updates(
    event: TagEvent, delivery: str | None, *, hosts: Sequence[str] | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Decide, for every application that follows tags in a repository, what a tag means.

    Args:
        event: The release or tag push.
        delivery: GitHub's delivery id.
        hosts: The hosts whose repositories count; this server's GitHub integration's by default.

    Returns:
        The ``domain`` and ``job_id`` of each job queued, and the ``domain``
        and ``reason`` of each application that ignored the tag.
    """
    queued: list[dict[str, Any]] = []
    ignored: list[dict[str, Any]] = []
    for app in webhooks.apps_following_tags(event, hosts):
        result = queue_tag_update(app, event, provider="github-app", delivery=delivery)
        if result.job_id is not None:
            queued.append({"domain": app.domain, "job_id": result.job_id})
        else:
            ignored.append({"domain": app.domain, "reason": result.detail})
    return queued, ignored


def forward_event(
    event: PushEvent | TagEvent | PullRequestEvent, delivery: str | None, default_branch: str | None
) -> list[dict[str, str]]:
    """
    Write an event down for the servers of this central that deploy its repository.

    The delivery is answered to GitHub whatever becomes of this: what could not be written down
    is logged, and what was is handed to the servers by the worker, not by this request.

    Args:
        event: The push, the tag or the pull request.
        delivery: GitHub's delivery id.
        default_branch: The repository's default branch, for a push.

    Returns:
        ``node`` and ``id`` of each delivery owed; none when this central manages no server that
        deploys the repository.
    """
    try:
        worker = get_worker()
        owed = worker.forwarder.enqueue(
            event,
            delivery=delivery or "none",
            host=configured_host(),
            default_branch=default_branch,
        )
    except (NoustError, sqlite3.Error) as exc:
        logger.warning(
            "Could not forward %s of %s to the servers: %s",
            event_kind(event),
            event.repository,
            exc,
        )
        return []
    if owed:
        worker.wake()
    return [{"node": item.node, "id": item.id} for item in owed]


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

    if event in ("push", "release"):
        announced = parse_tag_event("github", event, payload)
        if announced is not None:
            queued, ignored = await run_in_threadpool(queue_tag_updates, announced, delivery)
            forwarded = await run_in_threadpool(forward_event, announced, delivery, None)
            if queued or forwarded:
                domains = ", ".join(
                    [job["domain"] for job in queued]
                    + [f"{f['node']} (forwarded)" for f in forwarded]
                )
                _record(request, "accepted", f"{event} of {announced.tag}: {domains}")
                return JSONResponse(
                    status_code=202,
                    content={
                        "status": "queued",
                        "jobs": queued,
                        "ignored": ignored,
                        **({"forwarded": forwarded} if forwarded else {}),
                    },
                )
            if ignored:
                _record(
                    request, "ignored", f"{event} of {announced.tag}: no application deploys it"
                )
                return JSONResponse(
                    status_code=200,
                    content={"status": "ignored", "reason": "tag", "ignored": ignored},
                )
            # No application follows tags here: what a push or a release was
            # before tags is what it still is, below.

    if event == "push":
        push = webhooks.parse_push(payload)
        if push is None:
            _record(request, "ignored", "push to no branch")
            return JSONResponse(
                status_code=200, content={"status": "ignored", "reason": "not_a_branch"}
            )
        repository = payload.get("repository")
        default = repository.get("default_branch") if isinstance(repository, dict) else None
        jobs, refused, declined = await run_in_threadpool(queue_updates, push, default, delivery)
        forwarded = await run_in_threadpool(forward_event, push, delivery, default)
        if not jobs and refused and not forwarded:
            _record(
                request,
                "refused",
                f"push to {push.repository}@{push.branch}: the deploy queue is full",
            )
            return error_response(refused[0])
        if not jobs and not forwarded and declined:
            domains = ", ".join(item["domain"] for item in declined)
            _record(
                request,
                "ignored",
                f"push to {push.repository}@{push.branch}: declined by {domains} "
                f"({declined[0]['reason']})",
            )
            return JSONResponse(
                status_code=200,
                content={"status": "ignored", "reason": declined[0]["reason"], "ignored": declined},
            )
        if not jobs and not forwarded:
            _record(request, "ignored", f"push to {push.repository}@{push.branch}: no application")
            return JSONResponse(
                status_code=200, content={"status": "ignored", "reason": "no_application"}
            )
        domains = ", ".join(
            [job["domain"] for job in jobs] + [f"{f['node']} (forwarded)" for f in forwarded]
        )
        _record(request, "accepted", f"push to {push.repository}@{push.branch}: {domains}")
        return JSONResponse(
            status_code=202,
            content={
                "status": "queued",
                "jobs": jobs,
                **({"refused": len(refused)} if refused else {}),
                **({"ignored": declined} if declined else {}),
                **({"forwarded": forwarded} if forwarded else {}),
            },
        )

    if event == "pull_request":
        pull = parse_pull_request("github", payload)
        if pull is None:
            _record(request, "ignored", f"pull_request {payload.get('action')}")
            return JSONResponse(status_code=200, content={"status": "ignored", "reason": "action"})
        # Imported here: the previews manager imports the deployers, which
        # this router has no other reason to load at start-up.
        from noust.managers.previews import handle_pull_request

        job_ids = await run_in_threadpool(handle_pull_request, pull)
        forwarded = await run_in_threadpool(forward_event, pull, delivery, None)
        _record(
            request,
            "accepted",
            f"pull_request {pull.action.value} {pull.repository}#{pull.number}: "
            f"{len(job_ids)} job(s), {len(forwarded)} forwarded",
        )
        return JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                "jobs": [{"job_id": job_id} for job_id in job_ids],
                **({"forwarded": forwarded} if forwarded else {}),
            },
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
