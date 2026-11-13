# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
When a schedule runs, for the calendar expressions Noust writes after ``OnCalendar=``.

Where systemd keeps a schedule's timer, systemd says when it runs. Where it does not (the central of a
NAS, which runs the schedules itself) something has to read the same expression, and a schedule written
for a server must mean the same thing there. This reads the part of systemd's calendar language that
Noust accepts (``managers.backup_scheduler.validate_calendar`` allows letters, digits and ``*-/,:. ``):

- **weekdays**: a name (``Mon`` or ``Monday``, either case), a list (``Mon,Wed``) or a range
  (``Mon..Fri``);
- **a date**: ``year-month-day``, or ``month-day`` with any year, each field ``*``, a number, a list
  (``1,15``), a range (``20..22``) or a step (``*/2``, ``1/3``, ``1..10/3``);
- **a time**: ``hour:minute`` or ``hour:minute:second``, each field the same way;
- the names systemd gives them (``hourly``, ``daily``, ``weekly``, ``monthly``, ``quarterly``,
  ``semiannually``, ``yearly``, ``minutely``), unless the caller gives one of them another meaning (Noust's
  ``daily`` is two in the morning).

An omitted date is every day and an omitted time is midnight, as in systemd; all of it is local time. What
is not understood (a time zone suffix, ``~``, two times) is refused by naming the part, and is never run
at a guess: a backup that runs at the wrong hour is worse than one that says it cannot.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from noust.core.exceptions import NoustError

__all__ = ["SCHEDULE_ALIASES", "Calendar", "CalendarError"]

#: The names Noust gives a schedule, which mean what Noust writes for them: its own, not systemd's (a backup
#: at midnight is the hour everything else runs at, so these say 02:00). Wherever a schedule is read, from
#: the row of a backup to a setting of the central, these are the words it may be given.
SCHEDULE_ALIASES: dict[str, str] = {
    "hourly": "*-*-* *:00:00",
    "daily": "*-*-* 02:00:00",
    "weekly": "Mon *-*-* 02:00:00",
    "monthly": "*-*-01 02:00:00",
}

#: What systemd calls a calendar.
SYSTEMD_NAMES: dict[str, str] = {
    "minutely": "*-*-* *:*:00",
    "hourly": "*-*-* *:00:00",
    "daily": "*-*-* 00:00:00",
    "weekly": "Mon *-*-* 00:00:00",
    "monthly": "*-*-01 00:00:00",
    "quarterly": "*-01,04,07,10-01 00:00:00",
    "semiannually": "*-01,07-01 00:00:00",
    "yearly": "*-01-01 00:00:00",
    "annually": "*-01-01 00:00:00",
}

#: Weekdays by their first three letters, Monday first as systemd and ISO count them.
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

#: How far ahead a schedule is looked for before it is said to have nothing left.
_YEARS_AHEAD = 8

_FIELD = re.compile(r"[0-9*/,.]+")


class CalendarError(NoustError):
    """A calendar expression Noust cannot read."""


@dataclass(frozen=True)
class _Field:
    """
    The values one position of an expression allows.

    Attributes:
        values: The allowed numbers, ascending; empty for any (a year only).
    """

    values: tuple[int, ...]


def _fail(text: str, part: str, why: str) -> CalendarError:
    """
    Args:
        text: The whole expression.
        part: The part that is not understood.
        why: What is wrong with it.

    Returns:
        The error, naming the part.
    """
    return CalendarError(
        f"The calendar expression {text!r} has a {part} that is not understood: {why}",
        details=(
            "Noust reads weekdays (Mon, Mon,Wed, Mon..Fri), a date (*-*-01, 2027-01-15, *-*-1,15) and a "
            "time (02:00, *-*-* */4:15) with *, numbers, lists, ranges and steps; nothing else, so "
            "that a schedule never runs at a time nobody meant."
        ),
    )


def _field(text: str, whole: str, part: str, low: int, high: int) -> tuple[int, ...]:
    """
    Read one position of an expression.

    Args:
        text: The position as written (``*/4``, ``1,15``, ``20..22``, ``00/6``).
        whole: The whole expression, for the error.
        part: What it is called there (``hour``).
        low: The smallest value it may have.
        high: The largest.

    Returns:
        The values it allows, ascending.

    Raises:
        CalendarError: When it is not a number, list, range or step inside its bounds.
    """
    if not text or _FIELD.fullmatch(text) is None:
        raise _fail(whole, part, f"{text!r} is not a number, a list, a range or a step")
    found: set[int] = set()
    for item in text.split(","):
        body, slash, step_text = item.partition("/")
        if slash and (not step_text.isdigit() or int(step_text) < 1):
            raise _fail(whole, part, f"the step in {item!r} must be a whole number from 1")
        step = int(step_text) if slash else 1
        if body == "*":
            first, last = low, high
        else:
            start_text, dots, end_text = body.partition("..")
            if not start_text.isdigit() or (dots and not end_text.isdigit()):
                raise _fail(whole, part, f"{item!r} is not a number or a range")
            first = int(start_text)
            last = int(end_text) if dots else (high if slash else first)
            if first > last:
                raise _fail(whole, part, f"the range {item!r} runs backwards")
        if first < low or last > high:
            raise _fail(whole, part, f"{item!r} is outside {low} to {high}")
        found.update(range(first, last + 1, step))
    return tuple(sorted(found))


def _weekdays(text: str, whole: str) -> tuple[int, ...]:
    """
    Args:
        text: The weekdays as written (``Mon,Wed``, ``Mon..Fri``).
        whole: The whole expression, for the error.

    Returns:
        The weekdays allowed, Monday as 0.

    Raises:
        CalendarError: When a name is not a weekday.
    """

    def day(name: str) -> int:
        short = name.lower()[:3]
        if short not in _WEEKDAYS or not re.fullmatch(r"[A-Za-z]+", name):
            raise _fail(whole, "weekday", f"{name!r} is not a day of the week")
        return _WEEKDAYS.index(short)

    found: set[int] = set()
    for item in text.split(","):
        first_name, dots, last_name = item.partition("..")
        first = day(first_name)
        last = day(last_name) if dots else first
        if first <= last:
            found.update(range(first, last + 1))
        else:
            found.update(range(first, 7))
            found.update(range(0, last + 1))
    return tuple(sorted(found))


@dataclass(frozen=True)
class Calendar:
    """
    A calendar expression, read.

    Attributes:
        text: The expression as written.
        weekdays: The weekdays allowed (Monday is 0); empty for any.
        years: The years allowed; empty for any.
        months: The months allowed.
        days: The days of the month allowed.
        hours: The hours allowed.
        minutes: The minutes allowed.
        seconds: The seconds allowed.
    """

    text: str
    weekdays: tuple[int, ...]
    years: tuple[int, ...]
    months: tuple[int, ...]
    days: tuple[int, ...]
    hours: tuple[int, ...]
    minutes: tuple[int, ...]
    seconds: tuple[int, ...]
    _times: list[tuple[int, int, int]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Work out once the times of a day that are allowed, which every search reads."""
        object.__setattr__(
            self,
            "_times",
            [(h, m, s) for h in self.hours for m in self.minutes for s in self.seconds],
        )

    @classmethod
    def parse(cls, text: str, aliases: Mapping[str, str] | None = None) -> Calendar:
        """
        Read a calendar expression.

        Args:
            text: The expression, or a name (see the module's documentation).
            aliases: Names with a meaning of the caller's own, which win over systemd's.

        Returns:
            The calendar.

        Raises:
            CalendarError: The expression is empty or has a part that is not understood.
        """
        written = text.strip()
        if not written:
            raise _fail(text, "schedule", "it is empty")
        expanded = (aliases or {}).get(written) or SYSTEMD_NAMES.get(written.lower(), written)
        parts = expanded.split()
        weekdays: tuple[int, ...] = ()
        if parts and re.fullmatch(r"[A-Za-z][A-Za-z,.]*", parts[0]):
            weekdays = _weekdays(parts.pop(0), text)
        date_part = time_part = None
        for part in parts:
            if ":" in part:
                if time_part is not None:
                    raise _fail(text, "time", f"there is a second time, {part!r}")
                time_part = part
            elif "-" in part and date_part is None:
                date_part = part
            else:
                raise _fail(text, "schedule", f"{part!r} is not a date or a time")
        years, months, days = cls._date(date_part or "*-*-*", text)
        hours, minutes, seconds = cls._time(time_part or "00:00:00", text)
        return cls(written, weekdays, years, months, days, hours, minutes, seconds)

    @staticmethod
    def _date(part: str, whole: str) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
        """
        Args:
            part: ``year-month-day`` or ``month-day``.
            whole: The whole expression, for the error.

        Returns:
            The years (empty for any), months and days allowed.

        Raises:
            CalendarError: When it is not a date.
        """
        pieces = part.split("-")
        if len(pieces) == 2:
            pieces = ["*", *pieces]
        if len(pieces) != 3:
            raise _fail(whole, "date", f"{part!r} is not year-month-day")
        year = () if pieces[0] == "*" else _field(pieces[0], whole, "year", 1970, 2200)
        return (
            year,
            _field(pieces[1], whole, "month", 1, 12),
            _field(pieces[2], whole, "day", 1, 31),
        )

    @staticmethod
    def _time(part: str, whole: str) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
        """
        Args:
            part: ``hour:minute`` or ``hour:minute:second``.
            whole: The whole expression, for the error.

        Returns:
            The hours, minutes and seconds allowed.

        Raises:
            CalendarError: When it is not a time.
        """
        pieces = part.split(":")
        if len(pieces) == 2:
            pieces.append("0")
        if len(pieces) != 3:
            raise _fail(whole, "time", f"{part!r} is not hour:minute:second")
        return (
            _field(pieces[0], whole, "hour", 0, 23),
            _field(pieces[1], whole, "minute", 0, 59),
            _field(pieces[2], whole, "second", 0, 59),
        )

    def _on(self, day: date) -> bool:
        """
        Args:
            day: A date.

        Returns:
            Whether the expression allows anything to run that day.
        """
        return (
            (not self.years or day.year in self.years)
            and day.month in self.months
            and day.day in self.days
            and (not self.weekdays or day.weekday() in self.weekdays)
        )

    def next_after(self, after: datetime) -> datetime | None:
        """
        Find the next time the schedule runs, strictly after a moment.

        Args:
            after: The moment, in local time.

        Returns:
            The time, or None when the schedule has nothing left to run (a date in the past) within
            eight years.
        """
        times = self._times
        moment = after.replace(microsecond=0)
        today = moment.date()
        for offset in range(0, 366 * _YEARS_AHEAD):
            day = today + timedelta(days=offset)
            if not self._on(day):
                continue
            if offset == 0:
                index = bisect_right(times, (moment.hour, moment.minute, moment.second))
                if index < len(times):
                    hour, minute, second = times[index]
                    return datetime(day.year, day.month, day.day, hour, minute, second)
                continue
            hour, minute, second = times[0]
            return datetime(day.year, day.month, day.day, hour, minute, second)
        return None

    def between(self, start: datetime, end: datetime, *, limit: int = 1000) -> list[datetime]:
        """
        List the times the schedule runs after a moment and up to another.

        Args:
            start: Exclusive.
            end: Inclusive.
            limit: The most to list: a central that was off for a month does not owe a month of runs.

        Returns:
            The times, ascending, at most ``limit``.
        """
        found: list[datetime] = []
        moment = start
        while len(found) < limit:
            following = self.next_after(moment)
            if following is None or following > end:
                break
            found.append(following)
            moment = following
        return found
