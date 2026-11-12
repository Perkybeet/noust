# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
When a schedule next runs, for the calendar expressions Noust writes after ``OnCalendar=``.

Where systemd keeps the timers, systemd says when each runs. Where it does not (the central of a NAS, which
runs the schedules itself), something has to read the same expressions, and a schedule a person wrote for a
server must mean the same thing there. What is read is the part of systemd's calendar language Noust
accepts and its aliases: weekdays (a name, a list, a range), a date (year, month, day, each ``*``, a number,
a list, a range or a step) and a time (hour, minute, second likewise), in local time. What is not understood
is refused by name and never run at a guess.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from noust.core.calendar_expr import Calendar, CalendarError

# 2026-11-12 is a Thursday.
NOW = datetime(2026, 11, 12, 10, 30, 15)


def next_of(text: str, after: datetime = NOW) -> datetime | None:
    return Calendar.parse(text).next_after(after)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The names systemd has for a calendar, when nobody gave them another meaning.
        ("hourly", datetime(2026, 11, 12, 11, 0, 0)),
        ("daily", datetime(2026, 11, 13, 0, 0, 0)),
        ("weekly", datetime(2026, 11, 16, 0, 0, 0)),
        ("monthly", datetime(2026, 12, 1, 0, 0, 0)),
        ("yearly", datetime(2027, 1, 1, 0, 0, 0)),
        ("minutely", datetime(2026, 11, 12, 10, 31, 0)),
        ("quarterly", datetime(2027, 1, 1, 0, 0, 0)),
        # What Noust itself writes (managers.backup_scheduler.SCHEDULE_ALIASES and its constants).
        ("*-*-* 02:00:00", datetime(2026, 11, 13, 2, 0, 0)),
        ("*-*-* *:00:00", datetime(2026, 11, 12, 11, 0, 0)),
        ("Mon *-*-* 02:00:00", datetime(2026, 11, 16, 2, 0, 0)),
        ("*-*-01 02:00:00", datetime(2026, 12, 1, 2, 0, 0)),
        ("*-*-* 04:30:00", datetime(2026, 11, 13, 4, 30, 0)),
        ("*-*-* *:0/5:00", datetime(2026, 11, 12, 10, 35, 0)),
        # The same day when the time has not come, and the next one when it has.
        ("*-*-* 23:00", datetime(2026, 11, 12, 23, 0, 0)),
        ("*-*-* 10:30:15", datetime(2026, 11, 13, 10, 30, 15)),
        ("*-*-* 10:30:16", datetime(2026, 11, 12, 10, 30, 16)),
        # Weekdays, as a name, a list and a range; either case; the long name.
        ("Fri 09:00", datetime(2026, 11, 13, 9, 0, 0)),
        ("Mon,Wed 03:00", datetime(2026, 11, 16, 3, 0, 0)),
        ("mon..fri 18:30", datetime(2026, 11, 12, 18, 30, 0)),
        ("Thursday 10:30:20", datetime(2026, 11, 12, 10, 30, 20)),
        ("Sat..Sun 08:00", datetime(2026, 11, 14, 8, 0, 0)),
        # Dates: a day, a month, a year, a list and a range of days.
        ("*-12-25 00:00", datetime(2026, 12, 25, 0, 0, 0)),
        ("2027-01-15 12:00", datetime(2027, 1, 15, 12, 0, 0)),
        ("*-*-1,15 06:00", datetime(2026, 11, 15, 6, 0, 0)),
        ("*-*-20..22 06:00", datetime(2026, 11, 20, 6, 0, 0)),
        ("*-1,7-* 06:00", datetime(2027, 1, 1, 6, 0, 0)),
        # Steps and ranges in the time.
        ("*-*-* 00/6:00", datetime(2026, 11, 12, 12, 0, 0)),
        ("*-*-* */4:15", datetime(2026, 11, 12, 12, 15, 0)),
        ("*-*-* 9..17:00", datetime(2026, 11, 12, 11, 0, 0)),
        ("*-*-* 10:30..45:00", datetime(2026, 11, 12, 10, 31, 0)),
        # A time alone is every day, and a date alone is at midnight.
        ("12:00", datetime(2026, 11, 12, 12, 0, 0)),
        ("*-*-15", datetime(2026, 11, 15, 0, 0, 0)),
    ],
)
def test_the_next_time_is_what_systemd_would_say(text: str, expected: datetime) -> None:
    assert next_of(text) == expected


def test_a_day_that_does_not_exist_in_a_month_is_skipped_to_one_that_has_it() -> None:
    assert next_of("*-*-31 12:00", datetime(2026, 11, 12)) == datetime(2026, 12, 31, 12, 0, 0)
    assert next_of("*-02-29 12:00", datetime(2026, 3, 1)) == datetime(2028, 2, 29, 12, 0, 0)


def test_the_time_is_strictly_after_so_a_run_that_just_happened_does_not_run_again() -> None:
    at = datetime(2026, 11, 13, 2, 0, 0)

    assert next_of("*-*-* 02:00:00", at) == datetime(2026, 11, 14, 2, 0, 0)


def test_a_schedule_with_nothing_left_to_run_says_so() -> None:
    assert next_of("2020-01-01 00:00") is None


def test_the_runs_between_two_moments_are_counted_and_the_last_is_known() -> None:
    calendar = Calendar.parse("*-*-* 02:00:00")

    runs = calendar.between(datetime(2026, 11, 8, 12, 0, 0), datetime(2026, 11, 12, 10, 0, 0))

    assert runs == [
        datetime(2026, 11, 9, 2, 0, 0),
        datetime(2026, 11, 10, 2, 0, 0),
        datetime(2026, 11, 11, 2, 0, 0),
        datetime(2026, 11, 12, 2, 0, 0),
    ]


def test_a_stretch_that_holds_too_many_runs_is_cut_so_a_long_outage_is_not_a_flood() -> None:
    calendar = Calendar.parse("*-*-* *:*:00")

    runs = calendar.between(datetime(2026, 1, 1), datetime(2026, 11, 12), limit=50)

    assert len(runs) == 50


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "someday",
        "*-*-* 25:00",
        "*-*-* 10:61",
        "*-13-01 00:00",
        "*-*-32 00:00",
        "Funday 10:00",
        "Mon..Foo 10:00",
        "*-*-* 10:00 UTC",
        "*-*-* 10:00:00:00",
        "*-*-* */0:00",
        "*-*-* 10:30~",
        "10:00 11:00",
        "5..1 10:00".replace("5..1", "*-*-5..1"),
        "*-*-* 10:00/x",
    ],
)
def test_what_is_not_understood_is_refused_by_name_and_never_run_at_a_guess(text: str) -> None:
    with pytest.raises(CalendarError):
        Calendar.parse(text)


def test_the_refusal_says_which_part_was_not_understood() -> None:
    with pytest.raises(CalendarError) as caught:
        Calendar.parse("*-*-* 25:00")

    assert "25" in caught.value.message and "hour" in caught.value.message
    assert caught.value.details


def test_the_names_noust_gives_a_schedule_mean_what_noust_writes_for_them_and_not_systemds() -> (
    None
):
    """Noust's ``daily`` is two in the morning, not midnight: the name is expanded before systemd sees it."""
    from noust.managers.backup_scheduler import SCHEDULE_ALIASES

    daily = Calendar.parse("daily", aliases=SCHEDULE_ALIASES).next_after(NOW)

    assert daily == datetime(2026, 11, 13, 2, 0, 0)
    for alias, expression in SCHEDULE_ALIASES.items():
        assert Calendar.parse(alias, aliases=SCHEDULE_ALIASES).next_after(NOW) == Calendar.parse(
            expression
        ).next_after(NOW)
