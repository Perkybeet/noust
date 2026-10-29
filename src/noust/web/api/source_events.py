# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An event of a code host that the central forwarded to this server: ``POST /api/sources/events``.

The central holds the GitHub App, so GitHub delivers a push, a tag or a pull request to the
central's ``/hooks/github``, and the central finds the servers that deploy that repository and
hands each one the event, already read into :mod:`noust.core.forge_events`' records. This server
does with it what it does with its own webhook, by the same functions:

- a **push** or a **tag** queues the update of the applications that follow that branch or that
  tag, behind a build that is running, merged into an update that waits, or refused when the queue
  has no room (:func:`noust.web.api.github_hooks.queue_updates`), and says which applications
  queued a job, which declined and why (a skip marker, watch paths, a pause, a freeze);
- a **pull request** goes to the previews (:func:`noust.managers.previews.handle_pull_request`),
  and the answer says which applications preview it, so that the central can follow the previews
  and write the comment of the pull request, which it alone can (the App is its).

It is the central's alone (a fleet token at the ``deploy`` ceiling) and judges the event by an
allowlist before anything else (:mod:`noust.validators.forge_event`). A full queue is the error
the forge would have been answered with, so that the central tries again.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from noust.core import audit
from noust.core.exceptions import ValidationError
from noust.core.forge_events import PullRequestEvent, PushEvent, TagEvent
from noust.core.github_host import hosts_of
from noust.integrations.github.leases import checked_host
from noust.validators.forge_event import parse_forwarded_event
from noust.web.api import github_hooks
from noust.web.api.databases.common import session_actor
from noust.web.api.deps import NoustErrorRoute, error_response, require_auth
from noust.web.api.managed_settings import central_of
from noust.web.api.source_leases import central_only

router = APIRouter(route_class=NoustErrorRoute)

Session = Annotated[dict[str, Any], Depends(require_auth)]


class ForwardedEventIn(BaseModel):
    """
    An event a central forwards.

    Attributes:
        kind: ``push``, ``tag`` or ``pull_request``.
        event: The event as :func:`noust.core.forge_events.event_to_dict` writes it.
        delivery: The code host's id for the delivery, kept in the jobs' metadata and the
            applications' delivery logs.
        host: The GitHub host the repository is on, which decides which sources are it.
        default_branch: The repository's default branch (a push only), which an application with
            no branch of its own follows.
    """

    kind: str = Field(max_length=20)
    event: dict[str, Any]
    delivery: str | None = Field(default=None, max_length=200)
    host: str = Field(default="github.com", max_length=300)
    default_branch: str | None = Field(default=None, max_length=255)


class ForwardedJob(BaseModel):
    """
    An update the event queued.

    Attributes:
        domain: The application.
        job_id: The job, which the central follows.
        merged: The update that waited already covers this push: the last push wins.
    """

    domain: str
    job_id: str
    merged: bool = False


class ForwardedIgnored(BaseModel):
    """
    An application the event concerned and that did not update.

    Attributes:
        domain: The application.
        reason: Why, in a word (``skip``, ``paths``, ``paused``, ``frozen``) or the sentence
            of a tag it does not deploy.
        detail: The sentence, when there is one.
    """

    domain: str
    reason: str
    detail: str | None = None


class ForwardedPreview(BaseModel):
    """
    An application that previews the pull request.

    Attributes:
        parent: The application previewed.
        number: The pull request.
        action: ``opened``, ``updated`` or ``closed``.
    """

    parent: str
    number: int
    action: str


class ForwardedEventOut(BaseModel):
    """
    What this server did with the event.

    Attributes:
        status: ``queued`` when something was queued, ``ignored`` otherwise.
        kind: The kind of event.
        jobs: The updates queued by a push or a tag.
        ignored: The applications that did not update, with why.
        refused: How many updates the full queue refused (when some were queued anyway).
        previews: The applications that preview a pull request.
        job_ids: The jobs the previews queued.
    """

    status: str
    kind: str
    jobs: list[ForwardedJob] = Field(default_factory=list)
    ignored: list[ForwardedIgnored] = Field(default_factory=list)
    refused: int = 0
    previews: list[ForwardedPreview] = Field(default_factory=list)
    job_ids: list[str] = Field(default_factory=list)


def _checked(
    body: ForwardedEventIn,
) -> tuple[PushEvent | TagEvent | PullRequestEvent, tuple[str, ...]]:
    """
    Args:
        body: What the central sent.

    Returns:
        The event, and the hosts whose repositories it is about.

    Raises:
        ValidationError: When the kind, the event or the host is not what it should be.
    """
    event = parse_forwarded_event(body.kind, body.event)
    return event, hosts_of(checked_host(body.host))


@router.post("/events", response_model=ForwardedEventOut)
def forward_event(body: ForwardedEventIn, session: Session) -> ForwardedEventOut | JSONResponse:
    """
    Queue what an event of the central's code host asks for on this server.

    Args:
        body: The event.
        session: The authenticated session; only a central's.

    Returns:
        What was queued, what declined and why, and what previews the pull request.

    Raises:
        HTTPException: 403 ``central_only`` for anyone but a central.
        ValidationError: 400 naming the field of an event that is not exactly one.
    """
    central = central_only(session)
    event, hosts = _checked(body)
    through = f"the GitHub App of {central}"
    answer = ForwardedEventOut(status="ignored", kind=body.kind)
    if isinstance(event, PushEvent):
        jobs, refused, declined = github_hooks.queue_updates(
            event, body.default_branch, body.delivery, hosts=hosts, through=through
        )
        if not jobs and refused:
            return error_response(refused[0])
        answer.jobs = [ForwardedJob(**job) for job in jobs]
        answer.ignored = [ForwardedIgnored(**item) for item in declined]
        answer.refused = len(refused)
    elif isinstance(event, TagEvent):
        queued, ignored = github_hooks.queue_tag_updates(event, body.delivery, hosts=hosts)
        answer.jobs = [ForwardedJob(**job) for job in queued]
        answer.ignored = [ForwardedIgnored(**item) for item in ignored]
    elif isinstance(event, PullRequestEvent):
        # Imported here: the previews import the deployers, which this router has no other
        # reason to load at start-up.
        from noust.managers import previews

        previewing = [app.domain for app, _ in previews.previewing_apps(event)]
        answer.job_ids = previews.handle_pull_request(event)
        answer.previews = [
            ForwardedPreview(parent=domain, number=event.number, action=event.action.value)
            for domain in previewing
        ]
    else:  # pragma: no cover - parse_forwarded_event returns one of the three
        raise ValidationError(f"Not a kind of event: {body.kind!r}", field="kind")
    if answer.jobs or answer.job_ids:
        answer.status = "queued"
        audit.record(
            "sources.event.forward",
            actor=session_actor(session),
            target=f"central:{central_of(session)}",
            details={
                "kind": body.kind,
                "repository": event.repository,
                "jobs": len(answer.jobs) + len(answer.job_ids),
                "delivery": body.delivery or "",
            },
        )
    return answer
