# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tasks that run once and retire themselves: the moment, and what a run left (item 164).

A one-time task is a task like any other (:mod:`noust.managers.cron_manager` writes and drives its
unit pair) whose timer names one date, with ``Persistent=true``, so a server that was off at that
moment runs it when it comes back. Its service ends with ``ExecStopPost=-+... cron settle NAME``:
systemd runs that as root once the command has ended, however it ended, and it records the result
and the output where the units will no longer be (:mod:`noust.core.cron_once_history`) and removes
the pair.

This module holds what is not about systemd: the moment, in the server's zone and written to the
second as a calendar expression; reading it back from the unit; whether a run came late; and how
a run is announced. The functions are pure, so each rule is tested without a machine.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING, Any

from noust.core.cron_once_history import KEEP_DAYS, OnceRun
from noust.core.notifications.composers_ops import compose_cron
from noust.core.timeutil import local_now
from noust.managers import cron_ledger
from noust.validators.once_at import parse_once_at, zone_label

if TYPE_CHECKING:
    from collections.abc import Callable

    from noust.core.notifications.context import NotificationContext
    from noust.core.notifications.model import Notification

__all__ = [
    "CALENDAR_FORMAT",
    "ONCE_COMMENT",
    "announcement",
    "calendar_of",
    "history_document",
    "moment_of_calendar",
    "moment_of_text",
    "started_late",
    "zone_label",
]

#: How the service unit records that a task is a one-time one and when it runs: the calendar
#: expression, which is also what the timer holds. This module's own writing, read back by it.
ONCE_COMMENT = "# Once: "

#: A calendar expression written to the second, the one shape the timer is given.
CALENDAR_FORMAT = "%Y-%m-%d %H:%M:%S"


def moment_of_text(text: str) -> datetime:
    """
    Read the moment a person asked a task to run at.

    Args:
        text: ``YYYY-MM-DD HH:MM`` in the server's time, or with an offset.

    Returns:
        The moment, aware, in the server's zone.

    Raises:
        ValidationError: It is not a moment, has passed, or is too far ahead
            (:func:`noust.validators.once_at.parse_once_at`).
    """
    return parse_once_at(text, now=local_now())


def calendar_of(moment: datetime) -> str:
    """
    Write a moment as the ``OnCalendar=`` of its timer.

    Args:
        moment: An aware datetime in the server's zone.

    Returns:
        ``YYYY-MM-DD HH:MM:SS``, which systemd reads in the server's zone. Built from the moment,
        never from what was typed.
    """
    return moment.strftime(CALENDAR_FORMAT)


def moment_of_calendar(calendar: str) -> datetime | None:
    """
    Read back the moment a unit this module wrote carries.

    Args:
        calendar: What :func:`calendar_of` wrote.

    Returns:
        The moment in the server's zone (the system's own rules for that date), or None for text
        that is not one: a unit somebody edited. Whether it is still ahead is not asked.
    """
    try:
        return datetime.strptime(calendar.strip(), CALENDAR_FORMAT).astimezone()
    except ValueError:
        return None


def started_late(run_at: datetime | None, started: float | None) -> bool:
    """
    Say whether a run began well after the moment it was due.

    Args:
        run_at: When the task was asked to run.
        started: When the run began, epoch seconds, or None when nothing says.

    Returns:
        True when it began more than the ledger's grace after ``run_at``: the server was off at
        that moment, and the timer ran it on the way back up (``Persistent=true``). False when it
        is not known.
    """
    if run_at is None or started is None:
        return False
    return started - run_at.timestamp() > cron_ledger.GRACE_SECONDS


def history_document(runs: list[OnceRun]) -> dict[str, Any]:
    """
    Say the one-time tasks that ran, as ``noust cron history --json`` and the API both do.

    Args:
        runs: The recorded runs, newest first.

    Returns:
        ``items`` (each run, with whether it ``ok``), ``total``, the ``days`` the record is kept
        and the server's ``zone``, which is the one ``run_at`` of each item is written in.
    """
    return {
        "items": [{**asdict(run), "ok": run.ok} for run in runs],
        "total": len(runs),
        "days": KEEP_DAYS,
        "zone": zone_label(local_now()),
    }


def announcement(run: OnceRun) -> Callable[[NotificationContext], Notification]:
    """
    Compose the notification for a one-time task that ran.

    Args:
        run: How it ended.

    Returns:
        The composer for the notification worker or the monitor's publisher: ``cron_failed`` with
        the last lines it printed, or ``cron_success``, pointing at ``noust cron history`` since
        its units are gone.
    """
    return partial(
        compose_cron,
        run.name,
        run.ok,
        exit_code=run.exit_code,
        result=run.result,
        ran_at=datetime.fromisoformat(run.finished_at) if run.finished_at else None,
        output=run.output or None,
        domain=run.app_domain or None,
        once=True,
    )
