"""
The Overview in one call: what a dashboard needs to answer "how is this server".

The console's Overview used to assemble itself from a dozen requests (machine,
apps, deployments, certificates, units, observations, seven metric series) and
count things in the browser, wrongly when a day had more than 200 deployments.
:func:`collect_overview` is the one answer, computed here from the managers that
already own each fact (rule 3: nothing is reimplemented, only gathered):

- six key figures: applications running and failed, deployments today with their
  failures, certificates expiring or expired, backups in the last 24 hours
  against the applications that should have them, disk headroom with a forecast
  of when it fills, and pending operating system updates;
- the **attention list**: everything that needs an operator, worst first, each
  item carrying a machine-readable reason and the system's own words;
- **recent activity** for a timeline, from the jobs the console and the CLI ran;
- optionally the last hour of CPU and memory as two short series, so a fleet
  view can draw a thumbnail per server from the same call.

The answer is plain JSON-ready data with a ``schema`` number, so the central's
fleet aggregator asks each node for it through the node proxy and a node of an
older Noust simply lacks fields. A section whose probe fails carries an
``error`` in the tool's own words instead of vanishing: a dashboard with a hole
and no reason reads as "all fine".
"""

from __future__ import annotations

import logging
import socket
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from noust.core.exceptions import NoustError
from noust.core.timeutil import to_iso_offset
from noust.monitor import sampler
from noust.monitor.process_monitor import days_until_expiry

log = logging.getLogger(__name__)

#: The shape of the answer; bumped when a field is removed or changes meaning.
SCHEMA = 1

# The states :func:`~noust.web.machine.classify_apps` sorts applications into.
APP_RUNNING = "running"
APP_FAILED = "failed"
APP_STOPPED = "stopped"
APP_STATIC = "static"
APP_RESTARTING = "restarting"
APP_UNMANAGED = "running_unmanaged"

#: Days before expiry at which a certificate needs attention: the same window
#: the console's attention list has always used.
CERT_WARNING_DAYS = 21

#: How far back "backups in the last 24 hours" looks.
BACKUP_WINDOW_HOURS = 24

#: How long an answer is reused. The console asks every few seconds, and the
#: certificate and backup reads are the expensive ones.
CACHE_SECONDS = 15.0

#: Most items the attention list returns; ``attention_total`` says how many
#: there were.
ATTENTION_LIMIT = 100

#: Most entries of the activity timeline.
ACTIVITY_LIMIT = 20

#: Disk forecast: a fit needs at least this much history, and a disk that would
#: take longer than this to fill is "not growing" for any practical purpose.
FORECAST_MIN_DAYS = 3
FORECAST_MAX_DAYS = 730
FORECAST_WINDOW_DAYS = 14

#: What a failing section can raise; anything else is a bug and stays loud.
_SECTION_ERRORS: tuple[type[Exception], ...] = (
    NoustError,
    OSError,
    ValueError,
    sqlite3.Error,
)

_RANK = {"fail": 0, "warn": 1}

_cache_lock = threading.Lock()
_cache: tuple[float, bool, dict[str, Any]] | None = None


def reset_cache() -> None:
    """Forget the last answer, so the next call collects again."""
    global _cache
    with _cache_lock:
        _cache = None


def get_overview(
    *,
    spark: bool = False,
    max_age: float = CACHE_SECONDS,
    **collaborators: Any,
) -> dict[str, Any]:
    """
    Answer with a recent Overview, collecting a fresh one when it is stale.

    Args:
        spark: Include the last hour of CPU and memory.
        max_age: Seconds an answer may be reused. Zero collects every time.
        **collaborators: Passed to :func:`collect_overview`.

    Returns:
        The Overview.
    """
    global _cache
    now = time.monotonic()
    with _cache_lock:
        if _cache is not None and max_age > 0:
            taken_at, had_spark, answer = _cache
            if now - taken_at < max_age and (had_spark or not spark):
                return answer
    answer = collect_overview(spark=spark, **collaborators)
    with _cache_lock:
        _cache = (now, spark, answer)
    return answer


def collect_overview(
    *,
    spark: bool = False,
    store: Any | None = None,
    services: Callable[[], list[dict[str, Any]]] | None = None,
    certificates: Callable[[], list[Any]] | None = None,
    backups: Callable[[], list[Any]] | None = None,
    observations: Callable[[], list[dict[str, Any]]] | None = None,
    metrics: Any | None = None,
    updates: Callable[[], dict[str, Any] | None] | None = None,
    recording: Callable[[], Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Gather everything the Overview shows.

    Every collaborator defaults to the real manager and is injectable, so the
    whole call is testable without a machine.

    Args:
        spark: Include the last hour of CPU and memory as two short series.
        store: The Noust store (applications, deployments, jobs, schedules).
        services: Lists the systemd units Noust manages
            (:func:`~noust.web.machine.fetch_service_states`).
        certificates: Lists certificates as :class:`CertificateInfo` records.
        backups: Lists backups, newest first, as :class:`BackupMetadata`.
        observations: Lists the monitor's open observations.
        metrics: The metrics store, for the disk forecast and the spark.
        updates: Returns the server area's summary (``updates`` and ``reboot``
            sections), or None when this Noust has none. The integration point
            for operating system updates: the manager that reads them is not
            duplicated here.
        recording: Says whether metrics history is being recorded.
        now: The current moment; the clock when None.

    Returns:
        A JSON-serialisable mapping; see the module docstring.
    """
    from noust.web.machine import classify_apps, fetch_service_states

    moment = now or datetime.now()
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    if store is None:
        from noust.core.store import get_store

        store = get_store()

    units = _read("services", services or fetch_service_states, [])
    apps = _read("applications", store.list_apps, [])

    overview: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "server": _server(),
        "apps": _section("apps", lambda: _apps_figure(classify_apps(units))),
        "deploys": _section("deploys", lambda: _deploys(store, moment)),
        "certificates": _section("certificates", lambda: _certificates(certificates)),
        "backups": _section("backups", lambda: _backups(store, backups, apps, moment)),
        "disk": _section("disk", lambda: _disk(metrics, moment)),
        "updates": _updates(updates),
    }
    items, total = _attention(store, units, certificates, observations, apps)
    overview["attention"] = items
    overview["attention_total"] = total
    overview["activity"] = _section("activity", lambda: _activity(store))
    if recording is not None:
        overview["history"] = _section("history", lambda: _history(recording))
    if spark:
        overview["spark"] = _section("spark", lambda: _spark(metrics))
    return overview


# ---------------------------------------------------------------------------
# Plumbing: a section that fails says so
# ---------------------------------------------------------------------------


def _read(name: str, read: Callable[[], Any], default: Any) -> Any:
    """
    Read something several sections share, falling back on a known failure.

    Args:
        name: What is being read, for the log.
        read: Reads it.
        default: The value to use when the read fails.

    Returns:
        The value, or the default.
    """
    try:
        return read()
    except _SECTION_ERRORS as exc:
        log.warning("The overview could not read %s: %s", name, exc)
        return default


def _section(name: str, build: Callable[[], Any]) -> Any:
    """
    Build a section, replacing a failure with a description of it.

    Args:
        name: The section, for the log.
        build: Builds it.

    Returns:
        The section, or ``{"error": "..."}`` with the tool's own words.
    """
    try:
        return build()
    except _SECTION_ERRORS as exc:
        log.warning("The overview section %r failed: %s", name, exc)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# The sections
# ---------------------------------------------------------------------------


def _server() -> dict[str, Any]:
    """
    Name this server.

    Returns:
        Its hostname, Noust's version and the central role (``server`` or
        ``hub``).
    """
    from noust import __version__
    from noust.central import role

    try:
        current = role()
    except (NoustError, OSError):
        current = "server"
    return {"name": socket.gethostname(), "version": __version__, "role": current}


def _apps_figure(states: dict[str, str]) -> dict[str, int]:
    """
    Count applications by state.

    Args:
        states: What :func:`~noust.web.machine.classify_apps` returned.

    Returns:
        Applications running, failed and stopped (a restarting one counts as
        stopped, as the machine snapshot counts it), static, and running
        outside their units.
    """
    values = list(states.values())
    return {
        "running": values.count(APP_RUNNING),
        "failed": values.count(APP_FAILED),
        "stopped": values.count(APP_STOPPED) + values.count(APP_RESTARTING),
        "static": values.count(APP_STATIC),
        "unmanaged": values.count(APP_UNMANAGED),
    }


def _deploys(store: Any, moment: datetime) -> dict[str, Any]:
    """
    Count today's deployments and name the last one.

    Args:
        store: The Noust store.
        moment: Now.

    Returns:
        Totals since local midnight and how they ended, and the newest.
    """
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    counts = store.count_deployments_since(midnight.isoformat())
    finished = {status: n for status, n in counts.items() if status not in ("queued", "running")}
    latest = store.list_deployments(limit=1)
    last = latest[0] if latest else None
    return {
        "since": to_iso_offset(midnight),
        "total": sum(counts.values()),
        "succeeded": counts.get("success", 0),
        "failed": counts.get("failed", 0),
        "rolled_back": counts.get("rolled_back", 0),
        "running": counts.get("running", 0) + counts.get("queued", 0),
        "finished": sum(finished.values()),
        "last_at": to_iso_offset(last.finished_at or last.started_at) if last else None,
        "last_status": last.status if last else None,
        "last_domain": last.domain if last else None,
    }


def _certificates(read: Callable[[], list[Any]] | None) -> dict[str, Any]:
    """
    Count certificates that are expiring or expired.

    Args:
        read: Lists certificates; certbot's, by default.

    Returns:
        The total, how many expire within :data:`CERT_WARNING_DAYS`, how many
        already have, the days to the next expiry and the warning window.
    """
    listed = _list_certificates(read)
    days = [d for d in (_days(cert) for cert in listed) if d is not None]
    return {
        "total": len(listed),
        "expiring": sum(0 <= d < CERT_WARNING_DAYS for d in days),
        "expired": sum(d < 0 for d in days),
        "next_days": min(days) if days else None,
        "warning_days": CERT_WARNING_DAYS,
    }


def _list_certificates(read: Callable[[], list[Any]] | None) -> list[Any]:
    """
    List certificates through the certificate manager.

    Args:
        read: An override, or None for certbot's own list.

    Returns:
        The certificate records.
    """
    if read is not None:
        return list(read())
    from noust.managers.cert_manager import CertManager

    return list(CertManager(verbose=False).list_certificates())


def _days(cert: Any) -> int | None:
    """
    Days a certificate has left.

    Args:
        cert: A certificate record.

    Returns:
        Whole days, negative once expired, or None when unknown.
    """
    return days_until_expiry(cert.expiry, datetime.now().date())


def _parse_moment(text: str | None) -> datetime | None:
    """
    Read a stored timestamp.

    Args:
        text: An ISO 8601 string, naive (local) or with an offset.

    Returns:
        An aware datetime, or None for anything else.
    """
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.astimezone()


def _backups(
    store: Any,
    read: Callable[[], list[Any]] | None,
    apps: list[Any],
    moment: datetime,
) -> dict[str, Any]:
    """
    Compare the applications with the backups of the last 24 hours.

    Args:
        store: The Noust store.
        read: Lists backups, newest first; the backup manager's by default.
        apps: The deployed applications.
        moment: Now.

    Returns:
        How many applications there are, how many have a scheduled backup, how
        many were backed up in the window, how many scheduled ones were not, how
        many backup jobs failed in the window and when the newest backup is.
    """
    if read is not None:
        backups = list(read())
    else:
        from noust.managers.backup_manager import BackupManager

        backups = list(BackupManager(verbose=False).list_backups())

    cutoff = moment.astimezone() - timedelta(hours=BACKUP_WINDOW_HOURS)
    domains = {app.domain for app in apps if not getattr(app, "preview_parent", None)}
    recent = {
        backup.domain
        for backup in backups
        if backup.domain in domains
        and (taken := _parse_moment(backup.created_at)) is not None
        and taken >= cutoff
    }
    scheduled = {schedule.app_domain for schedule in store.list_backup_schedules()}
    failed = sum(
        1
        for job in store.list_jobs(limit=200)
        if job.type == "backup"
        and job.status == "failed"
        and (when := _parse_moment(job.finished_at or job.created_at)) is not None
        and when >= cutoff
    )
    newest = next((b.created_at for b in backups if _parse_moment(b.created_at) is not None), None)
    return {
        "window_hours": BACKUP_WINDOW_HOURS,
        "apps": len(domains),
        "scheduled": len(scheduled),
        "with_backup_24h": len(recent),
        "unprotected_scheduled": len(scheduled - recent),
        "failed_24h": failed,
        "last_at": to_iso_offset(newest),
    }


def _disk(metrics: Any | None, moment: datetime) -> dict[str, Any]:
    """
    Read the disk the applications live on, and forecast when it fills.

    Args:
        metrics: The metrics store, for the growth; None to skip the forecast.
        moment: Now.

    Returns:
        Bytes used and total, the percentage, and ``forecast_full_days`` with,
        when there is none, a ``forecast_reason``.
    """
    disk = sampler.read_disk(sampler.resolve_apps_root())
    days, reason = forecast_full_days(metrics, disk.used, disk.total, int(moment.timestamp()))
    return {
        "used": disk.used,
        "total": disk.total,
        "percent": disk.percent,
        "free_percent": round(100.0 - disk.percent, 1),
        "forecast_full_days": days,
        "forecast_reason": reason,
    }


def forecast_full_days(
    metrics: Any | None, used: int, total: int, now: int
) -> tuple[int | None, dict[str, str] | None]:
    """
    Estimate in how many days the disk fills, from its recent growth.

    A least-squares line through the hourly means of the last two weeks of used
    bytes. It is an estimate and says so: no forecast is given without three
    days of history, for a disk that is not growing, or for one that would take
    more than two years.

    Args:
        metrics: The metrics store, or None.
        used: Bytes in use now.
        total: Bytes in all.
        now: The current time, epoch seconds.

    Returns:
        ``(days, None)`` or ``(None, reason)`` with a ``code``
        (``no_history``, ``insufficient_history``, ``not_growing``) and a
        sentence.
    """
    if metrics is None or total <= 0:
        return None, {
            "code": "no_history",
            "message": "There is no metrics history to forecast from.",
        }
    result = metrics.query_range(
        ["disk.used_bytes"],
        start=now - FORECAST_WINDOW_DAYS * 86_400,
        end=now,
        step=3_600,
    )
    points = [(ts, mean) for ts, mean, _peak in result.series[0].points if mean is not None]
    if len(points) < 24 or points[-1][0] - points[0][0] < FORECAST_MIN_DAYS * 86_400:
        return None, {
            "code": "insufficient_history",
            "message": f"A forecast needs at least {FORECAST_MIN_DAYS} days of history.",
        }
    count = len(points)
    mean_t = sum(t for t, _ in points) / count
    mean_v = sum(v for _, v in points) / count
    spread = sum((t - mean_t) ** 2 for t, _ in points)
    slope = sum((t - mean_t) * (v - mean_v) for t, v in points) / spread if spread else 0.0
    per_day = slope * 86_400
    if per_day <= 0 or total <= used:
        return None, {
            "code": "not_growing",
            "message": "The disk is not growing, so it is not expected to fill.",
        }
    days = (total - used) / per_day
    if days > FORECAST_MAX_DAYS:
        return None, {
            "code": "not_growing",
            "message": "At its current growth the disk would take over two years to fill.",
        }
    return max(0, int(days)), None


def _updates(read: Callable[[], dict[str, Any] | None] | None) -> dict[str, Any]:
    """
    Report pending operating system updates, from the server area's manager.

    Args:
        read: Returns that area's summary (its ``updates`` and ``reboot``
            sections), or None; None when this Noust has no such area.

    Returns:
        ``available`` and ``security`` counts, ``reboot_required``,
        ``checked_at`` and whether updates are ``supported`` here; ``None``
        counts when they are not known yet (the probe is slow and runs in the
        background); a ``reason`` when there is nothing to report and why.
    """
    unknown: dict[str, Any] = {
        "supported": None,
        "available": None,
        "security": None,
        "reboot_required": None,
        "checked_at": None,
    }
    if read is None:
        return {
            **unknown,
            "reason": {
                "code": "not_available",
                "message": "This Noust does not report operating system updates.",
            },
        }
    try:
        summary = read()
    except _SECTION_ERRORS as exc:
        log.warning("The overview could not read the updates: %s", exc)
        return {**unknown, "error": str(exc)}
    if summary is None:
        return {**unknown, "reason": {"code": "not_available", "message": "No update information."}}

    section = summary.get("updates") or {}
    reboot = summary.get("reboot") or {}
    figure: dict[str, Any] = {
        "supported": section.get("supported"),
        "available": section.get("pending"),
        "security": section.get("security"),
        "reboot_required": reboot.get("required"),
        "checked_at": section.get("checked_at"),
    }
    if section.get("supported") is False:
        figure["reason"] = {
            "code": "unsupported",
            "message": str(section.get("reason") or "Updates cannot be read on this server."),
        }
    if section.get("error"):
        figure["error"] = section["error"]
    return figure


# ---------------------------------------------------------------------------
# What needs an operator
# ---------------------------------------------------------------------------


def _attention(
    store: Any,
    services: list[dict[str, Any]],
    certificates: Callable[[], list[Any]] | None,
    observations: Callable[[], list[dict[str, Any]]] | None,
    apps: list[Any],
) -> tuple[list[dict[str, Any]], int]:
    """
    Gather every problem that needs an operator, worst first.

    The same sources and rules the console's attention list has always had,
    computed where the data is: an application whose service failed or is
    restarting, one whose newest deployment failed or was rolled back, a
    certificate about to expire or expired, a unit Noust manages that failed
    beyond the applications' own, and an open monitor observation. A source that
    cannot be read is skipped and logged; what remains is still worth showing.

    Args:
        store: The Noust store.
        services: Noust's systemd units.
        certificates: Lists certificates.
        observations: Lists open observations.
        apps: The deployed applications.

    Returns:
        The items, at most :data:`ATTENTION_LIMIT`, and how many there were.
    """
    from noust.web.machine import classify_apps

    items: dict[str, dict[str, Any]] = {}

    def add(key: str, subject: dict[str, Any], title: str, reason: dict[str, Any]) -> None:
        item = items.get(key)
        if item is None:
            items[key] = {
                "id": key,
                "subject": subject,
                "title": title,
                "severity": reason["severity"],
                "reasons": [reason],
            }
            return
        item["reasons"].append(reason)
        if _RANK[reason["severity"]] < _RANK[item["severity"]]:
            item["severity"] = reason["severity"]

    domains = {app.domain for app in apps}

    try:
        states = classify_apps(services)
    except _SECTION_ERRORS as exc:
        log.warning("The attention list could not classify the applications: %s", exc)
        states = {}
    for domain, state in states.items():
        if state == APP_FAILED:
            add(
                f"app:{domain}",
                {"kind": "app", "domain": domain},
                domain,
                _reason("state", "fail", "service_failed", actions=["view_log", "diagnose"]),
            )
        elif state == APP_RESTARTING:
            add(
                f"app:{domain}",
                {"kind": "app", "domain": domain},
                domain,
                _reason("state", "warn", "service_restarting", actions=["view_log", "diagnose"]),
            )
        elif state == APP_UNMANAGED:
            # It serves, but nothing done to its unit reaches it and a server
            # restart would not bring it back; the app's page hands it back.
            add(
                f"app:{domain}",
                {"kind": "app", "domain": domain},
                domain,
                _reason("state", "warn", "running_outside_unit"),
            )

    _add_deployments(store, domains, add)
    _add_certificates(certificates, domains, add)
    _add_units(services, add)
    _add_observations(observations, add)

    ordered = sorted(items.values(), key=lambda i: (_RANK[i["severity"]], i["title"].lower()))
    return ordered[:ATTENTION_LIMIT], len(ordered)


def _reason(
    kind: str,
    severity: str,
    code: str,
    *,
    params: dict[str, Any] | None = None,
    detail: str | None = None,
    when: str | None = None,
    deployment_id: int | None = None,
    actions: list[str] | None = None,
) -> dict[str, Any]:
    """
    Build one reason an item needs attention.

    Args:
        kind: Where it comes from: ``state``, ``deploy``, ``certificate``,
            ``unit`` or ``monitor``.
        severity: ``fail`` or ``warn``.
        code: Machine-readable and stable; the console words it.
        params: Values for the console's sentence.
        detail: The system's own words, verbatim.
        when: When it happened, with its UTC offset.
        deployment_id: A deployment to open for the full story.
        actions: What the operator can do about it, as identifiers the console
            maps to buttons (``view_log``, ``diagnose``, ``renew_certificate``...).

    Returns:
        The reason.
    """
    return {
        "kind": kind,
        "severity": severity,
        "code": code,
        "params": params or {},
        "detail": detail,
        "when": when,
        "deployment_id": deployment_id,
        "actions": actions or [],
    }


def _first_line(text: str | None) -> str | None:
    """
    Take the first non-empty line of an error, where tools put the error itself.

    Args:
        text: A possibly multi-line error.

    Returns:
        The line, or None.
    """
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return None


def _add_deployments(store: Any, domains: set[str], add: Callable[..., None]) -> None:
    """
    Add the applications whose newest deployment did not succeed.

    Args:
        store: The Noust store.
        domains: The applications that still exist.
        add: Adds a reason to an item.
    """
    try:
        latest = store.get_latest_deployments(sorted(domains))
    except _SECTION_ERRORS as exc:
        log.warning("The attention list could not read the deployments: %s", exc)
        return
    for domain, deployment in latest.items():
        if deployment.status not in ("failed", "rolled_back"):
            continue
        failed = deployment.status == "failed"
        add(
            f"app:{domain}",
            {"kind": "app", "domain": domain},
            domain,
            _reason(
                "deploy",
                "fail" if failed else "warn",
                "deploy_failed" if failed else "deploy_rolled_back",
                detail=_first_line(deployment.error),
                when=to_iso_offset(deployment.finished_at or deployment.started_at),
                deployment_id=deployment.id,
                actions=["view_deployment", "diagnose"],
            ),
        )


def _add_certificates(
    read: Callable[[], list[Any]] | None, domains: set[str], add: Callable[..., None]
) -> None:
    """
    Add certificates that are expiring or expired.

    Args:
        read: Lists certificates.
        domains: The applications that still exist, so a certificate of one is
            shown on it.
        add: Adds a reason to an item.
    """
    try:
        listed = _list_certificates(read)
    except _SECTION_ERRORS as exc:
        log.warning("The attention list could not read the certificates: %s", exc)
        return
    for cert in listed:
        days = _days(cert)
        if days is None or days >= CERT_WARNING_DAYS:
            continue
        name, expiry = cert.name, cert.expiry
        if days < 0:
            reason = _reason(
                "certificate", "fail", "certificate_expired", actions=["renew_certificate"]
            )
        elif days == 0:
            reason = _reason(
                "certificate", "warn", "certificate_expires_today", actions=["renew_certificate"]
            )
        else:
            reason = _reason(
                "certificate",
                "warn",
                "certificate_expires_in",
                params={"days": days},
                actions=["renew_certificate"],
            )
        reason["params"]["valid_until"] = expiry
        if name in domains:
            add(f"app:{name}", {"kind": "app", "domain": name}, name, reason)
        else:
            add(
                f"certificate:{name}",
                {"kind": "certificate", "domain": name},
                name,
                reason,
            )


def _add_units(services: list[dict[str, Any]], add: Callable[..., None]) -> None:
    """
    Add Noust's own units that failed, beyond the applications' units.

    An application's state already accounts for its own units.

    Args:
        services: Noust's systemd units.
        add: Adds a reason to an item.
    """
    from noust.web.machine import classify_unit

    for service in services:
        if service.get("app"):
            continue
        state = classify_unit(service)
        if state not in ("failed", "busy"):
            continue
        name = str(service.get("name", ""))
        failed = state == "failed"
        add(
            f"unit:{name}",
            {"kind": "unit", "name": name},
            name,
            _reason(
                "unit",
                "fail" if failed else "warn",
                "unit_failed" if failed else "unit_restarting",
                actions=["open_service"],
            ),
        )


def _add_observations(
    read: Callable[[], list[dict[str, Any]]] | None, add: Callable[..., None]
) -> None:
    """
    Add what the monitor noted and nobody acknowledged.

    Args:
        read: Lists open observations; the observation store's by default.
        add: Adds a reason to an item.
    """
    try:
        if read is not None:
            rows = read()
        else:
            from noust.monitor.observation_store import ObservationStore

            rows = ObservationStore(verbose=False).recent(limit=20)
    except _SECTION_ERRORS as exc:
        log.warning("The attention list could not read the monitor's observations: %s", exc)
        return
    for row in rows:
        if row.get("acknowledged"):
            continue
        name = str(row.get("process_name", ""))
        pid = row.get("pid")
        add(
            f"monitor:{row.get('id', f'{name}-{pid}')}",
            {"kind": "monitor", "process": name, "pid": pid},
            name,
            # The monitor observes and never acts: what it noted is for a person
            # to look at, not a failure of anything Noust runs.
            _reason(
                "monitor",
                "warn",
                "monitor_finding",
                params={"severity": row.get("severity"), "signal": row.get("signal")},
                detail=row.get("detail") or None,
                when=to_iso_offset(row.get("observed_at")),
                actions=["open_observation"],
            ),
        )


# ---------------------------------------------------------------------------
# Activity, history, spark
# ---------------------------------------------------------------------------


def _activity(store: Any) -> list[dict[str, Any]]:
    """
    List what happened lately: the jobs the console and the CLI ran.

    Args:
        store: The Noust store.

    Returns:
        The newest :data:`ACTIVITY_LIMIT` jobs, newest first, each with what it
        was, on which application, how it ended and who asked. Audit entries are
        not mixed in: they name sessions and addresses and need a stronger
        scope than this read has.
    """
    jobs = store.list_jobs(limit=ACTIVITY_LIMIT * 3)
    entries = []
    for job in jobs:
        at = job.finished_at or job.started_at or job.created_at
        entries.append(
            {
                "id": f"job:{job.id}",
                "type": job.type,
                "title": job.name,
                "status": job.status,
                "domain": job.domain,
                "actor": job.actor,
                "at": to_iso_offset(at),
                "_sort": _parse_moment(at) or datetime.min.replace(tzinfo=timezone.utc),
            }
        )
    entries.sort(key=lambda entry: entry["_sort"], reverse=True)
    for entry in entries:
        del entry["_sort"]
    return entries[:ACTIVITY_LIMIT]


def _history(recording: Callable[[], Any]) -> dict[str, Any]:
    """
    Say whether metrics history is being recorded.

    Args:
        recording: Returns a :class:`~noust.monitor.collector.RecordingStatus`.

    Returns:
        Whether it is, by whom, and why not when it is not.
    """
    status = recording()
    return {
        "recording": status.recording,
        "host": status.host,
        "reason": status.reason.to_dict() if status.reason else None,
    }


def _spark(metrics: Any | None) -> dict[str, Any]:
    """
    Read the last hour of CPU and memory as two short series.

    Args:
        metrics: The metrics store, or None.

    Returns:
        Sixty one-minute means each, ``null`` where nothing was recorded, and
        the domain they cover.
    """
    if metrics is None:
        return {"cpu": [], "mem": [], "step": 60, "from": None, "to": None}
    end = metrics.now()
    result = metrics.query_range(
        ["cpu.percent", "mem.used_bytes"], start=end - 3_600, end=end, step=60
    )
    cpu, mem = result.series
    return {
        "cpu": [mean for _ts, mean, _peak in cpu.points],
        "mem": [mean for _ts, mean, _peak in mem.points],
        "step": result.step,
        "from": result.start,
        "to": result.end,
    }
