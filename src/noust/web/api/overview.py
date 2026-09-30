# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Overview over the API: everything the dashboard needs, in one call.

A thin translation of :func:`noust.managers.overview.get_overview`: every figure
is gathered there from the manager that owns it, so the console, a script and
the central's fleet aggregator (which asks each node for exactly this through the
node proxy) read one answer. The answer is cached for a few seconds: the
console polls, and the certificate and backup reads are not free. The cache is
forgotten whenever something it shows may have changed: after every write the
API accepted (:func:`invalidate_after_mutation`, called by the security
middleware every call passes through) and whenever a job ends
(:func:`invalidate_after_job`, subscribed to the job manager for the life of the
console), so the page an operator returns to after acting is not 15 seconds old.

A section whose probe failed carries an ``error`` in the tool's own words and
its other fields empty, so a consumer can tell "nothing to report" from "could
not look".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from noust.managers.overview import get_overview, reset_cache
from noust.web import metrics_collector
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute

if TYPE_CHECKING:
    from noust.web.jobs import Job

router = APIRouter(route_class=NoustErrorRoute)

#: Methods that change nothing, so never make the cached Overview stale.
_READS = frozenset({"GET", "HEAD", "OPTIONS"})


def invalidate_after_mutation(method: str, path: str, status_code: int) -> None:
    """
    Forget the cached Overview after a write the API accepted.

    Every API call passes the security middleware, which calls this as the
    response leaves; no endpoint has to remember to. A refusal or a read
    changes nothing and keeps the answer.

    Args:
        method: The request method.
        path: The request path.
        status_code: The status the endpoint answered with.
    """
    if method.upper() in _READS or not path.startswith("/api/") or not 200 <= status_code < 300:
        return
    reset_cache()


def invalidate_after_job(job: Job) -> None:
    """
    Forget the cached Overview when a job ends.

    A deploy, a backup or a certificate renewal changes what the Overview
    shows when it finishes, well after the 202 that queued it. Subscribed to
    the job manager with ``subscribe_all`` by the console's lifespan.

    Args:
        job: The job that changed.
    """
    from noust.web.jobs import FINISHED_STATUSES

    if job.status in FINISHED_STATUSES:
        reset_cache()


Session = Annotated[dict, Depends(get_current_session)]


class ServerInfo(BaseModel):
    """
    Which server this is.

    Attributes:
        name: Its hostname.
        version: The Noust it runs.
        role: ``server`` (deploys applications) or ``hub`` (only manages other
            servers).
    """

    name: str = ""
    version: str = ""
    role: str = "server"


class AppsFigure(BaseModel):
    """
    Applications by state.

    Attributes:
        running: Serving.
        failed: A unit failed.
        stopped: Not running, or restarting.
        static: Served straight from disk: nothing to run.
        error: Why this could not be read, in the tool's own words.
    """

    running: int = 0
    failed: int = 0
    stopped: int = 0
    static: int = 0
    error: str | None = None


class DeploysFigure(BaseModel):
    """
    Deployments since local midnight.

    Attributes:
        since: Midnight, with its UTC offset.
        total: Deployments started since.
        succeeded: Of those, finished well.
        failed: Of those, failed.
        rolled_back: Of those, undone.
        running: Still in progress.
        finished: Finished either way.
        last_at: When the newest one ended (or started).
        last_status: How it ended.
        last_domain: Which application it was.
        error: Why this could not be read.
    """

    since: str | None = None
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    rolled_back: int = 0
    running: int = 0
    finished: int = 0
    last_at: str | None = None
    last_status: str | None = None
    last_domain: str | None = None
    error: str | None = None


class CertificatesFigure(BaseModel):
    """
    Certificates by how long they have left.

    Attributes:
        total: How many there are.
        expiring: Expiring within ``warning_days``.
        expired: Already expired.
        next_days: Days to the nearest expiry, or null with none known.
        warning_days: The window ``expiring`` counts.
        error: Why this could not be read.
    """

    total: int = 0
    expiring: int = 0
    expired: int = 0
    next_days: int | None = None
    warning_days: int = 21
    error: str | None = None


class BackupsFigure(BaseModel):
    """
    Backups of the last 24 hours against the applications that should have them.

    Attributes:
        window_hours: How far back it looks.
        apps: Applications (previews excluded).
        scheduled: Applications with a backup schedule.
        with_backup_24h: Applications backed up in the window.
        unprotected_scheduled: Scheduled applications with no backup in it.
        failed_24h: Backup jobs that failed in it.
        last_at: When the newest backup was taken.
        error: Why this could not be read.
    """

    window_hours: int = 24
    apps: int = 0
    scheduled: int = 0
    with_backup_24h: int = 0
    unprotected_scheduled: int = 0
    failed_24h: int = 0
    last_at: str | None = None
    error: str | None = None


class ForecastReason(BaseModel):
    """
    Why there is no disk forecast.

    Attributes:
        code: ``no_history``, ``insufficient_history`` or ``not_growing``.
        message: One sentence.
    """

    code: str
    message: str


class DiskFigure(BaseModel):
    """
    The disk the applications live on.

    Attributes:
        used: Bytes in use.
        total: Bytes in all.
        percent: Percent used.
        free_percent: Percent free.
        forecast_full_days: Estimated days until it fills, from recent growth;
            an estimate, and null when there is none.
        forecast_reason: Why there is none.
        error: Why this could not be read.
    """

    used: int = 0
    total: int = 0
    percent: float = 0.0
    free_percent: float = 100.0
    forecast_full_days: int | None = None
    forecast_reason: ForecastReason | None = None
    error: str | None = None


class UpdatesReason(BaseModel):
    """
    Why updates are not reported.

    Attributes:
        code: ``not_available`` or ``unsupported``.
        message: One sentence.
    """

    code: str
    message: str


class UpdatesFigure(BaseModel):
    """
    Pending operating system updates.

    Attributes:
        supported: Whether this server's package manager is supported; null
            when unknown.
        available: Updates pending; null until the first check has run.
        security: Of those, security updates.
        reboot_required: Whether a reboot is due.
        checked_at: When the pending list was read.
        reason: Why nothing is reported.
        error: The failure, verbatim.
    """

    supported: bool | None = None
    available: int | None = None
    security: int | None = None
    reboot_required: bool | None = None
    checked_at: str | None = None
    reason: UpdatesReason | None = None
    error: str | None = None


class AttentionReason(BaseModel):
    """
    One reason an item needs attention.

    Attributes:
        kind: ``state``, ``deploy``, ``certificate``, ``unit`` or ``monitor``.
        severity: ``fail`` or ``warn``.
        code: Machine-readable and stable; the console words it. ``service_failed``,
            ``service_restarting``, ``deploy_failed``, ``deploy_rolled_back``,
            ``certificate_expired``, ``certificate_expires_today``,
            ``certificate_expires_in``, ``unit_failed``, ``unit_restarting``,
            ``monitor_finding``.
        params: Values for the console's sentence (days, a signal).
        detail: The system's own words, verbatim.
        when: When it happened.
        deployment_id: A deployment to open for the full story.
        actions: What can be done, as identifiers the console maps to buttons:
            ``view_log``, ``diagnose``, ``view_deployment``,
            ``renew_certificate``, ``open_service``, ``open_observation``.
    """

    kind: str
    severity: str
    code: str
    params: dict[str, Any] = Field(default_factory=dict)
    detail: str | None = None
    when: str | None = None
    deployment_id: int | None = None
    actions: list[str] = Field(default_factory=list)


class AttentionItem(BaseModel):
    """
    Something that needs an operator.

    Attributes:
        id: Stable across refreshes.
        subject: What it is about: ``{"kind": "app", "domain": ...}``,
            ``{"kind": "certificate", "domain": ...}``, ``{"kind": "unit",
            "name": ...}`` or ``{"kind": "monitor", "process": ..., "pid": ...}``.
        title: A domain, a unit, a process.
        severity: The worst of its reasons.
        reasons: Each reason, with its own severity.
    """

    id: str
    subject: dict[str, Any]
    title: str
    severity: str
    reasons: list[AttentionReason]


class ActivityEntry(BaseModel):
    """
    One thing that happened lately.

    Attributes:
        id: Stable identifier (``job:<id>``).
        type: What it was: ``deploy``, ``update``, ``backup``, ``restore``...
        title: The job's name.
        status: How it went: ``pending``, ``running``, ``completed``,
            ``failed``, ``cancelled``.
        domain: The application it acted on, if any.
        actor: Who asked, if known.
        at: When it ended (or started), with its UTC offset.
    """

    id: str
    type: str
    title: str = ""
    status: str = ""
    domain: str | None = None
    actor: str | None = None
    at: str | None = None


class HistoryFigure(BaseModel):
    """
    Whether metrics history is being recorded.

    Attributes:
        recording: True while a collector is running.
        host: ``daemon`` or ``console``.
        reason: Why not, with the fix (``code``, ``message``, ``fix``...).
        error: Why this could not be read.
    """

    recording: bool = False
    host: str | None = None
    reason: dict[str, Any] | None = None
    error: str | None = None


class SparkFigure(BaseModel):
    """
    The last hour of CPU and memory, for a thumbnail.

    Attributes:
        cpu: One-minute means of CPU percent; null where nothing was recorded.
        mem: One-minute means of used memory in bytes.
        step: Seconds per point.
        from_: Start of the hour (``from`` on the wire).
        to: End of it.
        error: Why this could not be read.
    """

    cpu: list[float | None] = Field(default_factory=list)
    mem: list[float | None] = Field(default_factory=list)
    step: int = 60
    from_: int | None = Field(default=None, alias="from")
    to: int | None = None
    error: str | None = None


class OverviewResponse(BaseModel):
    """
    Everything the Overview shows.

    Attributes:
        schema_version: The shape of this answer (``schema`` in the body).
        generated_at: When it was collected (it may be a few seconds old).
        server: Which server.
        apps: Applications by state.
        deploys: Deployments today.
        certificates: Certificates expiring.
        backups: Backups in the last 24 hours.
        disk: The disk and its forecast.
        updates: Pending operating system updates.
        attention: What needs an operator, worst first.
        attention_total: How many there are, if the list was cut.
        activity: What happened lately, newest first.
        history: Whether metrics history is being recorded.
        spark: The last hour of CPU and memory, when asked for.
    """

    schema_version: int = Field(alias="schema", default=1)
    generated_at: str
    server: ServerInfo
    apps: AppsFigure
    deploys: DeploysFigure
    certificates: CertificatesFigure
    backups: BackupsFigure
    disk: DiskFigure
    updates: UpdatesFigure
    attention: list[AttentionItem] = Field(default_factory=list)
    attention_total: int = 0
    activity: list[ActivityEntry] = Field(default_factory=list)
    history: HistoryFigure | None = None
    spark: SparkFigure | None = None


def server_summary() -> dict[str, Any] | None:
    """
    Read the server area's summary, when this Noust has one.

    The operating system updates are read by that area's manager, not
    reimplemented here (:func:`~noust.managers.server.summary.build_summary`
    answers from cached facts and computes the slow ones in the background).

    Returns:
        Its summary, or None when the server area is not part of this Noust.
    """
    try:
        from noust.managers.server.summary import build_summary
        from noust.web.api.server.common import get_server_context
    except ImportError:
        return None
    return build_summary(get_server_context(), wait=False)


@router.get("", response_model=OverviewResponse, response_model_by_alias=True)
def overview(
    session: Session,
    spark: Annotated[
        bool,
        Query(description="Include the last hour of CPU and memory as two short series."),
    ] = False,
) -> Any:
    """
    Answer the six key figures, what needs attention and what happened lately.

    Args:
        session: Authenticated session, injected.
        spark: Include the last hour of CPU and memory: what a fleet view draws
            as a thumbnail per server.

    Returns:
        The Overview.
    """
    store = metrics_collector.get_metrics_store()
    return get_overview(
        spark=spark,
        metrics=store,
        updates=server_summary,
        recording=metrics_collector.history_status,
    )
