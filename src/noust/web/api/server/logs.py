# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/server/logs``: the journal of any unit, with filters.

Not ``/api/services/{name}/logs``, which answers for the units Noust manages and
refuses every other. The system journal carries addresses, user names and, now
and then, somebody else's secret, so it needs the permission that reads secrets
(administrators); the route map says so, and the filters are checked in
:mod:`noust.managers.server.journal` before anything reaches journalctl.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from noust.managers.log_apps import app_units_resolver
from noust.managers.server.journal import DEFAULT_SCAN, MAX_CONTEXT, MAX_LINES, MAX_SCAN
from noust.managers.service_manager import ServiceManager
from noust.web.api.auth import get_current_session
from noust.web.api.server.common import ServerRoute, get_server_context
from noust.web.api.server.models import (
    BootOut,
    JournalContextOut,
    JournalEntryOut,
    JournalOut,
    JournalSearchOut,
    JournalUnitOut,
)

router = APIRouter(route_class=ServerRoute)


@router.get("", response_model=JournalOut)
def read_journal(
    session: Annotated[dict, Depends(get_current_session)],
    unit: Annotated[str | None, Query()] = None,
    priority: Annotated[str | None, Query()] = None,
    since: Annotated[str | None, Query()] = None,
    until: Annotated[str | None, Query()] = None,
    lines: Annotated[int, Query(ge=1, le=MAX_LINES)] = 200,
    q: Annotated[str | None, Query(max_length=200)] = None,
    boot: Annotated[int | None, Query(le=0)] = None,
    kernel: Annotated[bool, Query()] = False,
    cursor: Annotated[str | None, Query()] = None,
) -> JournalOut:
    """
    Read the journal.

    Args:
        session: The authenticated session.
        unit: A unit name; every unit when omitted.
        priority: A level (``err``) or 0 to 7; it and everything more serious is shown.
        since: ``2026-09-29``, ``2026-09-29 10:30`` or ``-30min``, ``-2h``, ``-7d``.
        until: The same forms.
        lines: How many entries, at most 1000.
        q: Keep only entries whose message contains this, ignoring case.
        boot: 0 for this boot, -1 for the one before.
        kernel: Only the kernel's messages.
        cursor: Continue after the ``next_cursor`` of a previous read.

    Returns:
        The entries oldest first, the cursor to continue from and whether there
        were more than asked for.

    Raises:
        ValidationError: A filter is not valid (400).
        ServerError: journalctl failed, carrying its output.
    """
    page = get_server_context().journal.read(
        text=q,
        unit=unit,
        priority=priority,
        since=since,
        until=until,
        lines=lines,
        boot=boot,
        kernel=kernel,
        after_cursor=cursor,
    )
    return JournalOut(
        entries=[JournalEntryOut(**entry.to_dict()) for entry in page.entries],
        next_cursor=page.next_cursor,
        truncated=page.truncated,
    )


@router.get("/search", response_model=JournalSearchOut)
def search_journal(
    session: Annotated[dict, Depends(get_current_session)],
    query: Annotated[str, Query(max_length=500)] = "",
    since: Annotated[str | None, Query()] = None,
    until: Annotated[str | None, Query()] = None,
    lines: Annotated[int, Query(ge=1, le=MAX_LINES)] = 300,
    scan: Annotated[int, Query(ge=1, le=MAX_SCAN)] = DEFAULT_SCAN,
    boot: Annotated[int | None, Query(le=0)] = None,
    kernel: Annotated[bool, Query()] = False,
) -> JournalSearchOut:
    """
    Answer a query over the journal: fields, ``AND``, ``OR``, ``-``, quotes and groups.

    ``@unit:nginx error -timeout`` is the entries of nginx that say "error" and do not say
    "timeout". The query is read and judged on the server against what ``journalctl``
    returns for the range (see :mod:`noust.managers.server.logquery`); nothing a person types
    is part of a command line but a unit name or a time, after the checks of
    :mod:`noust.managers.server.journal`. Reading the journal needs the right to reveal secrets,
    like ``GET /api/server/logs``.

    Args:
        session: The authenticated session.
        query: The query; empty matches every entry.
        since: ``2026-09-29``, ``2026-09-29 10:30``, ``-30min`` or ``@<epoch seconds>``.
        until: The same forms.
        lines: How many matching entries to return, the newest, at most 1000.
        scan: How many entries of the range to examine, the newest, at most 20000.
        boot: 0 for this boot, -1 for the one before.
        kernel: Only the kernel's messages.

    Returns:
        The matches oldest first, how many matched among how many were examined, whether the
        range held older entries that were not examined, and the query as it was understood.

    Raises:
        ValidationError: The query cannot be read (it says where), or a filter is not valid (400).
        ServerError: journalctl failed, carrying its output.
    """
    page = get_server_context().journal.search(
        query,
        since=since,
        until=until,
        lines=lines,
        scan=scan,
        boot=boot,
        kernel=kernel,
        app_units=app_units_resolver(runner=get_server_context().runner),
    )
    return JournalSearchOut(
        entries=[JournalEntryOut(**entry.to_dict()) for entry in page.entries],
        matched=page.matched,
        scanned=page.scanned,
        truncated=page.truncated,
        scan_limit_reached=page.scan_limit_reached,
        oldest=page.oldest,
        normalised=page.normalised,
    )


@router.get("/context", response_model=JournalContextOut)
def journal_context(
    session: Annotated[dict, Depends(get_current_session)],
    cursor: Annotated[str, Query(min_length=1, max_length=256)],
    before: Annotated[int, Query(ge=0, le=MAX_CONTEXT)] = 25,
    after: Annotated[int, Query(ge=0, le=MAX_CONTEXT)] = 25,
    unit: Annotated[str | None, Query(max_length=256)] = None,
) -> JournalContextOut:
    """
    Show what was logged around one entry: what led to it and what came of it.

    Args:
        session: The authenticated session.
        cursor: The entry's ``cursor``, from a read or a search.
        before: How many entries before it, at most 200.
        after: How many after it, at most 200.
        unit: Only this unit's entries around it; every unit's when omitted.

    Returns:
        The entries oldest first and the position of the entry itself in them; ``target`` is
        null when the entry is no longer in the journal (it rotated away).

    Raises:
        ValidationError: The cursor, a count or the unit is not valid (400).
        ServerError: journalctl failed, carrying its output.
    """
    page = get_server_context().journal.context(cursor, before=before, after=after, unit=unit)
    return JournalContextOut(
        entries=[JournalEntryOut(**entry.to_dict()) for entry in page.entries],
        target=page.target,
    )


@router.get("/units", response_model=list[JournalUnitOut])
def list_journal_units(
    session: Annotated[dict, Depends(get_current_session)],
) -> list[JournalUnitOut]:
    """
    List the units the journal can be read for, the failed ones first.

    The listing is :meth:`ServiceManager.list_services` with every unit on the
    machine, the one implementation of that.

    Args:
        session: The authenticated session.

    Returns:
        The units, failed first and then by name.
    """
    rows = ServiceManager(runner=get_server_context().runner).list_services(all_services=True)
    units = [
        JournalUnitOut(
            name=str(row["name"]),
            active=str(row.get("active", "")),
            sub=str(row.get("sub", "")),
            failed=row.get("active") == "failed",
        )
        for row in rows
    ]
    return sorted(units, key=lambda unit: (not unit.failed, unit.name))


@router.get("/boots", response_model=list[BootOut])
def list_boots(session: Annotated[dict, Depends(get_current_session)]) -> list[BootOut]:
    """
    List the boots the journal remembers.

    Args:
        session: The authenticated session.

    Returns:
        The boots. One at most when the journal is volatile.
    """
    return [BootOut(**asdict(boot)) for boot in get_server_context().journal.boots()]


__all__ = ["router"]
