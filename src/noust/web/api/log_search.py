# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/logs/search``: one search over the logs of every application you can see.

A thin client of :class:`~noust.managers.log_search.LogSearch`, which ``noust log
search`` uses too (rule 3). The query language is the journal's, the one the Server page
searches with (:mod:`noust.managers.server.logquery`), and its limits are described there.
The applications searched are the ones the caller sees (a team's own, never another's);
a system unit is the Server page's to search. A central searches every server it manages
through ``/api/fleet/logs/search``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from noust.core import audit
from noust.core.teams import scoped_store
from noust.managers.log_retention import MAX_DAYS, MAX_MB, MIN_DAYS, MIN_MB, LogRetention
from noust.managers.log_search import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    LogSearch,
    process_names,
)
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import session_actor
from noust.web.api.deps import NoustErrorRoute, require_elevated

router = APIRouter(route_class=NoustErrorRoute)

Session = Annotated[dict[str, Any], Depends(get_current_session)]
Elevated = Annotated[dict[str, Any], Depends(require_elevated)]


class LogLineModel(BaseModel):
    """
    One line of a log.

    Attributes:
        ts: Epoch seconds.
        app: The application; empty for a system unit.
        source: ``journal`` or ``container``.
        unit: The unit, or the container's service.
        process: The application's process.
        level: The journal's level; null for a container's line.
        message: The text, with credentials masked.
    """

    ts: float
    app: str
    source: str
    unit: str
    process: str
    level: str | None = None
    message: str


class LogSearchResponse(BaseModel):
    """
    What a search found.

    Attributes:
        lines: The matching lines, newest first.
        matched: How many matched in all (of the entries examined).
        scanned: How many entries were examined.
        truncated: The scan stopped at its limit, so older matches may exist.
        since: Start of the window, epoch seconds.
        until: End of the window.
        notes: What was left out and why.
        apps: The applications that were searched.
        understood: The query written back in full, so the caller sees what was asked.
    """

    lines: list[LogLineModel]
    matched: int
    scanned: int
    truncated: bool
    since: float
    until: float
    notes: list[str]
    apps: list[str]
    understood: str = ""


@router.get("/search", response_model=LogSearchResponse)
def search_logs(
    session: Session,
    q: Annotated[
        str,
        Query(
            max_length=1000,
            description='Words ("quoted phrases", -excluded, a OR b, groups) and fields '
            "(@app:, @unit:, @level:, @pid:).",
        ),
    ] = "",
    app: Annotated[
        list[str] | None,
        Query(description="Search only these applications (their domains)."),
    ] = None,
    since: Annotated[
        str | None,
        Query(description="Start: epoch seconds, a time ago (15m, 2h, 7d) or a date. An hour ago."),
    ] = None,
    until: Annotated[str | None, Query(description="End: the same forms. Now.")] = None,
    level: Annotated[
        str | None, Query(description="The worst level kept: err, warning, info...")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> LogSearchResponse:
    """
    Search the logs of the applications the caller sees.

    Args:
        session: The authenticated session.
        q: The query.
        app: Narrow to these applications (domains); any the caller does not see
            is simply not searched.
        since: Start of the window.
        until: End of the window.
        level: The worst level kept.
        limit: Most lines to return.

    Returns:
        The lines, newest first, and how much was examined.

    Raises:
        ValidationError: 400 naming what in the query or the window is not acceptable,
            and where in the query when it is the query.
    """
    visible = scoped_store().list_apps()
    if app:
        wanted = {name.lower() for name in app}
        visible = [item for item in visible if item.domain.lower() in wanted]
    result = LogSearch().search(
        q,
        visible,
        since=since,
        until=until,
        level=level,
        limit=limit,
        processes=process_names(visible),
    )
    return LogSearchResponse(**result.to_dict(), apps=[item.domain for item in visible])


class RetentionModel(BaseModel):
    """
    How long the logs are kept.

    Attributes:
        days: The age past which entries are dropped; null for journald's default.
        max_use_mb: The most disk the journal may use; null for the default.
        persistent: The drop-in asks to keep the journal across reboots.
        journal_on_disk: The journal survives a reboot now.
        disk_usage: What the journal takes now, in journald's words.
        configured: A Noust drop-in is in place.
        foreign: A drop-in of that name that Noust did not write is in the way.
        notes: What an operator should know.
    """

    days: int | None = None
    max_use_mb: int | None = None
    persistent: bool = False
    journal_on_disk: bool = False
    disk_usage: str = ""
    configured: bool = False
    foreign: bool = False
    notes: list[str]


class RetentionBody(BaseModel):
    """
    What to keep; a field left out is kept as it is.

    Attributes:
        days: Drop entries older than this many days (1 to 3650).
        max_use_mb: Use at most this many megabytes (16 to 1048576).
        persistent: Keep the journal across reboots.
    """

    days: int | None = Field(default=None, ge=MIN_DAYS, le=MAX_DAYS)
    max_use_mb: int | None = Field(default=None, ge=MIN_MB, le=MAX_MB)
    persistent: bool | None = None


@router.get("/retention", response_model=RetentionModel)
def get_retention(session: Session) -> RetentionModel:
    """
    Say how long the logs are kept.

    Args:
        session: The authenticated session.

    Returns:
        The retention the drop-in sets, whether the journal survives a reboot
        and how much disk it takes.
    """
    return RetentionModel(**LogRetention().status().to_dict())


@router.put("/retention", response_model=RetentionModel)
def set_retention(body: RetentionBody, session: Elevated) -> RetentionModel:
    """
    Keep the logs this long, using at most this much disk.

    Args:
        body: What to keep.
        session: The authenticated session in sudo mode (journald is restarted).

    Returns:
        The retention afterwards.

    Raises:
        ValidationError: 400 when nothing is named or a number is out of range.
    """
    status = LogRetention().set(
        days=body.days, max_use_mb=body.max_use_mb, persistent=body.persistent
    )
    audit.record(
        "logs.retention",
        actor=session_actor(session),
        target="journald",
        details={"days": status.days, "max_use_mb": status.max_use_mb},
    )
    return RetentionModel(**status.to_dict())


@router.delete("/retention", response_model=RetentionModel)
def clear_retention(session: Elevated) -> RetentionModel:
    """
    Go back to journald's own defaults.

    Args:
        session: The authenticated session in sudo mode.

    Returns:
        The retention afterwards.
    """
    status = LogRetention().clear()
    audit.record("logs.retention", actor=session_actor(session), target="journald")
    return RetentionModel(**status.to_dict())
