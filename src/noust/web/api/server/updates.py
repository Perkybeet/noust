# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/server/updates``: what is pending, and applying it.

Applying is a job that runs the package manager in its own systemd unit (see
:mod:`noust.web.api.server.jobs`), because updating ``noust`` restarts the
console. Everything that can be decided before a job exists is decided here, so
the answer is immediate: a refusal (another package manager is running, a deploy
is in progress, the update would remove packages) is a 409 with what is in the
way, and no job is queued for it.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from noust.core.exceptions import ValidationError
from noust.managers.server.errors import (
    ConfirmationRequiredError,
    PreflightError,
    ServerError,
    UnsupportedHostError,
)
from noust.managers.server.pkg.base import (
    AutoUpdates,
    PendingUpdates,
    RestartProbe,
    UpdateScope,
)
from noust.managers.server.restarts import RestartPlan, plan_service_restarts
from noust.managers.server.summary import TTL_AUTO, TTL_RESTART, TTL_UPDATES
from noust.managers.server.updates import UpdateRecord
from noust.web.api.auth import get_current_session
from noust.web.api.deps import JobAcceptedResponse, require_elevated
from noust.web.api.server.common import (
    ServerRoute,
    accepted,
    audit_event,
    ensure_no_package_job,
    get_server_context,
)
from noust.web.api.server.jobs import (
    auto_updates_job,
    os_refresh_job,
    os_repair_job,
    os_update_job,
    restart_services_job,
)
from noust.web.api.server.models import (
    ApplyPlanOut,
    ApplyUpdatesRequest,
    AutoUpdatesOut,
    AutoUpdatesRequest,
    PackageOut,
    RebootOut,
    RefusedRestartOut,
    RestartPlanOut,
    RestartServicesRequest,
    UpdateRunOut,
    UpdatesOut,
)
from noust.web.auth import actor_label
from noust.web.jobs import JobType, get_job_manager

router = APIRouter(route_class=ServerRoute)

#: Most runs one listing returns.
MAX_RUNS = 50


def parse_scope(value: str) -> UpdateScope:
    """
    Read a scope from a request.

    Args:
        value: ``security`` or ``all``.

    Returns:
        The scope.

    Raises:
        ValidationError: It is neither.
    """
    try:
        return UpdateScope(value)
    except ValueError:
        raise ValidationError(
            f"Unknown scope: {value!r}", "Use 'security' or 'all'.", field="scope"
        ) from None


def _run_out(record: UpdateRecord) -> UpdateRunOut:
    """
    Describe an update run.

    Args:
        record: The run's record.

    Returns:
        Its API shape.
    """
    return UpdateRunOut(**record.to_dict())


@router.get("", response_model=UpdatesOut)
def list_updates(session: Annotated[dict, Depends(get_current_session)]) -> UpdatesOut:
    """
    Everything the updates tab shows.

    Served from the cache with its age; the first look computes it here, which
    takes about a second of ``apt-get -s``. ``POST /api/server/updates/refresh``
    renews the package lists and this answer.

    Args:
        session: The authenticated session.

    Returns:
        The pending updates with security ones marked, whether a reboot is due
        and why, the services running old libraries, the automatic updates and
        the update that is running now, if one is.
    """
    ctx = get_server_context()
    pending_fact = ctx.cache.get("updates", ctx.updates.pending, TTL_UPDATES, wait=True)
    restart_fact = ctx.cache.get("restart", ctx.updates.restart_probe, TTL_RESTART, wait=True)
    auto_fact = ctx.cache.get("auto", ctx.updates.backend.auto_status, TTL_AUTO, wait=True)

    platform = ctx.platform
    listing = pending_fact.value if pending_fact else None
    probe = restart_fact.value if restart_fact else None
    auto = auto_fact.value if auto_fact else None
    running = ctx.records.running()

    listing = listing or PendingUpdates(security_scope=platform.security_scope_supported)
    probe = probe or RestartProbe()
    auto = auto or AutoUpdates()
    return UpdatesOut(
        supported=platform.updates_supported,
        reason=platform.why_updates_unsupported() or None,
        security_scope=listing.security_scope,
        pending=listing.pending,
        security=listing.security,
        packages=[PackageOut(**asdict(package)) for package in listing.packages],
        kept_back=listing.kept_back,
        holds=listing.holds,
        broken=listing.broken,
        checked_at=listing.checked_at,
        lists_age_seconds=listing.lists_age_seconds,
        notes=listing.notes,
        error=pending_fact.error if pending_fact else None,
        reboot=RebootOut(
            required=probe.reboot.required,
            packages=list(probe.reboot.packages),
            reasons=list(probe.reboot.reasons),
            since=probe.reboot.since,
            source=probe.reboot.source,
            detector_available=probe.available,
        ),
        stale_services=list(probe.services),
        auto=AutoUpdatesOut(**asdict(auto)),
        running=_run_out(running[0]) if running else None,
    )


@router.get("/plan", response_model=ApplyPlanOut)
def plan_updates(
    session: Annotated[dict, Depends(get_current_session)],
    scope: Annotated[str, Query()] = "security",
    full: Annotated[bool, Query()] = False,
) -> ApplyPlanOut:
    """
    Say what applying updates would do, without doing it.

    Args:
        session: The authenticated session.
        scope: ``security`` or ``all``.
        full: A full upgrade, which may remove packages.

    Returns:
        The packages, what would be removed, which software the update disturbs,
        whether the console itself restarts, and the exact command.

    Raises:
        ValidationError: The scope is not one (400).
    """
    plan = get_server_context().updates.plan(parse_scope(scope), full=full)
    return ApplyPlanOut(
        scope=plan.scope.value,
        full=plan.full,
        packages=[PackageOut(**asdict(package)) for package in plan.packages],
        removals=list(plan.removals),
        impact=list(plan.impact),
        restarts_console=plan.restarts_console,
        command=" ".join(plan.argv),
    )


@router.get("/runs", response_model=list[UpdateRunOut])
def list_runs(
    session: Annotated[dict, Depends(get_current_session)],
    limit: Annotated[int, Query(ge=1, le=MAX_RUNS)] = 20,
) -> list[UpdateRunOut]:
    """
    List the updates that were run, newest first.

    Args:
        session: The authenticated session.
        limit: How many.

    Returns:
        The runs, each with what it installed, how it ended and its last output.
    """
    return [_run_out(record) for record in get_server_context().records.recent(limit)]


@router.get("/runs/{update_id}", response_model=UpdateRunOut)
def get_run(update_id: str, session: Annotated[dict, Depends(get_current_session)]) -> UpdateRunOut:
    """
    Read one run of an update.

    Args:
        update_id: The run's identifier.
        session: The authenticated session.

    Returns:
        The run.

    Raises:
        ValidationError: The identifier is not one (400).
        HTTPException: 404 when there is no such run.
    """
    record = get_server_context().records.read(update_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No update run {update_id}")
    return _run_out(record)


@router.post("/refresh", response_model=JobAcceptedResponse, status_code=202)
def refresh_updates(session: Annotated[dict, Depends(require_elevated)]) -> JobAcceptedResponse:
    """
    Renew the package lists, as a job.

    Args:
        session: The elevated session.

    Returns:
        The queued job. Its log is the package manager's own output.

    Raises:
        UnsupportedHostError: Updates are not managed here (501).
        HostBusyError: A package operation is in progress (409).
    """
    ctx = get_server_context()
    if not ctx.platform.updates_supported:
        raise UnsupportedHostError(
            "Updates cannot be managed on this system", ctx.platform.why_updates_unsupported()
        )
    ensure_no_package_job()
    actor = actor_label(session)
    job = get_job_manager().create_job(
        job_type=JobType.OS_REFRESH,
        name="Refresh the package lists",
        description="Downloading fresh package metadata and listing the pending updates",
        func=os_refresh_job,
        kwargs={"actor": actor},
        actor=actor,
    )
    audit_event("server.refresh", target="packages", stage="queued", job=job.id)
    return accepted(job, "Refresh queued")


@router.post("/apply", response_model=JobAcceptedResponse, status_code=202)
def apply_updates(
    body: ApplyUpdatesRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> JobAcceptedResponse:
    """
    Apply updates, as a job that runs in its own systemd unit.

    The unit is what lets this survive the console restarting when the ``noust``
    package is among the updates. Refused before any job exists when another
    package manager is running, a deploy or backup is in progress, or the
    update would remove packages and ``allow_removals`` was not given.

    Args:
        body: Which updates and whether removals are allowed.
        session: The elevated session.

    Returns:
        The queued job.

    Raises:
        ConfirmationRequiredError: The update would remove packages (409, with
            the list in ``required.removals``).
        HostBusyError: Another package manager or operation is running (409).
        PreflightError: A deploy is running, the disk is nearly full, nothing is
            marked security, or the package database is half configured (409).
        UnsupportedHostError: Updates are not managed here (501).
    """
    scope = parse_scope(body.scope)
    ctx = get_server_context()
    ensure_no_package_job()
    ctx.updates.preflight()
    plan = ctx.updates.plan(scope, full=body.full)
    if plan.removals and not body.allow_removals:
        raise ConfirmationRequiredError(
            f"This update would remove {len(plan.removals)} package(s)",
            "Read the list and repeat the request with allow_removals.",
            required={"removals": list(plan.removals)},
        )
    if scope is UpdateScope.SECURITY and not plan.packages:
        raise PreflightError(
            "There are no security updates to install",
            "Refresh the package lists first, or apply all updates.",
            blockers=["Nothing pending is marked as a security update"],
        )
    actor = actor_label(session)
    job = get_job_manager().create_job(
        job_type=JobType.OS_UPDATE,
        name=f"Apply {'security' if scope is UpdateScope.SECURITY else 'all'} updates",
        description=f"{len(plan.packages)} package(s) through {' '.join(plan.argv[:2])}",
        func=os_update_job,
        kwargs={
            "scope": scope.value,
            "full": body.full,
            "allow_removals": body.allow_removals,
            "actor": actor,
        },
        metadata={"scope": scope.value, "packages": len(plan.packages)},
        actor=actor,
    )
    audit_event(
        "server.update",
        target="packages",
        stage="queued",
        job=job.id,
        scope=scope.value,
        full=body.full,
        allow_removals=body.allow_removals,
        packages=len(plan.packages),
    )
    return accepted(job, "Update queued: it runs in its own unit and survives a console restart")


class RestartServicesAccepted(JobAcceptedResponse):
    """The queued restart, with the plan it follows."""

    restart: list[str]
    refused: list[RefusedRestartOut]
    restarts_console: bool


def _restart_plan(requested: list[str] | None) -> RestartPlan:
    """
    Plan the restarts from the update check's current list.

    Args:
        requested: The units chosen; every one when None.

    Returns:
        The plan.
    """
    ctx = get_server_context()
    fact = ctx.cache.get("restart", ctx.updates.restart_probe, TTL_RESTART, wait=True)
    probe = fact.value if fact and fact.value else RestartProbe()
    return plan_service_restarts(probe.services, requested)


def _refused(plan: RestartPlan) -> list[RefusedRestartOut]:
    """
    List what a plan leaves as it is.

    Args:
        plan: The plan.

    Returns:
        Each unit and why.
    """
    return [RefusedRestartOut(unit=unit, reason=reason) for unit, reason in plan.refused.items()]


@router.get("/restarts", response_model=RestartPlanOut)
def plan_restarts(session: Annotated[dict, Depends(get_current_session)]) -> RestartPlanOut:
    """
    Say which services on replaced libraries a restart would restart, and which not.

    Args:
        session: The authenticated session.

    Returns:
        The plan: what the update check reported, what restarts (the console's
        own unit last), what is left for a reboot and why.
    """
    plan = _restart_plan(None)
    return RestartPlanOut(
        services=list(plan.services),
        restart=list(plan.restart),
        refused=_refused(plan),
        restarts_console=plan.restarts_console,
    )


@router.post("/restarts", response_model=RestartServicesAccepted, status_code=202)
def restart_outdated_services(
    body: RestartServicesRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> RestartServicesAccepted:
    """
    Restart the services an update left on replaced libraries, as a job.

    Only units the update check reported, and never one that ends sessions,
    drops the network or stops containers (the guard is in ServiceManager).
    When the console's own unit is among them it restarts last, and the answer
    says so before it happens.

    Args:
        body: The units; every restartable one when omitted.
        session: The elevated session.

    Returns:
        The queued job and the plan it follows.

    Raises:
        PreflightError: Nothing asked for can be restarted from here (409, with
            each unit and why in ``blockers``).
    """
    plan = _restart_plan(body.services)
    if not plan.restart:
        raise PreflightError(
            "None of these services can be restarted from here",
            "Reboot the server to restart them with the new libraries.",
            blockers=[f"{unit}: {reason}" for unit, reason in plan.refused.items()]
            or ["No service runs replaced libraries"],
        )
    actor = actor_label(session)
    job = get_job_manager().create_job(
        job_type=JobType.SERVICE_ACTION,
        name="Restart the services on replaced libraries",
        description=", ".join(plan.restart),
        func=restart_services_job,
        kwargs={"services": list(plan.restart), "actor": actor},
        metadata={"services": list(plan.restart), "restarts_console": plan.restarts_console},
        actor=actor,
    )
    audit_event(
        "server.update",
        target="services",
        stage="queued",
        job=job.id,
        action="restart_services",
        services=list(plan.restart),
    )
    message = "Restart queued"
    if plan.restarts_console:
        message += ": the console restarts last, and this page reconnects by itself"
    base = accepted(job, message)
    return RestartServicesAccepted(
        job_id=base.job_id,
        status=base.status,
        message=base.message,
        job=base.job,
        restart=list(plan.restart),
        refused=_refused(plan),
        restarts_console=plan.restarts_console,
    )


@router.post("/repair", response_model=JobAcceptedResponse, status_code=202)
def repair_updates(session: Annotated[dict, Depends(require_elevated)]) -> JobAcceptedResponse:
    """
    Finish a half-applied update, as a job.

    Args:
        session: The elevated session.

    Returns:
        The queued job.

    Raises:
        UnsupportedHostError: There is nothing to repair with on this system (501).
        HostBusyError: A package operation is in progress (409).
    """
    ctx = get_server_context()
    if not ctx.updates.backend.repair_commands():
        raise UnsupportedHostError(
            "There is no repair step on this system", "Only dpkg leaves a database half configured."
        )
    ensure_no_package_job()
    actor = actor_label(session)
    job = get_job_manager().create_job(
        job_type=JobType.OS_UPDATE,
        name="Repair the package database",
        description="Finishing what an interrupted update left half done",
        func=os_repair_job,
        kwargs={"actor": actor},
        actor=actor,
    )
    audit_event("server.update", target="packages", stage="queued", job=job.id, action="repair")
    return accepted(job, "Repair queued")


@router.get("/auto", response_model=AutoUpdatesOut)
def get_auto_updates(session: Annotated[dict, Depends(get_current_session)]) -> AutoUpdatesOut:
    """
    Read the state of the distribution's automatic updates.

    Args:
        session: The authenticated session.

    Returns:
        Which mechanism this system has, whether it is installed and enabled,
        whether it applies only security updates, and whether it reboots by itself.
    """
    ctx = get_server_context()
    fact = ctx.cache.get("auto", ctx.updates.backend.auto_status, TTL_AUTO, wait=True)
    if fact is None or fact.value is None:
        raise ServerError(
            "The automatic updates could not be read", (fact.error if fact else None) or ""
        )
    return AutoUpdatesOut(**asdict(fact.value))


@router.put("/auto", response_model=JobAcceptedResponse, status_code=202)
def set_auto_updates(
    body: AutoUpdatesRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> JobAcceptedResponse:
    """
    Turn the automatic updates on or off, as a job (it may install a package).

    Noust never turns on an automatic reboot: a server with clients'
    applications does not restart by itself.

    Args:
        body: The wanted state.
        session: The elevated session.

    Returns:
        The queued job.

    Raises:
        UnsupportedHostError: The mechanism cannot be changed here (501).
        HostBusyError: A package operation is in progress (409).
    """
    ctx = get_server_context()
    status = ctx.updates.backend.auto_status()
    if not status.supported:
        raise UnsupportedHostError("Automatic updates cannot be changed here", status.detail)
    ensure_no_package_job()
    actor = actor_label(session)
    job = get_job_manager().create_job(
        job_type=JobType.SERVER_ACTION,
        name=f"{'Enable' if body.enabled else 'Disable'} automatic updates",
        description=f"{status.mechanism}: {'security only' if body.security_only else 'all updates'}",
        func=auto_updates_job,
        kwargs={"enabled": body.enabled, "security_only": body.security_only, "actor": actor},
        actor=actor,
    )
    audit_event(
        "server.update",
        target="automatic-updates",
        stage="queued",
        job=job.id,
        action="auto",
        enabled=body.enabled,
        security_only=body.security_only,
    )
    return accepted(job, "Change queued")


__all__ = ["router"]
