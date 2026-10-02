# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/server/time``, ``/clock/timezones``, ``/identity`` and ``/processes``: the system tab.

The clock and the host name are changed in place and answered with what the tool
said, verbatim; they take a moment and there is nothing to follow, so they are not
jobs. The process list is observation only: there is no way to signal a process
from here, on purpose.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from noust.core.exceptions import ValidationError
from noust.managers.server.processes import (
    SORT_KEYS,
    group_by_unit,
    list_processes,
)
from noust.web.api.auth import get_current_session
from noust.web.api.deps import require_elevated
from noust.web.api.server.common import ServerRoute, audit_event, get_server_context
from noust.web.api.server.models import (
    HostnameRequest,
    IdentityOut,
    ProcessesOut,
    ProcessOut,
    StepsOut,
    TimeChangeOut,
    TimeChangeRequest,
    TimeOut,
    TimezoneOut,
    TimezonesOut,
    UnitProcessesOut,
)
from noust.web.auth import sees_command_lines

router = APIRouter(route_class=ServerRoute)

#: Most processes one listing returns.
MAX_PROCESSES = 500


@router.get("/time", response_model=TimeOut)
def get_time(session: Annotated[dict, Depends(get_current_session)]) -> TimeOut:
    """
    Read the clock.

    Args:
        session: The authenticated session.

    Returns:
        The time zone, whether the clock is synchronised and by what, and, when
        chrony is running, how far off it is.

    Raises:
        ServerError: timedatectl cannot be run, carrying its output.
    """
    return TimeOut(**get_server_context().clock.status().to_dict())


@router.get("/clock/timezones", response_model=TimezonesOut)
def list_timezones(session: Annotated[dict, Depends(get_current_session)]) -> TimezonesOut:
    """
    List the time zones the server can be set to.

    The managed server computes it, not the browser: the names are the ones its
    tz database has (so the change never refuses one), and the offsets are the ones
    in force now, daylight saving included. On a fleet the node answers.

    Args:
        session: The authenticated session.

    Returns:
        Every zone with its region, city, offset now and abbreviation, ``Etc/UTC``
        and ``UTC`` first and the rest by offset and name.
    """
    moment = datetime.now(timezone.utc)
    zones = get_server_context().clock.timezones(now=moment)
    return TimezonesOut(
        generated_at=moment.isoformat(),
        timezones=[TimezoneOut(**zone.to_dict()) for zone in zones],
    )


@router.put("/time", response_model=TimeChangeOut)
def change_time(
    body: TimeChangeRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> TimeChangeOut:
    """
    Change the time zone, the synchronisation, or both.

    Changing the zone moves every timer Noust wrote for cron jobs and backups,
    which fire at local times; they are listed in ``moved_timers``.

    Args:
        body: What to change; at least one of ``timezone`` and ``ntp``.
        session: The elevated session.

    Returns:
        The clock afterwards, the zone it had, the timers that moved and the
        tools' own output.

    Raises:
        ValidationError: Nothing to change, or an unknown zone (400).
        ServerError: timedatectl refused; without a time daemon the message says
            to allow installing chrony.
    """
    if body.timezone is None and body.ntp is None:
        raise ValidationError("Nothing to change", "Give a timezone, ntp, or both.")
    clock = get_server_context().clock
    output: list[str] = []
    previous: str | None = None
    moved: list[str] = []
    if body.timezone is not None:
        change = clock.set_timezone(body.timezone)
        previous, moved = change.previous, change.moved_timers
        output.append(change.output)
        audit_event(
            "server.time",
            target="time",
            action="timezone",
            previous=change.previous,
            timezone=change.timezone,
            moved_timers=len(change.moved_timers),
        )
    if body.ntp is not None:
        output.append(clock.set_ntp(body.ntp, install=body.install_ntp))
        audit_event(
            "server.time", target="time", action="ntp", enabled=body.ntp, install=body.install_ntp
        )
    get_server_context().cache.invalidate("time")
    return TimeChangeOut(
        time=TimeOut(**clock.status().to_dict()),
        previous_timezone=previous,
        moved_timers=moved,
        output="\n".join(line for line in output if line),
    )


@router.get("/identity", response_model=IdentityOut)
def get_identity(session: Annotated[dict, Depends(get_current_session)]) -> IdentityOut:
    """
    Describe what the machine is called and what it runs.

    Args:
        session: The authenticated session.

    Returns:
        The host name and whether cloud-init would rename it back, the operating
        system and where it is in its support (from a table shipped with Noust,
        no network), the kernel, the uptime and the load.
    """
    data = get_server_context().identity.identity().to_dict()
    return IdentityOut(**data)


@router.put("/identity/hostname", response_model=StepsOut)
def change_hostname(
    body: HostnameRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> StepsOut:
    """
    Rename the machine.

    Args:
        body: The new name, and whether to tell cloud-init to keep it.
        session: The elevated session.

    Returns:
        One sentence per thing done, including a warning when cloud-init would
        set the name back on the next boot.

    Raises:
        ValidationError: The name is not a valid host name (400).
        ServerError: hostnamectl refused, carrying its output.
    """
    steps = get_server_context().identity.set_hostname(
        body.hostname, keep_against_cloud_init=body.keep_against_cloud_init
    )
    get_server_context().cache.invalidate("identity")
    audit_event("server.hostname", target="hostname", name=body.hostname)
    return StepsOut(steps=steps)


@router.get("/processes", response_model=ProcessesOut)
def get_processes(
    session: Annotated[dict, Depends(get_current_session)],
    sort_by: Annotated[str, Query()] = "cpu",
    limit: Annotated[int, Query(ge=1, le=MAX_PROCESSES)] = 25,
    group: Annotated[str | None, Query()] = None,
) -> ProcessesOut:
    """
    List the processes, or add them up by the unit they belong to.

    Observation only: nothing here signals a process. Command lines are left out
    unless the credential may read them, because argv carries other people's
    secrets.

    Args:
        session: The authenticated session.
        sort_by: ``cpu``, ``memory``, ``pid`` or ``name``.
        limit: How many processes to return.
        group: ``unit`` to add the processes up by unit instead.

    Returns:
        The processes and, with ``group=unit``, the units, biggest memory user first.

    Raises:
        ValidationError: ``group`` is not ``unit`` (400).
    """
    if group not in (None, "unit"):
        raise ValidationError(f"Cannot group by {group!r}", "Use group=unit.", field="group")
    if sort_by not in SORT_KEYS:
        sort_by = "cpu"
    rows, total = list_processes(
        sort_by=sort_by,
        limit=MAX_PROCESSES * 100 if group == "unit" else limit,
        show_commands=sees_command_lines(session),
    )
    if group == "unit":
        units = [UnitProcessesOut(**asdict(unit)) for unit in group_by_unit(rows)][:limit]
        return ProcessesOut(processes=[], units=units, total=total)
    return ProcessesOut(
        processes=[ProcessOut(**row.to_dict()) for row in rows], units=[], total=total
    )


__all__ = ["router"]
