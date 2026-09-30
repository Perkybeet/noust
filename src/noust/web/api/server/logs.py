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

from noust.managers.server.journal import MAX_LINES
from noust.managers.service_manager import ServiceManager
from noust.web.api.auth import get_current_session
from noust.web.api.server.common import ServerRoute, get_server_context
from noust.web.api.server.models import BootOut, JournalEntryOut, JournalOut, JournalUnitOut

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
