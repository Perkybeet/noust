# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The moment a one-time task is asked to run at (item 164).

A person types it (``2026-12-01 14:30``), a script sends it, and it becomes the ``OnCalendar=`` of
a root-owned timer. So it is judged here, at the one place, by an allowlist: a date, a time and
optionally a UTC offset, nothing else, matched whole. It is read in the zone of the server unless
it carries an offset of its own, which is converted, and it has to lie ahead of the clock: a task
that was due a minute ago is an error that says so, and not a timer systemd would run at once or
never.

The result is a moment, not text: what reaches the unit is written from it
(:meth:`~noust.managers.cron_once.OnceMoment.calendar`), so nothing the person typed is ever
copied into a unit file.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from noust.core.exceptions import ValidationError

__all__ = ["MAX_AHEAD", "parse_once_at", "zone_label"]

#: The farthest ahead a one-time task may be. Past two years it is a typo, not a plan.
MAX_AHEAD = timedelta(days=730)

#: A date, a time to the minute or the second, and optionally ``Z`` or an offset. Matched whole,
#: and with ASCII digits only: ``\d`` would take a digit of any script, which ``int`` also reads.
_SHAPE = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})[ T]([0-9]{2}):([0-9]{2})(?::([0-9]{2}))?"
    r"(Z|[+-][0-9]{2}(?::?[0-9]{2})?)?"
)

#: What the message shows of a moment: the minute, which is how it is asked for.
_SHOWN = "%Y-%m-%d %H:%M"


def zone_label(moment: datetime) -> str:
    """
    Name the zone of a moment the way the console does.

    Args:
        moment: An aware datetime.

    Returns:
        ``UTC`` for no offset, ``UTC+01:00`` or ``UTC-05:30`` otherwise.
    """
    offset = moment.utcoffset()
    minutes = int(offset.total_seconds() // 60) if offset is not None else 0
    if minutes == 0:
        return "UTC"
    sign = "-" if minutes < 0 else "+"
    hours, rest = divmod(abs(minutes), 60)
    return f"UTC{sign}{hours:02d}:{rest:02d}"


def _offset(zone: str) -> timezone:
    """
    Read the ``Z`` or ``+HH[:MM]`` a moment ends with.

    Args:
        zone: What :data:`_SHAPE` matched.

    Returns:
        The offset.

    Raises:
        ValueError: The hours or the minutes are out of range.
    """
    if zone == "Z":
        return timezone.utc
    digits = zone[1:].replace(":", "")
    hours, minutes = int(digits[:2]), int(digits[2:4] or 0)
    if hours > 23 or minutes > 59:
        raise ValueError(f"{zone} is not an offset")
    return timezone((-1 if zone[0] == "-" else 1) * timedelta(hours=hours, minutes=minutes))


def parse_once_at(text: str, *, now: datetime) -> datetime:
    """
    Read the moment a one-time task runs at, and check it lies ahead.

    Args:
        text: ``YYYY-MM-DD HH:MM`` (``T`` for the space and seconds are accepted), in the server's
            time, or with ``Z`` or an offset to say another.
        now: The clock, aware, in the server's zone.

    Returns:
        The moment, in the server's zone.

    Raises:
        ValidationError: It is not a date and a time, it has passed, or it is too far ahead.
    """
    match = _SHAPE.fullmatch(text) if isinstance(text, str) else None
    if match is None:
        raise ValidationError(
            f"{text!r} is not a date and a time",
            details="Write it as YYYY-MM-DD HH:MM in this server's time, for example "
            "2026-12-01 14:30.",
            field="once_at",
        )
    year, month, day, hour, minute, second, zone = match.groups()
    try:
        naive = datetime(int(year), int(month), int(day), int(hour), int(minute), int(second or 0))
    except ValueError as exc:
        raise ValidationError(
            f"{text!r} is not a date and a time that exist",
            details=f"{exc}. Write it as YYYY-MM-DD HH:MM in this server's time.",
            field="once_at",
        ) from exc
    try:
        # A naive moment is the system's own clock for that date, daylight saving included.
        at = (naive if zone is None else naive.replace(tzinfo=_offset(zone))).astimezone()
    except (ValueError, OverflowError) as exc:
        raise ValidationError(
            f"{text!r} is not a date and a time that exist",
            details=f"{exc}. Write it as YYYY-MM-DD HH:MM in this server's time.",
            field="once_at",
        ) from exc
    if at <= now:
        raise ValidationError(
            f"{at.strftime(_SHOWN)} has already passed on this server",
            details=f"It is {now.strftime(_SHOWN)} here ({zone_label(now)}). Choose a later moment.",
            field="once_at",
        )
    if at - now > MAX_AHEAD:
        raise ValidationError(
            f"{at.strftime(_SHOWN)} is too far ahead",
            details="A one-time task runs within two years. For something later, schedule it "
            "closer to the date.",
            field="once_at",
        )
    return at
