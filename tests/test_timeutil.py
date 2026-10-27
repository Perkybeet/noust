# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the one place a stored timestamp gets a UTC offset attached.

The store writes ``datetime.now().isoformat()`` - naive, in whatever zone the
machine happens to be set to. A CLI process on that same machine never
noticed: it read the string back on the same machine, in the same zone. A
browser reading the same string over the API has no such luck, so
``to_iso_offset`` is the one function every timestamp-carrying response field
funnels through.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from noust.core.timeutil import local_now_iso, to_iso_offset, utc_now, utc_now_iso


def test_none_stays_none() -> None:
    """A timestamp that was never set must not become a string."""
    assert to_iso_offset(None) is None


def test_a_naive_string_gets_the_machines_own_offset() -> None:
    """
    This is the exact shape ``datetime.now().isoformat()`` produces.

    The result must parse back to the same wall-clock reading, with an
    offset attached rather than guessed at as UTC.
    """
    naive = datetime(2026, 6, 15, 10, 30, 0)

    result = to_iso_offset(naive.isoformat())

    assert result is not None
    parsed = datetime.fromisoformat(result)
    assert parsed.tzinfo is not None
    # The wall clock must not move: only the offset was attached.
    assert parsed.replace(tzinfo=None) == naive
    assert parsed.utcoffset() == naive.astimezone().utcoffset()


def test_a_naive_datetime_object_gets_the_machines_own_offset() -> None:
    """The function accepts a live ``datetime``, not only its string form."""
    naive = datetime(2026, 1, 1, 0, 0, 0)

    result = to_iso_offset(naive)

    assert result is not None
    parsed = datetime.fromisoformat(result)
    assert parsed.tzinfo is not None
    assert parsed.replace(tzinfo=None) == naive


def test_an_aware_string_is_left_exactly_as_it_was() -> None:
    """
    A value that already carries an offset is not the store's naive kind.

    Re-stamping it with the machine's own offset would be wrong whenever the
    machine's zone differs from the one the timestamp was already in.
    """
    aware = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone(timedelta(hours=-5)))

    result = to_iso_offset(aware.isoformat())

    assert result == aware.isoformat()


def test_an_aware_datetime_object_is_left_exactly_as_it_was() -> None:
    """Same guarantee, for a live ``datetime`` instead of its string form."""
    aware = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

    result = to_iso_offset(aware)

    assert result == aware.isoformat()


def test_a_string_that_is_not_a_timestamp_is_returned_unchanged() -> None:
    """
    systemd's own printed text ("Wed 2024-01-01 UTC") must not crash a
    response just because it landed on a field this function also touches;
    the operator needs to see it verbatim, not lose it to a 500.
    """
    assert to_iso_offset("never") == "never"


def test_an_empty_string_is_returned_unchanged() -> None:
    """A field that is a plain str, not optional, must stay a str."""
    assert to_iso_offset("") == ""


def test_the_offset_reflects_daylight_saving_at_that_date_not_today() -> None:
    """
    Python's own naive-to-aware conversion already accounts for this; this
    test pins that the function does not flatten it to "now's" offset.
    """
    winter = datetime(2026, 1, 15, 12, 0, 0)
    summer = datetime(2026, 7, 15, 12, 0, 0)

    winter_result = to_iso_offset(winter.isoformat())
    summer_result = to_iso_offset(summer.isoformat())

    assert winter_result is not None and summer_result is not None
    assert datetime.fromisoformat(winter_result).utcoffset() == winter.astimezone().utcoffset()
    assert datetime.fromisoformat(summer_result).utcoffset() == summer.astimezone().utcoffset()


# -- The clock the stores and the managers read ------------------------------------------------
#
# Forty-five modules each had their own ``_now``, in five spellings that stored three formats. The
# format a column holds is data: a later reader parses and compares it, so each spelling is one
# function here and a module keeps the one it wrote.


def test_the_clock_with_seconds_is_utc_with_its_offset_and_no_fraction() -> None:
    text = utc_now_iso("seconds")

    parsed = datetime.fromisoformat(text)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", text)
    assert parsed.utcoffset() == timedelta(0)


def test_the_clock_without_a_timespec_writes_what_isoformat_writes() -> None:
    """The default is ``datetime.isoformat()``: a fraction whenever there is one."""
    text = utc_now_iso()

    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{6})?\+00:00", text)
    assert datetime.fromisoformat(text).utcoffset() == timedelta(0)


def test_the_timespec_is_the_one_datetime_takes() -> None:
    assert re.fullmatch(r"[^.]*\.\d{6}\+00:00", utc_now_iso("microseconds"))
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d\+00:00", utc_now_iso("minutes"))


def test_the_local_clock_is_naive_and_to_the_second() -> None:
    """The store's own spelling: what ``NoustStore`` writes and ``to_iso_offset`` reads."""
    text = local_now_iso()

    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", text)
    assert datetime.fromisoformat(text).tzinfo is None


def test_the_clock_of_datetimes_is_utc() -> None:
    assert utc_now().utcoffset() == timedelta(0)


def test_the_clocks_move_together_with_the_machines() -> None:
    before = datetime.now(timezone.utc)

    moment = datetime.fromisoformat(utc_now_iso())

    assert before <= moment <= datetime.now(timezone.utc)
