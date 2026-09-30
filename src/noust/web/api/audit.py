# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The audit log over HTTP: read, verify, export, review, and its health.

A client of :mod:`noust.core.audit`, like ``noust audit`` at the terminal: the
same log, the same chain, the same catalog. Reading it is itself a sensitive
read (it names people, addresses and what they did), so each actor's reading
is recorded, once per :data:`READ_RECORD_SECONDS` rather than once per page
the console loads. Each route's permission is in
:mod:`noust.web.permissions.routes_audit`.

``GET /api/audit`` keeps the 3.0 fields the console's Activity page reads
(``timestamp``, ``action``, ``result``, ``actor``, ``client_ip``,
``resource``, ``detail``) and adds the 3.1 ones alongside.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from noust.core.audit import Actor, get_log, health, record
from noust.core.audit.catalog import CATEGORIES, EVENTS
from noust.core.audit.chain import LIMITATION
from noust.core.audit.log import category_of
from noust.core.exceptions import ValidationError
from noust.web.api.deps import NoustErrorRoute
from noust.web.auth import get_audit_logger, get_client_ip, require_auth
from noust.web.pydantic_compat import iso_offset_validator

router = APIRouter(route_class=NoustErrorRoute)

#: Most entries a single page may carry.
MAX_LIMIT = 200

#: One ``audit.read`` per actor per this many seconds: the console pages and
#: polls, and a record per request would bury what it records.
READ_RECORD_SECONDS = 600

_reads_lock = threading.Lock()
_last_read: dict[tuple[str, str], float] = {}


def _actor(request: Request, session: dict[str, Any]) -> Actor:
    """
    The audit actor behind a request.

    Args:
        request: The request.
        session: Its authenticated payload.

    Returns:
        The console's principal as an actor, or one read from the payload's
        label when the principal module cannot build it.
    """
    from noust.web.auth import actor_label
    from noust.web.permissions.principal import actor_of

    built = actor_of(session)
    if isinstance(built, Actor):
        return built if built.source else replace(built, source=get_client_ip(request))
    return Actor.from_label(actor_label(session), source=get_client_ip(request))


def _record_read(request: Request, session: dict[str, Any], what: str) -> None:
    actor = _actor(request, session)
    now = time.monotonic()
    key = (str(get_log().path), actor.label)
    with _reads_lock:
        last = _last_read.get(key)
        if last is not None and now - last < READ_RECORD_SECONDS:
            return
        _last_read[key] = now
    record("audit.read", actor=actor, target=what)


def _log_disabled() -> bool:
    """Auditing was switched off for this console (``SecurityConfig.audit_enabled``)."""
    installed = get_audit_logger()
    return installed is None or not installed.enabled


class AuditActor(BaseModel):
    """
    Who an event names.

    Attributes:
        kind: ``user``, ``token``, ``master``, ``fleet``, ``cli``, ``system``
            or ``anonymous``.
        id: A stable identifier, when there is one.
        name: The human-readable name.
        role: The role it acted with.
        via: The channel it came through.
        source: Where from.
    """

    kind: str
    id: str | None = None
    name: str | None = None
    role: str | None = None
    via: str | None = None
    source: str | None = None


class AuditEntry(BaseModel):
    """
    One audit event.

    Attributes:
        timestamp: When it was recorded, with its UTC offset.
        action: The event, for example ``apps.delete``.
        result: Outcome, for example ``ok``, ``success`` or ``denied``.
        actor: Label of who acted: an account, ``token:<name>``, ``master``,
            ``cli:<login>`` or ``anonymous``.
        client_ip: Address the request came from.
        resource: Target of the action.
        detail: One line of context. Never a credential.
        seq: Position in the chain; None for a line written before 3.1.
        id: Unique id of the event.
        category: The catalog category (``access``, ``change``, ``read``...);
            for a line written before 3.1, the one it is filed under.
        severity: Syslog severity it is shipped with.
        correlation_id: Links the events and host actions of one request,
            command or job.
        who: The structured actor.
        details: Structured context, secrets already removed.
        sensitive: The event records a sensitive read.
    """

    timestamp: str
    action: str
    result: str
    actor: str
    client_ip: str | None = None
    resource: str | None = None
    detail: str | None = None
    seq: int | None = None
    id: str | None = None
    category: str | None = None
    severity: int | None = None
    correlation_id: str | None = None
    who: AuditActor | None = None
    details: dict[str, Any] | None = None
    sensitive: bool = False

    _iso_timestamps = iso_offset_validator("timestamp")


class AuditListResponse(BaseModel):
    """
    Response for ``GET /api/audit``.

    Attributes:
        items: The matching entries, newest first.
        next_before: Pass as ``before`` to fetch the next page, or None when
            this page reached the end of the log.
    """

    items: list[AuditEntry]
    next_before: str | None = None


def _detail_of(raw: dict[str, Any]) -> str | None:
    if raw.get("detail"):
        return str(raw["detail"])
    details = raw.get("details")
    if isinstance(details, dict) and details:
        return ", ".join(
            f"{key}={value if isinstance(value, str) else json.dumps(value, sort_keys=True)}"
            for key, value in details.items()
        )
    return None


def _to_entry(raw: dict[str, Any]) -> AuditEntry:
    """
    Convert one line of the log into the API model.

    Args:
        raw: An event as the log stores it, 3.0 or 3.1.

    Returns:
        The API representation.
    """
    who = raw.get("who")
    return AuditEntry(
        timestamp=str(raw.get("ts", "")),
        action=str(raw.get("action", "")),
        result=str(raw.get("result", "")),
        actor=str(raw.get("actor", "")),
        client_ip=raw.get("ip"),
        resource=raw.get("resource"),
        detail=_detail_of(raw),
        seq=raw.get("seq") if isinstance(raw.get("seq"), int) else None,
        id=raw.get("id"),
        category=category_of(raw),
        severity=raw.get("sev") if isinstance(raw.get("sev"), int) else None,
        correlation_id=raw.get("corr"),
        who=AuditActor(**who) if isinstance(who, dict) and who.get("kind") else None,
        details=raw.get("details") if isinstance(raw.get("details"), dict) else None,
        sensitive=bool(raw.get("sensitive")),
    )


@router.get("", response_model=AuditListResponse)
def list_audit_entries(
    request: Request,
    session: Annotated[dict, Depends(require_auth)],
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
    before: Annotated[str | None, Query(description="Only entries older than this cursor")] = None,
    action: Annotated[str | None, Query(description="Filter by exact action")] = None,
    result: Annotated[str | None, Query(description="Filter by exact result")] = None,
    actor: Annotated[str | None, Query(description="Filter by exact actor")] = None,
    category: Annotated[str | None, Query(description="Filter by catalog category")] = None,
    categories: Annotated[
        list[str] | None,
        Query(
            description=(
                "Only entries of one of these categories, as a view of the console reads"
                " them: a request's generic line is left out when its request recorded"
                " an event of its own"
            )
        ),
    ] = None,
    correlation_id: Annotated[
        str | None, Query(description="Only the events of one request, command or job")
    ] = None,
    target: Annotated[str | None, Query(description="Filter by exact target")] = None,
) -> AuditListResponse:
    """
    List audit entries, newest first, with keyset pagination.

    Args:
        request: The request.
        session: The authenticated session.
        limit: Maximum entries to return.
        before: Cursor from a previous page's ``next_before``.
        action: Only entries with this exact action.
        result: Only entries with this exact result.
        actor: Only entries with this exact actor.
        category: Only entries of this catalog category.
        categories: Only entries of one of these categories, request lines
            described by their own request's events left out.
        correlation_id: Only entries with this correlation id.
        target: Only entries on this exact target.

    Returns:
        The matching entries and the cursor for the next page.
    """
    if _log_disabled():
        return AuditListResponse(items=[], next_before=None)
    _record_read(request, session, "audit log")
    raw_entries = get_log().read(
        limit=limit,
        before=before,
        action=action,
        result=result,
        actor=actor,
        category=category,
        categories=categories,
        correlation_id=correlation_id,
        target=target,
    )
    items = [_to_entry(entry) for entry in raw_entries]
    next_before = str(raw_entries[-1].get("ts", "")) if len(items) == limit else None
    return AuditListResponse(items=items, next_before=next_before)


class BrokenLinkOut(BaseModel):
    """
    Where the chain breaks.

    Attributes:
        file: The log file.
        line: Line number in it.
        seq: The sequence number the line claims.
        reason: What is wrong.
    """

    file: str
    line: int
    seq: int | None = None
    reason: str


class VerifyResponse(BaseModel):
    """
    Response for ``GET /api/audit/verify``.

    Attributes:
        ok: Whether the chain holds.
        checked: Chained events checked.
        legacy: Lines from before the chain, which cannot be checked.
        first_seq: Oldest chained event.
        last_seq: Newest chained event.
        last_mac: Its MAC: what a receiver's latest checkpoint should hold.
        broken: The first broken link, when there is one.
        notes: Things worth knowing that are not breaks.
        limitation: What a pass does not prove.
    """

    ok: bool
    checked: int
    legacy: int
    first_seq: int | None = None
    last_seq: int | None = None
    last_mac: str | None = None
    broken: BrokenLinkOut | None = None
    notes: list[str] = Field(default_factory=list)
    limitation: str


@router.get("/verify", response_model=VerifyResponse)
def verify_audit_log(
    request: Request, session: Annotated[dict, Depends(require_auth)]
) -> VerifyResponse:
    """
    Check the HMAC chain and name the first broken link.

    The check is recorded (``audit.verify``). A pass proves the log is
    consistent under the local key; root on the machine holds that key, so
    the shipped copy is what proves it is original.

    Args:
        request: The request.
        session: The authenticated session.

    Returns:
        The result.
    """
    result = get_log().verify()
    record(
        "audit.verify",
        actor=_actor(request, session),
        outcome="ok" if result.ok else "failure",
        details=result.to_dict(),
    )
    payload = result.to_dict()
    return VerifyResponse(
        ok=result.ok,
        checked=result.checked,
        legacy=result.legacy,
        first_seq=result.first_seq,
        last_seq=result.last_seq,
        last_mac=result.last_mac,
        broken=BrokenLinkOut(**payload["broken"]) if payload["broken"] else None,
        notes=result.notes,
        limitation=LIMITATION,
    )


class SinkOut(BaseModel):
    """
    One shipping destination.

    Attributes:
        sink_id: Its name.
        seq: The last event it received.
        delivered_at: When it last received one.
        error: Its last error, verbatim.
        error_at: When that happened.
        lag_seconds: How far behind the log it is.
        degraded: It has been failing for longer than ``audit.sink_lag_minutes``.
    """

    sink_id: str
    seq: int | None = None
    delivered_at: str | None = None
    error: str | None = None
    error_at: str | None = None
    lag_seconds: float | None = None
    degraded: bool = False


class AuditStatusResponse(BaseModel):
    """
    Response for ``GET /api/audit/status``: whether the trail works.

    Attributes:
        status: ``ok``, ``warning`` or ``error``.
        problems: One actionable sentence per problem, worst first.
        total_bytes: Size of the log.
        failing: Writes are failing right now.
        failures: Failed writes since the console started.
        last_failure: The last write error, verbatim.
        last_failure_at: When it happened.
        sinks: The shipping destinations.
    """

    status: str
    problems: list[str] = Field(default_factory=list)
    total_bytes: int = 0
    failing: bool = False
    failures: int = 0
    last_failure: str | None = None
    last_failure_at: str | None = None
    sinks: list[SinkOut] = Field(default_factory=list)


@router.get("/status", response_model=AuditStatusResponse)
def audit_status(session: Annotated[dict, Depends(require_auth)]) -> AuditStatusResponse:
    """
    Report whether the audit trail works: writes, key, size and destinations.

    Args:
        session: The authenticated session.

    Returns:
        The report.
    """
    report = health()
    return AuditStatusResponse(
        status=report.status,
        problems=report.problems,
        total_bytes=report.total_bytes,
        failing=report.failing,
        failures=report.failures,
        last_failure=report.last_failure,
        last_failure_at=report.last_failure_at,
        sinks=[SinkOut(**sink) for sink in report.sinks],
    )


class ReviewRequest(BaseModel):
    """
    Body of ``POST /api/audit/reviews``.

    Attributes:
        period_start: First day reviewed, ``YYYY-MM-DD``.
        period_end: Last day reviewed, ``YYYY-MM-DD``.
        notes: What was looked at and what was found.
    """

    period_start: str = Field(..., min_length=10, max_length=10)
    period_end: str = Field(..., min_length=10, max_length=10)
    notes: str = Field(default="", max_length=4000)


class ReviewOut(BaseModel):
    """
    One recorded review.

    Attributes:
        timestamp: When it was recorded.
        reviewer: Who attested it.
        period_start: First day reviewed.
        period_end: Last day reviewed.
        notes: What was found.
        events_in_period: Events the period held when it was reviewed.
        chain_ok: Whether the chain verified at that moment.
        chain_head_seq: The chain's head then.
    """

    timestamp: str
    reviewer: str
    period_start: str | None = None
    period_end: str | None = None
    notes: str = ""
    events_in_period: int | None = None
    chain_ok: bool | None = None
    chain_head_seq: int | None = None

    _iso_timestamps = iso_offset_validator("timestamp")


class ReviewListResponse(BaseModel):
    """
    Response for ``GET /api/audit/reviews``.

    Attributes:
        items: Reviews, newest first.
    """

    items: list[ReviewOut]


def _review_out(entry: dict[str, Any]) -> ReviewOut:
    raw = entry.get("details")
    details: dict[str, Any] = raw if isinstance(raw, dict) else {}
    return ReviewOut(
        timestamp=str(entry.get("ts", "")),
        reviewer=str(entry.get("actor", "")),
        period_start=details.get("period_start"),
        period_end=details.get("period_end"),
        notes=str(details.get("notes") or ""),
        events_in_period=details.get("events_in_period"),
        chain_ok=details.get("chain_ok"),
        chain_head_seq=details.get("chain_head_seq"),
    )


def _day_bound(value: str, *, end: bool) -> str:
    try:
        day = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValidationError(
            f"{value!r} is not a date", details="Use YYYY-MM-DD.", field="period"
        ) from exc
    if end:
        day = day.replace(hour=23, minute=59, second=59, microsecond=999999)
    return day.isoformat()


@router.get("/reviews", response_model=ReviewListResponse)
def list_reviews(
    session: Annotated[dict, Depends(require_auth)],
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
) -> ReviewListResponse:
    """
    List the recorded reviews of the audit log, newest first.

    Args:
        session: The authenticated session.
        limit: Most reviews returned.

    Returns:
        The reviews.
    """
    entries = get_log().read(action="audit.review", limit=limit)
    return ReviewListResponse(items=[_review_out(entry) for entry in entries])


@router.post("/reviews", response_model=ReviewOut, status_code=201)
def create_review(
    body: ReviewRequest, request: Request, session: Annotated[dict, Depends(require_auth)]
) -> ReviewOut:
    """
    Attest that the audit log was reviewed for a period (ENS op.exp.8.r1).

    The chain is verified first, and the result is part of the attestation,
    which is itself an audit event (``audit.review``).

    Args:
        body: The period and the notes.
        request: The request.
        session: The authenticated session.

    Returns:
        The recorded review.

    Raises:
        ValidationError: A date is malformed or the period is reversed.
    """
    start = _day_bound(body.period_start, end=False)
    end = _day_bound(body.period_end, end=True)
    if start > end:
        raise ValidationError(
            "The period starts after it ends", details="Swap the dates.", field="period_start"
        )
    log = get_log()
    in_period = [
        entry for entry in log.read(limit=1_000_000, since=start) if str(entry.get("ts", "")) <= end
    ]
    result = log.verify()
    actor = _actor(request, session)
    details = {
        "period_start": body.period_start,
        "period_end": body.period_end,
        "notes": body.notes,
        "events_in_period": len(in_period),
        "denied_in_period": sum(1 for entry in in_period if entry.get("result") == "denied"),
        "chain_ok": result.ok,
        "chain_head_seq": result.last_seq,
        "chain_head_mac": result.last_mac,
    }
    record(
        "audit.review",
        actor=actor,
        target=f"{body.period_start}..{body.period_end}",
        details=details,
    )
    written = log.read(action="audit.review", limit=1)
    return _review_out(written[0]) if written else ReviewOut(timestamp="", reviewer=actor.label)


class CatalogEvent(BaseModel):
    """
    One event the audit log can record.

    Attributes:
        name: The event name.
        category: Its category.
        severity: Syslog severity of a successful occurrence.
        sensitive_read: It records someone reading something secret.
        description: What it means.
    """

    name: str
    category: str
    severity: int
    sensitive_read: bool
    description: str


class CatalogResponse(BaseModel):
    """
    Response for ``GET /api/audit/events``.

    Attributes:
        categories: Every category, in display order.
        events: Every event.
    """

    categories: list[str]
    events: list[CatalogEvent]


@router.get("/events", response_model=CatalogResponse)
def audit_catalog(session: Annotated[dict, Depends(require_auth)]) -> CatalogResponse:
    """
    List every event the audit log can record, for filters and labels.

    Args:
        session: The authenticated session.

    Returns:
        The catalog.
    """
    return CatalogResponse(
        categories=list(CATEGORIES),
        events=[
            CatalogEvent(
                name=name,
                category=spec.category,
                severity=spec.severity,
                sensitive_read=spec.sensitive_read,
                description=spec.description,
            )
            for name, spec in sorted(EVENTS.items())
        ],
    )


class NdjsonResponse(StreamingResponse):
    """A streamed body of one JSON object per line."""

    media_type = "application/x-ndjson"


@router.get(
    "/export",
    response_class=NdjsonResponse,
    responses={
        200: {
            "description": "The events, oldest first, one JSON object per line.",
            "content": {"application/x-ndjson": {"schema": {"type": "string"}}},
        }
    },
)
def export_audit_log(
    request: Request,
    session: Annotated[dict, Depends(require_auth)],
    since: Annotated[str | None, Query(description="From this ISO timestamp")] = None,
    until: Annotated[str | None, Query(description="Up to this ISO timestamp")] = None,
) -> NdjsonResponse:
    """
    Export the audit log as NDJSON, every field and the chain included.

    The export is recorded (``audit.export``).

    Args:
        request: The request.
        session: The authenticated session.
        since: Lower bound on ``ts``.
        until: Upper bound on ``ts``.

    Returns:
        The events, oldest first, one JSON object per line.
    """
    log = get_log()
    selected: list[dict[str, Any]] = []
    for entry in log.iter_newest_first():
        timestamp = str(entry.get("ts", ""))
        if until is not None and timestamp > until:
            continue
        if since is not None and timestamp < since:
            break
        selected.append(entry)
    selected.reverse()
    record(
        "audit.export",
        actor=_actor(request, session),
        target="api",
        details={"events": len(selected), "since": since, "until": until, "format": "ndjson"},
    )

    def lines() -> Iterator[bytes]:
        for entry in selected:
            yield (json.dumps(entry, sort_keys=True) + "\n").encode()

    return NdjsonResponse(
        lines(),
        headers={"Content-Disposition": 'attachment; filename="noust-audit.ndjson"'},
    )
