# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/server/power``: reboot and shut down, now or later.

Not jobs. A job lives in the process a reboot kills, so it could never report
its own end; what these endpoints keep is a row (who asked, from which boot) and
what systemd keeps is the schedule, which survives the console and can be
cancelled. When the machine is back the console says so
(:mod:`noust.web.api.server.lifecycle`).

A reboot is never triggered by an update finishing. The operator schedules it,
with the checks of :meth:`~noust.managers.server.power.PowerManager.checks` in
front of them: a refused request lists what warned, and is repeated with
``force`` once it has been read.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from noust.core.exceptions import ValidationError
from noust.managers.server.power import resolve_delay
from noust.web.api.auth import get_current_session
from noust.web.api.deps import require_elevated
from noust.web.api.server.common import ServerRoute, audit_event, get_server_context
from noust.web.api.server.models import (
    CancelOut,
    CheckOut,
    PowerOut,
    ScheduledPowerOut,
    ScheduleRequest,
    ShutdownRequest,
)
from noust.web.auth import actor_label

router = APIRouter(route_class=ServerRoute)

#: An API request always leaves at least this long: the response has to reach the
#: operator, and there has to be time to cancel. ``noust server reboot --now`` is
#: the command that skips it.
MIN_API_DELAY_MINUTES = 1


def _minutes(body: ScheduleRequest) -> int:
    """
    Work out the delay of a request.

    Args:
        body: The request.

    Returns:
        Minutes from now, at least :data:`MIN_API_DELAY_MINUTES`.

    Raises:
        ValidationError: Both or neither of ``in_minutes`` and ``at`` were
            given as a moment that is not valid.
    """
    at: datetime | None = None
    if body.at is not None:
        try:
            at = datetime.fromisoformat(body.at)
        except ValueError:
            raise ValidationError(
                f"Not a date and time: {body.at!r}",
                "Use ISO 8601, such as 2026-09-30T04:00:00+02:00.",
                field="at",
            ) from None
    if body.in_minutes is None and at is None:
        return MIN_API_DELAY_MINUTES
    return max(MIN_API_DELAY_MINUTES, resolve_delay(in_minutes=body.in_minutes, at=at))


@router.get("", response_model=PowerOut)
def get_power(session: Annotated[dict, Depends(get_current_session)]) -> PowerOut:
    """
    Say what is scheduled and what a reboot would break.

    Runs the checks, which ask systemd about the console and the applications, so
    it is meant for the moment somebody is about to reboot, not for polling.

    Args:
        session: The authenticated session.

    Returns:
        The schedule, the boot's identity and the checks, each ``ok`` or ``warn``.
    """
    power = get_server_context().power
    status = power.status()
    return PowerOut(
        scheduled=ScheduledPowerOut(**status.scheduled.to_dict()) if status.scheduled else None,
        boot_id=status.boot_id,
        uptime_seconds=status.uptime_seconds,
        mode=status.mode,
        due_at=status.due_at,
        checks=[CheckOut(**asdict(check)) for check in power.checks()],
    )


@router.post("/reboot", response_model=ScheduledPowerOut)
def schedule_reboot(
    body: ScheduleRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> ScheduledPowerOut:
    """
    Schedule a reboot.

    Args:
        body: When, and whether to go ahead although a check warned.
        session: The elevated session.

    Returns:
        The schedule. In one minute when the request gave no time.

    Raises:
        ValidationError: The time is in the past, more than a week away, or not a
            time (400).
        PreflightError: A check warned and ``force`` was not given (409, with the
            warnings in ``blockers``).
        ServerError: ``shutdown`` refused, carrying its output.
    """
    minutes = _minutes(body)
    actor = actor_label(session)
    scheduled = get_server_context().power.schedule(
        "reboot", minutes=minutes, actor=actor, message=body.message, force=body.force
    )
    audit_event(
        "server.reboot",
        target="power",
        action="reboot",
        minutes=minutes,
        due=scheduled.scheduled_for,
        forced=body.force,
    )
    return ScheduledPowerOut(**scheduled.to_dict())


@router.post("/shutdown", response_model=ScheduledPowerOut)
def schedule_shutdown(
    body: ShutdownRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> ScheduledPowerOut:
    """
    Schedule a shutdown.

    A powered-off VPS cannot be started from Noust, only from the provider's
    panel, so the host name has to be typed. A central needs the node's explicit
    permission to change the host itself before it can call this.

    Args:
        body: When, the host name typed to confirm, and whether to go ahead
            although a check warned.
        session: The elevated session.

    Returns:
        The schedule.

    Raises:
        ValidationError: The host name does not match, or the time is not valid (400).
        PreflightError: A check warned and ``force`` was not given (409).
    """
    ctx = get_server_context()
    names = {ctx.identity.hostname().hostname, ctx.identity.hostname().static} - {""}
    if body.confirm_hostname.strip() not in names:
        raise ValidationError(
            "The host name does not match",
            f"Type the server's host name ({', '.join(sorted(names))}) to confirm. A powered-off "
            "server can only be started from your provider's panel.",
            field="confirm_hostname",
        )
    minutes = _minutes(body)
    scheduled = ctx.power.schedule(
        "poweroff",
        minutes=minutes,
        actor=actor_label(session),
        message=body.message,
        force=body.force,
    )
    audit_event(
        "server.reboot",
        target="power",
        action="shutdown",
        minutes=minutes,
        due=scheduled.scheduled_for,
        forced=body.force,
    )
    return ScheduledPowerOut(**scheduled.to_dict())


@router.delete("/scheduled", response_model=CancelOut)
def cancel_scheduled(session: Annotated[dict, Depends(get_current_session)]) -> CancelOut:
    """
    Cancel the pending reboot or shutdown.

    Not elevated: cancelling is the safe direction, and the operator who sees a
    reboot they did not want should not have to confirm anything first.

    Args:
        session: The authenticated session.

    Returns:
        Whether there was something to cancel.

    Raises:
        ServerError: ``shutdown -c`` failed although something was scheduled.
    """
    cancelled = get_server_context().power.cancel()
    audit_event("server.reboot", target="power", action="cancel", cancelled=cancelled)
    return CancelOut(cancelled=cancelled)


__all__ = ["router"]
