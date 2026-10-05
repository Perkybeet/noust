# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/fleet``: the fleet at once, and actions on several servers at a time.

A thin layer (rule 3): the views are :func:`noust.fleet.aggregate.gather`, the
actions are :mod:`noust.web.fleet_jobs`, the labels are
:class:`noust.fleet.labels.NodeLabels`. Every call they make to a node carries
the operator's identity, scope, role and sudo mode exactly as the proxy
forwards them (:func:`noust.web.api.node_proxy.forwarded_identity`), so each
node applies its own permissions, its ceiling and its own sudo mode check.

Views answer ``200`` with every server's outcome, whether or not it answered
(``partial`` says so); only a central that cannot reach any node at all (it is
locked: ``423``) answers an error.

A central whose role is ``server`` is one of its own servers: its rows are read
through its own API, in this process, with the operator's own credential, so
its own permissions apply to them exactly as to a request from the console.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Mapping
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from noust.core.exceptions import ValidationError
from noust.fleet.aggregate import Asker, Failure, decode_answer, gather
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, ensure_elevated
from noust.web.auth import actor_label, require_auth

if TYPE_CHECKING:
    from starlette.types import ASGIApp

router = APIRouter(route_class=NoustErrorRoute)

Session = Annotated[dict, Depends(require_auth)]

#: Request headers the central's own rows are read with: the credential, and
#: what decides who is asking from where (the session is bound to its address).
REPLAYED_HEADERS = (
    "authorization",
    "cookie",
    "user-agent",
    "accept-language",
    "x-forwarded-for",
    "x-forwarded-proto",
    "x-real-ip",
    "forwarded",
)

#: Seconds the central's own API may take for one of its rows.
LOCAL_TIMEOUT = 8.0


class CentralSource:
    """
    This central as one of its own servers, read through its own API in-process.

    The same endpoints a node answers, with the operator's own credential, so
    the central's permission check decides what its rows show, as it would for
    the console. Called from the aggregator's threads; the request runs on the
    application's event loop.

    Args:
        app: The application.
        loop: Its event loop.
        request: The fleet request whose credential and address are replayed.
        asker: Who asks, for the cache.
        name: What the central is called in the rows.
    """

    local = True

    def __init__(
        self,
        app: ASGIApp,
        loop: asyncio.AbstractEventLoop,
        request: Request,
        asker: Asker,
        name: str,
    ) -> None:
        self._app = app
        self._loop = loop
        self._base_url = str(request.base_url).rstrip("/")
        self._headers = {
            key: value for key in REPLAYED_HEADERS if (value := request.headers.get(key))
        }
        client = request.client
        self._client = (client.host, client.port) if client else ("127.0.0.1", 0)
        self._asker = asker
        self.name = name

    @property
    def cache_key(self) -> str:
        """The central and the operator: its own rows are never shared between operators."""
        return f"local:{self.name}:{self._asker.actor}:{self._asker.audience}"

    async def _get(self, path: str, params: Mapping[str, Any] | None) -> Any:
        """
        Send one GET through the application, as the operator.

        Args:
            path: The path.
            params: Query parameters.

        Returns:
            The response.
        """
        from noust.fleet.client import load_httpx

        httpx = load_httpx()
        transport = httpx.ASGITransport(app=self._app, client=self._client)
        async with httpx.AsyncClient(
            transport=transport, base_url=self._base_url, follow_redirects=False
        ) as client:
            return await client.get(path, params=dict(params or {}), headers=self._headers)

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        """
        GET one path of this central's API.

        Args:
            path: Such as ``/api/apps``.
            params: Query parameters.

        Returns:
            The decoded JSON.

        Raises:
            Failure: The central's own API refused or failed it.
        """
        future = asyncio.run_coroutine_threadsafe(self._get(path, params), self._loop)
        try:
            response = future.result(timeout=LOCAL_TIMEOUT)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise Failure(
                "timeout",
                f"This central did not answer GET {path} within {LOCAL_TIMEOUT:.0f}s",
                code="timeout",
            ) from exc
        return decode_answer(self.name, "GET", path, response)


def asker_for(session: Mapping[str, Any]) -> Asker:
    """
    Who a request from this console asks the nodes as.

    Args:
        session: The authenticated payload.

    Returns:
        The same identity, scope, role and sudo mode the proxy forwards.
    """
    from noust.web.api.node_proxy import forwarded_identity

    identity = forwarded_identity(dict(session))
    return Asker(
        actor=identity["actor"],
        scope=identity["actor_scope"],
        role=identity["actor_role"],
        elevated=bool(identity["elevated"]),
    )


class NodeOutcomeOut(BaseModel):
    """
    How one server answered a fleet view.

    Attributes:
        name: The server's name (this central's own name for its own row).
        local: The row is this central.
        status: ``ok``, ``stale``, ``unreachable``, ``unsupported``,
            ``forbidden`` or ``error``.
        code: Why, for a machine (``node_unreachable``, ``node_refused``,
            ``timeout``, ``not_offered``, ``permission_denied``...).
        message: Why, in a sentence.
        hint: What to do about it.
        error_verbatim: The node's or ssh's own words.
        age_seconds: How old the rows shown for it are.
        fetched_at: When they were read.
        elapsed_ms: How long its first answer took.
        version: The Noust it runs.
        missing: Its endpoints the view needed and it does not offer.
        warnings: Parts of the view it could not give.
    """

    name: str
    local: bool = False
    status: str
    code: str | None = None
    message: str | None = None
    hint: str | None = None
    error_verbatim: str | None = None
    age_seconds: float | None = None
    fetched_at: str | None = None
    elapsed_ms: float | None = None
    version: str | None = None
    missing: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class FleetViewOut(BaseModel):
    """
    One fleet view.

    Attributes:
        resource: Which view.
        generated_at: When it was put together.
        partial: Some server's answer is not fresh and complete.
        nodes: Every server's outcome.
        items: The rows, each with ``node``, ``local``, ``page`` (its page on
            that server's console) and ``href`` (the same page here).
    """

    resource: str
    generated_at: str
    partial: bool
    nodes: list[NodeOutcomeOut]
    items: list[dict[str, Any]]


async def _view(
    resource: str,
    request: Request,
    session: Mapping[str, Any],
    node: list[str] | None,
    refresh: bool,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Answer one fleet view.

    Args:
        resource: The view.
        request: The request, whose credential reads the central's own rows.
        session: The authenticated payload.
        node: Only these servers (``@central`` names this one).
        refresh: Ask every server again, whatever the cache holds.
        params: The view's parameters.

    Returns:
        The view.
    """
    from noust.fleet.models import central_name

    asker = asker_for(session)
    local = CentralSource(
        request.app,
        asyncio.get_running_loop(),
        request,
        asker,
        await run_in_threadpool(central_name),
    )
    result = await run_in_threadpool(
        lambda: gather(resource, params, asker=asker, nodes=node, local=local, refresh=refresh)
    )
    return result.to_dict()


NodeFilter = Annotated[
    list[str] | None,
    Query(description="Only these servers, repeated; '@central' is this central itself"),
]
Refresh = Annotated[bool, Query(description="Ask every server again, ignoring the cache")]


@router.get("/summary", response_model=FleetViewOut)
async def fleet_summary(
    request: Request, session: Session, node: NodeFilter = None, refresh: Refresh = False
) -> dict[str, Any]:
    """
    Every server at a glance: reachability, version, its overview and its machine.

    Each row carries the server's own ``overview`` (``GET /api/overview``),
    ``server`` summary (``GET /api/server/summary``) and ``machine`` snapshot,
    verbatim, plus the counts ``noust fleet status`` prints. A node too old to
    offer the overview is ``unsupported`` and still shows what it has.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.

    Returns:
        One row per server.
    """
    return await _view("summary", request, session, node, refresh)


@router.get("/servers", response_model=FleetViewOut)
async def fleet_servers(
    request: Request, session: Session, node: NodeFilter = None, refresh: Refresh = False
) -> dict[str, Any]:
    """
    Every server: whether it answers, its version, access ceiling and labels.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.

    Returns:
        One row per server.
    """
    return await _view("servers", request, session, node, refresh)


@router.get("/apps", response_model=FleetViewOut)
async def fleet_apps(
    request: Request, session: Session, node: NodeFilter = None, refresh: Refresh = False
) -> dict[str, Any]:
    """
    Every application of every server, as each server lists it.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.

    Returns:
        One row per application, with its server and its page there.
    """
    return await _view("apps", request, session, node, refresh)


@router.get("/certificates", response_model=FleetViewOut)
async def fleet_certificates(
    request: Request, session: Session, node: NodeFilter = None, refresh: Refresh = False
) -> dict[str, Any]:
    """
    Every certificate of every server, the soonest to expire first.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.

    Returns:
        One row per certificate.
    """
    return await _view("certificates", request, session, node, refresh)


@router.get("/backups", response_model=FleetViewOut)
async def fleet_backups(
    request: Request, session: Session, node: NodeFilter = None, refresh: Refresh = False
) -> dict[str, Any]:
    """
    Every application's backups, the gaps first: none, old, unverified, unscheduled.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.

    Returns:
        One row per application: its newest backup, whether it verified, its schedule.
    """
    return await _view("backups", request, session, node, refresh)


@router.get("/updates", response_model=FleetViewOut)
async def fleet_updates(
    request: Request, session: Session, node: NodeFilter = None, refresh: Refresh = False
) -> dict[str, Any]:
    """
    Every server's Noust and operating system updates.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.

    Returns:
        One row per server: installed and available Noust, how it updates, the
        last self-update, and the pending operating system updates.
    """
    return await _view("updates", request, session, node, refresh)


@router.get("/activity", response_model=FleetViewOut)
async def fleet_activity(
    request: Request,
    session: Session,
    node: NodeFilter = None,
    refresh: Refresh = False,
    limit: Annotated[int, Query(ge=1, le=200, description="Events per server")] = 50,
) -> dict[str, Any]:
    """
    What happened lately on every server, newest first.

    Each server's audit log decides what this operator may read of it: a
    server that does not let the operator's role read its audit log is
    ``forbidden``, not an error.

    Args:
        request: The request.
        session: The authenticated session.
        node: Only these servers.
        refresh: Ask again.
        limit: Events per server.

    Returns:
        One row per event.
    """
    return await _view("activity", request, session, node, refresh, {"limit": limit})


# ------------------------------------------------------------------ labels


class LabelsIn(BaseModel):
    """
    A server's labels.

    Attributes:
        labels: Every label it carries, replacing what it had.
    """

    labels: dict[str, str] = Field(default_factory=dict)


class LabelsOut(BaseModel):
    """A server's labels."""

    node: str
    labels: dict[str, str]


@router.put("/servers/{node}/labels", response_model=LabelsOut)
def set_labels(node: str, body: LabelsIn, session: Session) -> LabelsOut:
    """
    Set a server's labels, replacing the ones it had.

    Labels are this central's own grouping (``env=prod``) for aiming actions;
    the server never sees them.

    Args:
        node: The server.
        body: Its labels.
        session: The authenticated session.

    Returns:
        Its labels now.
    """
    from noust.core.audit import record as record_audit
    from noust.fleet.labels import NodeLabels

    labels = NodeLabels().change(node, set_labels=body.labels, replace=True)
    record_audit("fleet.node.label", target=f"node:{node}", details={"labels": labels})
    return LabelsOut(node=node, labels=labels)


# ----------------------------------------------------------------- actions


class TargetsIn(BaseModel):
    """
    The servers an action runs on: by name, by label, or both.

    Attributes:
        nodes: Servers by name.
        labels: A label selector; servers carrying every pair are added.
    """

    nodes: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)


class StrategyIn(BaseModel):
    """
    How an action goes through the servers.

    Attributes:
        serial: Servers at a time: a number or a percentage (``"25%"``); the
            action's default when absent.
        max_failures: Failed servers tolerated before the rest are skipped;
            the action's default when absent, ``-1`` never stops.
        canary: A server that goes alone first; if it fails, nothing else runs.
    """

    serial: int | str | None = None
    max_failures: int | None = None
    canary: str | None = None


class ActionIn(BaseModel):
    """
    A bulk action.

    Attributes:
        action: ``certs_renew``, ``backups_run``, ``backups_verify``,
            ``apps_update``, ``apps_restart``, ``noust_update`` or ``os_updates``.
        targets: Which servers.
        strategy: How.
        options: The action's options (``force``, ``domains``, ``verify``,
            ``scope``).
        plan: Only say what would happen; nothing runs.
    """

    action: str
    targets: TargetsIn = Field(default_factory=TargetsIn)
    strategy: StrategyIn = Field(default_factory=StrategyIn)
    options: dict[str, Any] = Field(default_factory=dict)
    plan: bool = False


class FleetNodeStateOut(BaseModel):
    """
    One server in a fleet job, or in its plan.

    Attributes:
        node: Its name.
        position: Its order.
        batch: Its batch; 0 is the canary's when there is one.
        state: ``queued``, ``running``, ``succeeded``, ``failed``, ``skipped``,
            ``unreachable``, ``refused`` or ``interrupted``.
        reason: Why it was skipped: ``policy``, ``unsupported``,
            ``not_needed``, ``aborted``, ``busy``, ``elevation_expired``,
            ``error``.
        step: What is happening on it, or what happened last.
        node_jobs: The node's own job ids.
        items: One result per application where the action has them
            (``domain``, ``state``, ``message``, ``output``...).
        error: ``code``, ``message`` and ``hint``.
        output: The node's (or ssh's) words, verbatim.
        started_at: When it started.
        ended_at: When it ended.
        requires_elevation: The node's schema says the action needs sudo mode.
        href: The node's activity page through this central.
    """

    node: str
    position: int
    batch: int
    state: str
    reason: str | None = None
    step: str | None = None
    node_jobs: list[str] = Field(default_factory=list)
    items: list[dict[str, Any]] = Field(default_factory=list)
    error: dict[str, Any] | None = None
    output: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    requires_elevation: bool = False
    href: str


class FleetPlanOut(BaseModel):
    """
    What a bulk action will do.

    Attributes:
        action: The action.
        title: What it does.
        strategy: ``serial``, ``max_failures`` (null never stops) and ``canary``.
        options: Its checked options.
        nodes: Every selected server, with what will happen to it.
        batches: The servers that run, batch by batch.
        summary: ``run`` and ``skipped``.
        requires_elevation: Sudo mode is asked once for the whole job.
        notes: Sentences for the operator.
    """

    action: str
    title: str
    strategy: dict[str, Any]
    options: dict[str, Any]
    nodes: list[FleetNodeStateOut]
    batches: list[list[str]]
    summary: dict[str, int]
    requires_elevation: bool
    notes: list[str] = Field(default_factory=list)


class ActionOut(BaseModel):
    """
    What a bulk action will do, or did start.

    Attributes:
        plan: The servers, the batches and what is skipped and why.
        job: The queued job (``JobType.FLEET``: its ``result`` is the state per
            server as it runs), when it runs; null for a plan.
    """

    plan: FleetPlanOut
    job: JobAcceptedResponse | None = None


class FleetActionOut(BaseModel):
    """
    One bulk action.

    Attributes:
        name: Its name.
        title: What it does.
        operations: The node operations it uses (``METHOD /path``).
        serial: Servers at a time by default.
        max_failures: Failures tolerated by default; null never stops.
    """

    name: str
    title: str
    operations: list[str]
    serial: int
    max_failures: int | None = None


class FleetActionsOut(BaseModel):
    """The bulk actions."""

    actions: list[FleetActionOut]


class FleetJobOut(BaseModel):
    """
    One fleet job.

    Attributes:
        job_id: The job (also a console job when started there).
        action: The action.
        title: What it does.
        request: What was asked: targets, strategy, options.
        status: ``running``, ``succeeded``, ``failed``, ``aborted`` or
            ``interrupted`` (the central restarted while it ran).
        created_at: When.
        created_by: Who.
        finished_at: When it ended.
        retry_of: The job it retried.
        summary: Servers per state.
        nodes: Every server; absent in a listing.
    """

    job_id: str
    action: str
    title: str
    request: dict[str, Any]
    status: str
    created_at: str
    created_by: str | None = None
    finished_at: str | None = None
    retry_of: str | None = None
    summary: dict[str, int]
    nodes: list[FleetNodeStateOut] | None = None


class FleetJobsOut(BaseModel):
    """The fleet jobs, newest first."""

    jobs: list[FleetJobOut]


@router.get("/actions", response_model=FleetActionsOut)
def list_actions(session: Session) -> dict[str, Any]:
    """
    Describe the bulk actions: what each uses on a node, and its defaults.

    Args:
        session: The authenticated session.

    Returns:
        ``actions``: each with ``name``, ``title``, ``operations``, ``serial``
        and ``max_failures``.
    """
    from noust.web.fleet_jobs import ACTIONS

    return {"actions": [spec.describe() for spec in ACTIONS.values()]}


def _request_from(body: ActionIn, retry_of: str | None = None) -> Any:
    """
    Build the engine's request from the API's.

    Args:
        body: The request body.
        retry_of: The job being retried, if any.

    Returns:
        A :class:`~noust.web.fleet_jobs.FleetRequest`.
    """
    from noust.web.fleet_jobs import FleetRequest

    return FleetRequest(
        action=body.action,
        nodes=list(body.targets.nodes),
        labels=dict(body.targets.labels),
        serial=body.strategy.serial,
        max_failures=body.strategy.max_failures,
        canary=body.strategy.canary,
        options=dict(body.options),
        retry_of=retry_of,
    )


async def _plan_and_start(
    request: Request, session: Mapping[str, Any], fleet_request: Any, only_plan: bool
) -> dict[str, Any]:
    """
    Plan a bulk action and, unless only planning, queue it as a job.

    Sudo mode is asked when any selected server's schema marks the action as
    needing it (the node's call, as for a proxied request), and one
    confirmation covers the whole job.

    Args:
        request: The request.
        session: The authenticated payload.
        fleet_request: The engine's request.
        only_plan: Do not run it.

    Returns:
        ``plan``, and ``job`` when it was queued.
    """
    from noust.fleet.nodes import NodeManager
    from noust.web.api.node_proxy import central_elevated
    from noust.web.fleet_jobs import plan, start_job

    asker = asker_for(session)
    the_plan = await run_in_threadpool(
        lambda: plan(fleet_request, manager=NodeManager(), asker=asker)
    )
    if only_plan:
        return {"plan": the_plan.to_dict(), "job": None}
    if not the_plan.batches():
        raise ValidationError(
            "No selected server can run this action",
            details="Every server was skipped; the plan says why for each one.",
            field="targets",
        )
    # One copy for the check and the job: passing the check slides the session's
    # window, and the job must be bounded by the window as it stands afterwards.
    confirmed = dict(session)
    if the_plan.requires_elevation:
        ensure_elevated(request, confirmed)
    actor = actor_label(session)
    window_end = confirmed.get("elevated_until")
    job, described = await run_in_threadpool(
        lambda: start_job(
            the_plan,
            asker=asker,
            actor=actor,
            elevated=central_elevated(confirmed),
            window_end=float(window_end) if window_end is not None else None,
        )
    )
    return {
        "plan": described,
        "job": JobAcceptedResponse(
            job_id=job.id,
            status=job.status.value,
            message=f"{the_plan.spec.title} queued on {sum(map(len, the_plan.batches()))} servers",
            job=job.to_dict(),
        ),
    }


@router.post("/actions", response_model=ActionOut)
async def run_action(request: Request, body: ActionIn, session: Session) -> dict[str, Any]:
    """
    Plan a bulk action, and run it as a job unless ``plan`` is set.

    Args:
        request: The request.
        body: The action, its servers, its strategy and options.
        session: The authenticated session.

    Returns:
        The plan and, when it runs, the job. Poll the job, or
        ``GET /api/fleet/jobs/{id}`` for every server's state and words.

    Raises:
        ValidationError: An unknown action, option or server, or nothing to run.
    """
    return await _plan_and_start(request, session, _request_from(body), body.plan)


@router.get("/jobs", response_model=FleetJobsOut)
def list_jobs(session: Session, limit: Annotated[int, Query(ge=1, le=200)] = 50) -> dict[str, Any]:
    """
    List the fleet jobs, newest first.

    Args:
        session: The authenticated session.
        limit: How many.

    Returns:
        ``jobs``: each with its action, status, request and a count per state.
    """
    from noust.web.fleet_jobs import FleetJobs

    return {"jobs": FleetJobs().list(limit)}


@router.get("/jobs/{job_id}", response_model=FleetJobOut)
def get_fleet_job(job_id: str, session: Session) -> dict[str, Any]:
    """
    Describe one fleet job, with every server's state and words.

    Args:
        job_id: The job.
        session: The authenticated session.

    Returns:
        The job: ``status`` (``running``, ``succeeded``, ``failed``,
        ``aborted``, ``interrupted``), ``summary`` and ``nodes``.

    Raises:
        HTTPException: 404 for an unknown job.
    """
    from fastapi import HTTPException

    from noust.web.fleet_jobs import FleetJobs

    job = FleetJobs().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Fleet job not found: {job_id}")
    return job


@router.post("/jobs/{job_id}/retry", response_model=ActionOut)
async def retry_job(
    job_id: str,
    request: Request,
    session: Session,
    plan: Annotated[bool, Query(description="Only say what the retry would do")] = False,
) -> dict[str, Any]:
    """
    Run a job again on its servers that did not get done.

    Args:
        job_id: The job.
        request: The request.
        session: The authenticated session.
        plan: Only plan it.

    Returns:
        The plan and, unless planning, the new job.
    """
    from noust.web.fleet_jobs import retry_request

    fleet_request = await run_in_threadpool(retry_request, job_id)
    return await _plan_and_start(request, session, fleet_request, plan)
