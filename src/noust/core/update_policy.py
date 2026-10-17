# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The rules of Noust updating itself on its own, which need no machine to be judged.

An update that nobody is watching has to be narrower than one an operator starts. These are
the three limits an operator sets (checklist N-28), and the one that is not theirs to set:

- **A window.** ``03:00-05:00`` in the server's own clock, which may cross midnight. Nothing
  is installed outside it, whatever started the run.
- **Days.** ``daily``, or the weekdays the window opens on (``mon,thu``). A window that
  crosses midnight belongs to the day it opened on.
- **A channel.** Which of the releases the one stable channel publishes the server takes
  by itself: ``security`` (only a release that fixes a vulnerability), ``patch`` (also the
  patch releases of the minor it runs, which by the release policy change no schema and no
  screen), or ``stable`` (also a new minor, which comes with an upgrade guide).
- **Never a new major.** A major version removes what it announced it would remove; that is
  read first by a person, so no channel takes it.

Pure, so ``core/config.py`` can validate the settings with the same functions the manager
judges them with, and every rule is tested without a clock or a server.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

#: What a server takes by itself, narrowest first.
CHANNELS: tuple[str, ...] = ("security", "patch", "stable")

DEFAULT_CHANNEL = "patch"
DEFAULT_WINDOW = "03:00-05:00"
DEFAULT_DAYS = "daily"

#: The days of a week, in the order ``datetime.weekday()`` numbers them.
WEEKDAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

#: How systemd spells them in a calendar expression.
_SYSTEMD_DAYS = {
    "mon": "Mon",
    "tue": "Tue",
    "wed": "Wed",
    "thu": "Thu",
    "fri": "Fri",
    "sat": "Sat",
    "sun": "Sun",
}

_WINDOW = re.compile(r"(?P<a>[01]\d|2[0-3]):(?P<b>[0-5]\d)-(?P<c>[01]\d|2[0-3]):(?P<d>[0-5]\d)")

#: How often the timer looks while the window is open. A run that finds the server busy, or
#: a release that is still being built, is tried again at the next look.
LOOK_EVERY_MINUTES = 10


@dataclass(frozen=True)
class Window:
    """
    The hours an update may start in, on the server's clock.

    Attributes:
        start: When it opens.
        end: When it closes (exclusive). Earlier than ``start`` for a window that crosses midnight.
    """

    start: time
    end: time

    def __str__(self) -> str:
        return f"{self.start:%H:%M}-{self.end:%H:%M}"

    @property
    def crosses_midnight(self) -> bool:
        """Whether the window is still open after midnight."""
        return self.end < self.start

    def opened_on(self, moment: datetime) -> date | None:
        """
        Say whether the window is open at a moment, and on which day it opened.

        Args:
            moment: A time on the server's clock.

        Returns:
            The date the window opened on, or None when it is closed.
        """
        at = moment.time().replace(second=0, microsecond=0)
        if not self.crosses_midnight:
            return moment.date() if self.start <= at < self.end else None
        if at >= self.start:
            return moment.date()
        if at < self.end:
            return moment.date() - timedelta(days=1)
        return None

    def hours(self) -> tuple[int, ...]:
        """
        Returns:
            The clock hours the window touches, in the order it opens through them. A window
            that ends exactly on the hour does not touch the hour it ends on.
        """
        last = self.end.hour if self.end.minute else (self.end.hour - 1) % 24
        count = (last - self.start.hour) % 24 + 1
        return tuple((self.start.hour + offset) % 24 for offset in range(count))


def parse_window(text: object) -> Window:
    """
    Read a window.

    Args:
        text: ``HH:MM-HH:MM``.

    Returns:
        The window.

    Raises:
        ValueError: It is not that shape, or opens and closes at the same minute.
    """
    match = _WINDOW.fullmatch(text.strip()) if isinstance(text, str) else None
    if match is None:
        raise ValueError(
            f"{text!r} is not a window: write it as 03:00-05:00, in the server's own time"
        )
    start = time(int(match["a"]), int(match["b"]))
    end = time(int(match["c"]), int(match["d"]))
    if start == end:
        raise ValueError("A window that opens and closes at the same minute never opens")
    return Window(start, end)


def parse_days(text: object) -> tuple[str, ...]:
    """
    Read the days a window opens on.

    Args:
        text: ``daily``, or weekdays separated by commas (``mon,thu``).

    Returns:
        The days, in the order of the week.

    Raises:
        ValueError: A word is not a weekday, or there is none.
    """
    if not isinstance(text, str):
        raise ValueError(f"{text!r} is not a list of days: write daily, or mon,thu")
    words = [word.strip().lower() for word in text.split(",") if word.strip()]
    if words == ["daily"]:
        return WEEKDAYS
    unknown = [word for word in words if word not in WEEKDAYS]
    if not words or unknown:
        raise ValueError(
            f"{(unknown or [text])[0]!r} is not a day: write daily, or weekdays from {', '.join(WEEKDAYS)}"
        )
    return tuple(day for day in WEEKDAYS if day in words)


def days_text(days: tuple[str, ...]) -> str:
    """
    Returns:
        The days as they are written in the configuration: ``daily`` for all seven.
    """
    return DEFAULT_DAYS if days == WEEKDAYS else ",".join(days)


def parse_channel(text: object) -> str:
    """
    Read a channel.

    Args:
        text: One of :data:`CHANNELS`.

    Returns:
        The channel.

    Raises:
        ValueError: It is not one of them.
    """
    if not isinstance(text, str) or text.strip().lower() not in CHANNELS:
        raise ValueError(f"{text!r} is not a channel: choose {', '.join(CHANNELS)}")
    return text.strip().lower()


def window_day(window: Window, days: tuple[str, ...], moment: datetime) -> date | None:
    """
    Say whether an update may start at a moment.

    Args:
        window: The hours.
        days: The days the window opens on.
        moment: A time on the server's clock.

    Returns:
        The date the open window started on, or None when it is closed or not on a chosen day.
    """
    opened = window.opened_on(moment)
    if opened is None or WEEKDAYS[opened.weekday()] not in days:
        return None
    return opened


def calendars(window: Window, days: tuple[str, ...]) -> list[str]:
    """
    The ``OnCalendar`` lines that look while a window is open.

    Hours and minutes are one expression (``03,04:00/10:00``), so a window that does not start
    on the hour is looked at from the hour it opens in; the run itself judges the exact minute
    (:func:`window_day`). A window that crosses midnight is two expressions per day: the hours
    before midnight on the day it opens, the hours after it on the next.

    Args:
        window: The hours.
        days: The days the window opens on.

    Returns:
        One expression per part, each valid for ``systemd.time``.
    """
    before: list[int] = []
    after: list[int] = []
    for hour in window.hours():
        # Hours are listed in the order the window passes through them; the day changes once.
        (after if before and hour < before[-1] else before).append(hour)

    def expression(weekdays: tuple[str, ...], hours: list[int]) -> str:
        names = (
            "" if weekdays == WEEKDAYS else ",".join(_SYSTEMD_DAYS[day] for day in weekdays) + " "
        )
        clock = ",".join(f"{hour:02d}" for hour in hours)
        return f"{names}*-*-* {clock}:00/{LOOK_EVERY_MINUTES}:00"

    lines = [expression(days, before)]
    if after:
        following = tuple(WEEKDAYS[(WEEKDAYS.index(day) + 1) % 7] for day in days)
        lines.append(expression(tuple(day for day in WEEKDAYS if day in following), after))
    return lines


def version_parts(version: str) -> tuple[int, int, int] | None:
    """
    Read ``MAJOR.MINOR.PATCH`` out of a version string.

    Args:
        version: ``3.4.1`` or a packaged ``3.4.1-1``.

    Returns:
        The three numbers, or None when it is not a version Noust numbers.
    """
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", version.strip().lstrip("v"))
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def allows(channel: str, running: str, target: str, *, security: bool) -> tuple[bool, str]:
    """
    Say whether a channel takes a release by itself.

    Args:
        channel: One of :data:`CHANNELS`.
        running: The version running.
        target: The version the update would install.
        security: Whether ``target`` includes the fix for a vulnerability (every release up to
            the one that fixes it is worth taking for that).

    Returns:
        Whether it is taken, and a sentence that says why or why not.
    """
    now, then = version_parts(running), version_parts(target)
    if now is None or then is None:
        return False, f"{target} is not a version this server can compare with {running}"
    if then <= now:
        return False, f"{target} is not newer than {running}"
    if then[0] > now[0]:
        return (
            False,
            f"{target} is a new major version: read its upgrade guide and update it yourself",
        )
    if channel == "security":
        if security:
            return True, f"{target} fixes a vulnerability"
        return (
            False,
            f"{target} fixes no vulnerability and this server takes security releases only",
        )
    if channel == "patch":
        if then[1] == now[1]:
            return True, f"{target} is a patch release of {now[0]}.{now[1]}"
        if security:
            return True, f"{target} fixes a vulnerability"
        return (
            False,
            f"{target} is a new minor version: this server takes patch releases, so update it yourself after reading its upgrade guide",
        )
    if then[1] > now[1]:
        return True, f"{target} is a new minor version, which this server takes by itself"
    return True, f"{target} is a patch release of {now[0]}.{now[1]}"
