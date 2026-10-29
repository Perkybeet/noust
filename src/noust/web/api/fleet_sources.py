# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a central does so that its servers deploy from GitHub: ``/api/fleet/sources``.

A thin layer over :mod:`noust.fleet.sources` (rule 3), for the page that shows it:

- ``GET /api/fleet/sources``: for each server, whether it holds access to clone with (the leases,
  until when, what GitHub's App does not reach and why), and whether GitHub can deliver to this
  central at all.
- ``GET /api/fleet/sources/deliveries``: the pushes, tags and pull requests the central handed (or
  owes) to its servers, each with where it stands, why not when it did not arrive, and the
  deployments and previews the server started for it, which the central follows.
- ``POST /api/fleet/sources/leases``: lease again now, all servers or some.
- ``POST /api/fleet/sources/deliveries/{id}/retry``: hand over again an event that was given up on
  (the server was away longer than an hour).

Every sentence in an answer has a code the console translates (``coded``) and, beside it, the words
of whoever said it (GitHub's, a server's) verbatim in ``output``. A token is in none of it.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from noust.core import audit
from noust.core.exceptions import ValidationError
from noust.core.github_host import configured_host
from noust.core.server_text import say
from noust.core.store import get_store
from noust.fleet.sources.follows import Follow, Follows
from noust.fleet.sources.forwards import STATES, Forward
from noust.fleet.sources.grants import Grant, LeaseGrants
from noust.fleet.sources.keeper import NodeLeases, Problem
from noust.fleet.sources.reach import webhook_warnings
from noust.fleet.sources.worker import get_worker
from noust.integrations.github.service import github_hooks_url
from noust.web.api.databases.common import session_actor
from noust.web.api.deps import NoustErrorRoute, require_auth
from noust.web.api.integrations import SentenceOut

router = APIRouter(route_class=NoustErrorRoute)

Session = Annotated[dict[str, Any], Depends(require_auth)]


class ProblemOut(BaseModel):
    """
    Something that could not be done, and why.

    Attributes:
        text: The sentence in English.
        hint: How to fix it.
        coded: The same sentence as ``{code, params}``, for the console to translate.
        output: The words of GitHub or of the server, verbatim; never a token.
    """

    text: str
    hint: str | None = None
    coded: dict[str, Any]
    output: str | None = None


class LeaseOut(BaseModel):
    """
    One lease a server holds, or was meant to.

    Attributes:
        key: Its name on the server.
        host: The GitHub host.
        installation_id: The installation its token comes from.
        repositories: What it lets the server read.
        state: ``leased``, ``failed``, ``issued`` (made just now) or ``current``.
        expires_at: When the server's token ends.
        expires_in: Seconds left; 0 once it ended.
        error: Why the last try failed.
    """

    key: str
    host: str | None = None
    installation_id: int
    repositories: list[str]
    state: str
    expires_at: str | None = None
    expires_in: int | None = None
    error: ProblemOut | None = None


class NodeSourcesOut(BaseModel):
    """
    Where one server stands on the access to clone with.

    Attributes:
        node: The server.
        state: ``leased``, ``none`` (it deploys nothing from the App's repositories),
            ``independent``, ``unsupported`` (an older Noust), ``ceiling`` (the server lets its
            central only read), ``unreachable``, ``failed``, ``unknown`` (not looked at since the
            console started and nothing recorded) or ``no_app``.
        leases: Its leases.
        uncovered: The repositories of its applications the App does not reach.
        problems: Why, one for each.
        error: Why the whole server could not be leased.
        checked_at: When it was last looked at; null when the answer is what was recorded.
    """

    node: str
    state: str
    leases: list[LeaseOut] = Field(default_factory=list)
    uncovered: list[str] = Field(default_factory=list)
    problems: list[ProblemOut] = Field(default_factory=list)
    error: ProblemOut | None = None
    checked_at: str | None = None


class DeliveryCountsOut(BaseModel):
    """
    How many events are in each state.

    Attributes:
        pending: Waiting for their server.
        delivered: Taken.
        failed: Refused for good.
        expired: Given up on after the server stayed away.
    """

    pending: int = 0
    delivered: int = 0
    failed: int = 0
    expired: int = 0


class GitHubSourcesOut(BaseModel):
    """
    The GitHub this central serves its servers with.

    Attributes:
        configured: Whether this central has an App.
        host: The GitHub host.
        hooks_url: Where GitHub delivers, when this central has a public address for it.
        warnings: What keeps it from working: GitHub has no address to deliver to.
    """

    configured: bool
    host: str
    hooks_url: str | None = None
    warnings: list[SentenceOut] = Field(default_factory=list)


class SourcesOut(BaseModel):
    """
    What a central does so that its servers deploy from GitHub.

    Attributes:
        github: The App and whether GitHub can reach this central.
        nodes: Each server's access to clone with.
        deliveries: How many events are in each state.
        following: How many deployments and previews are followed now.
    """

    github: GitHubSourcesOut
    nodes: list[NodeSourcesOut]
    deliveries: DeliveryCountsOut
    following: int


class FollowOut(BaseModel):
    """
    A deployment or a preview the central follows.

    Attributes:
        id: Its number.
        node: The server it is on.
        domain: The application.
        kind: ``deployment`` or ``preview``.
        repository: ``owner/repo``.
        ref: The commit, branch or tag.
        job_id: The server's job (a deployment).
        number: The pull request (a preview).
        action: What happened to the pull request.
        last_state: What was last said on GitHub.
        state: ``active``, ``done`` or ``lost``.
        detail: How it ended.
        created_at: When it began.
        finished_at: When it ended.
    """

    id: int
    node: str
    domain: str
    kind: str
    repository: str
    ref: str | None = None
    job_id: str | None = None
    number: int | None = None
    action: str | None = None
    last_state: str | None = None
    state: str
    detail: str | None = None
    created_at: str
    finished_at: str | None = None


class DeliveryOut(BaseModel):
    """
    An event handed, or owed, to a server.

    Attributes:
        id: Its id.
        delivery: GitHub's id for the delivery.
        node: The server.
        kind: ``push``, ``tag`` or ``pull_request``.
        repository: ``owner/repo``.
        ref: The branch or tag.
        number: The pull request.
        commit: The commit announced.
        state: ``pending``, ``delivered``, ``failed`` or ``expired``.
        attempts: How many times it was tried.
        next_attempt_at: When it is tried next.
        error: Why the last try did not work.
        jobs: What the server queued for it (domain and job).
        follows: What the central follows because of it.
        created_at: When it was written down.
        delivered_at: When the server took it.
    """

    id: str
    delivery: str
    node: str
    kind: str
    repository: str
    ref: str | None = None
    number: int | None = None
    commit: str | None = None
    state: str
    attempts: int
    next_attempt_at: str | None = None
    error: ProblemOut | None = None
    jobs: list[dict[str, Any]] = Field(default_factory=list)
    follows: list[FollowOut] = Field(default_factory=list)
    created_at: str
    delivered_at: str | None = None


class DeliveriesOut(BaseModel):
    """
    The events handed to the servers.

    Attributes:
        items: Them, newest first.
        total: How many are listed.
        counts: How many are in each state, all of them.
    """

    items: list[DeliveryOut]
    total: int
    counts: DeliveryCountsOut


class LeaseAgainIn(BaseModel):
    """
    Lease again now.

    Attributes:
        nodes: Only these servers; all of them when omitted.
        force: Replace the leases that are not due.
        repositories: ``owner/repo`` these servers should be able to read although no application
            of theirs deploys from them yet: the console asks before it creates the first one,
            because an application is made by cloning its repository. Needs ``nodes``; kept in
            the lease for half an hour.
    """

    nodes: list[str] | None = Field(default=None, max_length=500)
    force: bool = False
    repositories: list[str] | None = Field(default=None, max_length=100)


class LeasedAgainOut(BaseModel):
    """
    What leasing again came to.

    Attributes:
        nodes: Each server after the look.
    """

    nodes: list[NodeSourcesOut]


def _stamp(moment: float | None) -> str | None:
    """
    Args:
        moment: Epoch seconds, or None.

    Returns:
        ISO 8601 UTC.
    """
    return None if moment is None else datetime.fromtimestamp(moment, timezone.utc).isoformat()


def _problem(problem: Problem | dict[str, Any] | None) -> ProblemOut | None:
    """
    Args:
        problem: A problem the keeper made, or one a record kept as a dictionary.

    Returns:
        Its API shape.
    """
    if problem is None:
        return None
    if isinstance(problem, Problem):
        return ProblemOut(
            text=problem.sentence.text,
            hint=problem.sentence.hint,
            coded=problem.sentence.to_dict(),
            output=problem.output,
        )
    return ProblemOut(
        text=str(problem.get("text") or ""),
        hint=problem.get("hint"),
        coded=dict(problem.get("coded") or {}),
        output=problem.get("output"),
    )


def _grant(grant: Grant, now: float) -> LeaseOut:
    """
    Args:
        grant: A lease the central made.
        now: Epoch seconds.

    Returns:
        Its API shape.
    """
    return LeaseOut(
        key=grant.key,
        host=grant.host,
        installation_id=grant.installation_id,
        repositories=list(grant.repositories),
        state=grant.state,
        expires_at=_stamp(grant.expires_at),
        expires_in=None if grant.expires_at is None else max(int(grant.expires_at - now), 0),
        error=_problem(grant.error),
    )


def _node(report: NodeLeases | None, grants: list[Grant], name: str, now: float) -> NodeSourcesOut:
    """
    Args:
        report: Where the keeper found the server at its last look, if it has looked.
        grants: What was recorded as leased to it.
        name: The server.
        now: Epoch seconds.

    Returns:
        The server's standing: the keeper's when it has looked, else what was recorded.
    """
    if report is None:
        return NodeSourcesOut(
            node=name,
            state="leased" if any(g.state == "leased" for g in grants) else "unknown",
            leases=[_grant(g, now) for g in grants],
        )
    recorded = {g.key: g for g in grants}
    leases = []
    for lease in report.leases:
        known = recorded.get(lease.key)
        leases.append(
            LeaseOut(
                key=lease.key,
                host=known.host if known else None,
                installation_id=lease.installation_id,
                repositories=list(lease.repositories),
                state=lease.state,
                expires_at=_stamp(lease.expires_at),
                expires_in=None
                if lease.expires_at is None
                else max(int(lease.expires_at - now), 0),
                error=_problem(lease.error),
            )
        )
    return NodeSourcesOut(
        node=name,
        state=report.state,
        leases=leases,
        uncovered=list(report.uncovered),
        problems=[p for p in (_problem(x) for x in report.problems) if p is not None],
        error=_problem(report.error),
        checked_at=_stamp(report.checked_at),
    )


def _follow(follow: Follow) -> FollowOut:
    """
    Args:
        follow: A deployment or preview followed.

    Returns:
        Its API shape.
    """
    return FollowOut(
        id=follow.id,
        node=follow.node,
        domain=follow.domain,
        kind=follow.kind,
        repository=follow.repository,
        ref=follow.ref,
        job_id=follow.job_id,
        number=follow.number,
        action=follow.action,
        last_state=follow.last_state,
        state=follow.state,
        detail=follow.detail,
        created_at=_stamp(follow.created_at) or "",
        finished_at=_stamp(follow.finished_at),
    )


def _delivery(forward: Forward, follows: Follows) -> DeliveryOut:
    """
    Args:
        forward: An event owed to a server.
        follows: What is followed.

    Returns:
        Its API shape.
    """
    answer = forward.answer or {}
    jobs = answer.get("jobs") if isinstance(answer, dict) else None
    return DeliveryOut(
        id=forward.id,
        delivery=forward.delivery,
        node=forward.node,
        kind=forward.kind,
        repository=forward.repository,
        ref=forward.ref,
        number=forward.number,
        commit=forward.commit,
        state=forward.state,
        attempts=forward.attempts,
        next_attempt_at=_stamp(forward.next_attempt_at),
        error=_problem(dict(forward.error)) if forward.error else None,
        jobs=[dict(job) for job in jobs or [] if isinstance(job, dict)],
        follows=[_follow(f) for f in follows.of(forward.id)],
        created_at=_stamp(forward.created_at) or "",
        delivered_at=_stamp(forward.delivered_at),
    )


@router.get("/sources", response_model=SourcesOut)
def sources_status(session: Session) -> SourcesOut:
    """
    Show what this central does so that its servers deploy from GitHub.

    Args:
        session: The authenticated session.

    Returns:
        The App and whether GitHub can reach this central, each server's access to clone with, how
        many events are in each state and how many deployments are followed.
    """
    from noust.fleet.nodes import NodeManager

    now = time.time()
    store = get_store()
    worker = get_worker()
    reports = worker.keeper.reports()
    grants = LeaseGrants(store)
    names = [record.name for record in NodeManager().list()]
    counts = worker.forwarder.forwards.counts()
    return SourcesOut(
        github=GitHubSourcesOut(
            configured=store.get_github_app() is not None,
            host=configured_host(),
            hooks_url=github_hooks_url(),
            warnings=[
                SentenceOut(text=w.text, hint=w.hint, coded=w.to_dict()) for w in webhook_warnings()
            ],
        ),
        nodes=[_node(reports.get(name), grants.list(name), name, now) for name in names],
        deliveries=DeliveryCountsOut(**counts),
        following=len(worker.follower.follows.active()),
    )


@router.get("/sources/deliveries", response_model=DeliveriesOut)
def sources_deliveries(
    session: Session,
    node: Annotated[str | None, Query(max_length=100, description="Only this server's")] = None,
    state: Annotated[str | None, Query(max_length=20, description="Only in this state")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> DeliveriesOut:
    """
    List the events handed, or owed, to the servers.

    Args:
        session: The authenticated session.
        node: Only this server's.
        state: Only in this state: ``pending``, ``delivered``, ``failed`` or ``expired``.
        limit: Most events.

    Returns:
        The events, newest first, each with what the server queued and what is followed.

    Raises:
        ValidationError: 400 for a state nobody defined.
    """
    if state is not None and state not in STATES:
        raise ValidationError(
            f"Not a state of a delivery: {state!r}",
            field="state",
            details=f"Use one of: {', '.join(STATES)}.",
        )
    worker = get_worker()
    forwards = worker.forwarder.forwards
    listed = forwards.list(node=node, state=state, limit=limit)
    return DeliveriesOut(
        items=[_delivery(item, worker.follower.follows) for item in listed],
        total=len(listed),
        counts=DeliveryCountsOut(**forwards.counts()),
    )


@router.post("/sources/leases", response_model=LeasedAgainOut)
async def lease_again(body: LeaseAgainIn, session: Session) -> LeasedAgainOut:
    """
    Look at the servers and lease what each needs, now.

    Args:
        body: The servers to look at, and whether to replace what is not due.
        session: The authenticated session.

    Returns:
        Each server after the look.

    Raises:
        HTTPException: 409 ``no_github_app`` when this central has no App to lease with.
        ValidationError: 400 for repositories without the servers they are for, or a repository
            that is not one.
    """
    if body.repositories and not body.nodes:
        raise ValidationError(
            "Name the servers the repositories are for",
            field="nodes",
            details="A repository is asked for on the servers that will clone it.",
        )
    store = get_store()
    if store.get_github_app() is None:
        text = say("sources.no_app")
        raise HTTPException(
            status_code=409,
            detail={
                "error": "no_github_app",
                "detail": text.text,
                "hint": text.hint,
                "fields": None,
                "coded": text.to_dict(),
            },
        )
    worker = get_worker()
    now = time.time()
    for node in body.nodes or []:
        if body.repositories:
            worker.keeper.reserve(node, body.repositories)
    done = await run_in_threadpool(worker.keeper.sweep, body.nodes, force=body.force)
    audit.record(
        "sources.lease.refresh",
        actor=session_actor(session),
        target="fleet",
        details={"nodes": body.nodes or "*", "force": body.force},
    )
    grants = LeaseGrants(store)
    return LeasedAgainOut(
        nodes=[_node(report, grants.list(report.node), report.node, now) for report in done]
    )


@router.post("/sources/deliveries/{forward_id}/retry", response_model=DeliveryOut)
async def retry_delivery(forward_id: str, session: Session) -> DeliveryOut:
    """
    Hand an event over again that the central had given up on.

    Args:
        forward_id: The event.
        session: The authenticated session.

    Returns:
        The event, pending and due now; the worker hands it over within seconds.

    Raises:
        HTTPException: 404 when there is no such event; 409 when it is not one that was given up
            on (one the server took is not sent twice).
    """
    worker = get_worker()
    forwards = worker.forwarder.forwards
    found = forwards.get(forward_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Not found")
    again = forwards.requeue(forward_id)
    if again is None:
        text = say("sources.delivery_not_retryable", state=found.state)
        raise HTTPException(
            status_code=409,
            detail={
                "error": "not_retryable",
                "detail": text.text,
                "hint": text.hint,
                "fields": None,
                "coded": text.to_dict(),
            },
        )
    audit.record(
        "sources.forward.retry",
        actor=session_actor(session),
        target=f"node:{again.node}",
        details={"forward": forward_id, "kind": again.kind, "repository": again.repository},
    )
    worker.wake()
    return _delivery(again, worker.follower.follows)
