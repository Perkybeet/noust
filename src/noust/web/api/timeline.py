# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``GET /api/timeline``: everything Noust knows about a stretch of time.

A translation of :class:`noust.managers.timeline.TimelineBuilder` to HTTP, and
the place each source's permission is applied: the route itself needs
``server.read`` (:mod:`noust.web.permissions.routes_timeline`), and every source
keeps the permission its own page needs - the journal ``secrets.reveal``, the
audit trail ``audit.read``, command lines ``secrets.reveal``. A source the
caller may not read is answered as withheld, with the permission it needs, so
the console says what is missing instead of showing less without a word.

On a central the console reaches a node's timeline through the node proxy, like
any other route: each server answers for what it saw.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from noust.core.exceptions import ValidationError
from noust.managers.timeline import (
    SOURCE_AUDIT,
    SOURCE_DEPLOYMENTS,
    SOURCE_JOBS,
    SOURCE_JOURNAL,
    SOURCE_MONITOR,
    SOURCE_PROCESSES,
    SOURCES,
    STATE_SHOWN,
    TimelineBuilder,
    TimelineReaders,
    default_readers,
)
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute
from noust.web.auth import sees_command_lines
from noust.web.permissions import Permission
from noust.web.permissions.enforce import has_permission

router = APIRouter(route_class=NoustErrorRoute)

#: The permission each source needs, the one its own page needs.
SOURCE_PERMISSIONS: dict[str, str] = {
    SOURCE_JOURNAL: Permission.SECRETS_REVEAL,
    SOURCE_AUDIT: Permission.AUDIT_READ,
    SOURCE_DEPLOYMENTS: Permission.APPS_READ,
    SOURCE_JOBS: Permission.APPS_READ,
    SOURCE_MONITOR: Permission.SERVER_READ,
    SOURCE_PROCESSES: Permission.SERVER_READ,
}


class TimelineEventOut(BaseModel):
    """
    One thing that happened.

    Attributes:
        at: When, epoch seconds.
        source: ``journal``, ``audit``, ``deployments``, ``jobs`` or ``monitor``.
        kind: ``journal``, ``audit``, ``deployment``, ``job``, ``observation``,
            ``unit_failed``, ``unit_recovered`` or ``boot``.
        level: ``error``, ``warning``, ``notice`` or ``info``.
        text: What the source says, verbatim.
        unit: The unit it concerns.
        app: The application it concerns.
        actor: Who did it.
        status: Its outcome.
        ref: What to open: a deployment's id, a job's id.
        priority: The journal's priority, 0 to 7.
        details: Small extras.
    """

    at: float
    source: str
    kind: str
    level: str
    text: str
    unit: str | None = None
    app: str | None = None
    actor: str | None = None
    status: str | None = None
    ref: str | None = None
    priority: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ProcessRowOut(BaseModel):
    """
    One process in one minute's ranking.

    Attributes:
        position: 1 for the biggest.
        pid: Process id.
        name: Its name.
        user: The account it runs as.
        cpu_percent: CPU over the minute; 100 is one core.
        memory_bytes: Resident memory.
        memory_percent: Share of RAM.
        app: Its Noust application, by domain.
        owner_kind: ``unit``, ``container`` or ``pool``.
        owner: The unit, container or pool.
        command: Its command line, redacted; null when the caller may not read
            command lines.
    """

    position: int
    pid: int
    name: str
    user: str
    cpu_percent: float
    memory_bytes: int
    memory_percent: float
    app: str | None = None
    owner_kind: str | None = None
    owner: str | None = None
    command: str | None = None


class ProcessMinuteOut(BaseModel):
    """
    The processes of one minute.

    Attributes:
        at: The minute, epoch seconds.
        cpu: The top five by CPU.
        memory: The top five by memory.
    """

    at: int
    cpu: list[ProcessRowOut]
    memory: list[ProcessRowOut]


class ProcessHistoryOut(BaseModel):
    """
    The process samples of the stretch.

    Attributes:
        minutes: The minutes kept, oldest first.
        total_minutes: How many had samples; more than ``minutes`` when only the
            busiest were kept.
        since: The oldest sample there is at all; null when processes were
            never sampled.
        commands: Whether command lines are included.
    """

    minutes: list[ProcessMinuteOut]
    total_minutes: int
    since: int | None = None
    commands: bool


class SourceStatusOut(BaseModel):
    """
    What became of one source.

    Attributes:
        source: The source.
        state: ``shown``, ``withheld``, ``failed`` or ``skipped``.
        count: Entries it contributed.
        truncated: It had more than it may contribute.
        permission: The permission it needs, when withheld.
        message: Why it failed.
        evidence: What the system said, verbatim.
    """

    source: str
    state: str
    count: int = 0
    truncated: bool = False
    permission: str | None = None
    message: str | None = None
    evidence: str | None = None


class TimelineOut(BaseModel):
    """
    Response for ``GET /api/timeline``.

    Attributes:
        start: First moment, epoch seconds.
        end: Last moment.
        app: The application it is narrowed to.
        events: Every event, oldest first.
        processes: The process samples; null when not shown.
        sources: What became of each source.
    """

    start: int
    end: int
    app: str | None = None
    events: list[TimelineEventOut]
    processes: ProcessHistoryOut | None = None
    sources: list[SourceStatusOut]


def build_readers() -> TimelineReaders:
    """
    Build the readers of this console: the server area's journal and runner.

    Returns:
        The readers; replaced in tests.
    """
    from noust.web.api.server.common import get_server_context
    from noust.web.metrics_collector import get_metrics_store

    context = get_server_context()
    readers = default_readers(get_metrics_store(), runner=context.runner)
    readers.journal = context.journal
    return readers


def _sources(values: list[str] | None) -> list[str] | None:
    """
    Read the ``sources`` filter, repeated or comma-separated.

    Args:
        values: What the query carried.

    Returns:
        The source names, or None for every source.
    """
    if not values:
        return None
    return [name.strip() for value in values for name in value.split(",") if name.strip()]


@router.get("", response_model=TimelineOut)
def read_timeline(
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
    start: Annotated[int, Query(ge=0, description="First moment, epoch seconds")],
    end: Annotated[int, Query(ge=0, description="Last moment, epoch seconds")],
    app: Annotated[
        str | None, Query(max_length=253, description="Narrow to one application")
    ] = None,
    sources: Annotated[
        list[str] | None,
        Query(description=f"Only these sources: {', '.join(SOURCES)}; every one when omitted"),
    ] = None,
) -> TimelineOut:
    """
    Merge everything that happened in a stretch into one ordered timeline.

    Args:
        request: The request, for recording the audit read.
        session: The authenticated session.
        start: First moment, epoch seconds.
        end: Last moment.
        app: Narrow it to this application, by domain.
        sources: Only these sources.

    Returns:
        The events oldest first, the process samples, and what became of each
        source: shown, withheld (with the permission it needs) or failed (with
        the system's words).

    Raises:
        ValidationError: The stretch, a source or the application is not valid
            (400).
    """
    if app is not None and not app.strip():
        raise ValidationError("The application is empty", "Pass a domain.", field="app")
    withheld = {
        source: permission
        for source, permission in SOURCE_PERMISSIONS.items()
        if not has_permission(session, permission)
    }
    timeline = TimelineBuilder(build_readers()).build(
        start,
        end,
        app=app,
        sources=_sources(sources),
        withheld=withheld,
        show_commands=sees_command_lines(session),
    )
    if any(s.source == SOURCE_AUDIT and s.state == STATE_SHOWN for s in timeline.sources):
        # Reading the trail is itself on the record, as on the audit page.
        from noust.web.api.audit import _record_read

        _record_read(request, session, "audit log")
    return TimelineOut(**timeline.to_dict())


__all__ = ["SOURCE_PERMISSIONS", "build_readers", "router"]
