# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The moment a one-time task runs at (item 164).

It is read in the server's zone, converted when it carries an offset, and has to lie ahead: a
moment that has passed is refused with an error that says so and says what time it is here.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest

from noust.core.exceptions import ValidationError
from noust.validators.once_at import MAX_AHEAD, parse_once_at, zone_label

#: Central European time without a time zone database: +01:00, +02:00 in summer.
CET = "CET-1CEST,M3.5.0,M10.5.0/3"


@pytest.fixture(autouse=True)
def madrid(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TZ", CET)
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def clock() -> datetime:
    return datetime(2026, 11, 12, 10, 0).astimezone()


class TestTheMomentIsReadInTheServersZone:
    def test_a_date_and_a_time_are_the_servers_own(self) -> None:
        at = parse_once_at("2026-12-01 14:30", now=clock())

        assert at.isoformat() == "2026-12-01T14:30:00+01:00"

    def test_a_t_and_seconds_are_accepted(self) -> None:
        assert parse_once_at("2026-12-01T14:30:15", now=clock()).second == 15

    def test_summer_time_is_the_zones_for_that_date_and_not_for_today(self) -> None:
        at = parse_once_at("2027-07-01 14:30", now=clock())

        assert at.isoformat() == "2027-07-01T14:30:00+02:00"

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("2026-12-01T13:30:00Z", "2026-12-01T14:30:00+01:00"),
            ("2026-12-01 14:30+01:00", "2026-12-01T14:30:00+01:00"),
            ("2026-12-01 09:30-04:00", "2026-12-01T14:30:00+01:00"),
            ("2026-12-01 19:00+0530", "2026-12-01T14:30:00+01:00"),
        ],
    )
    def test_a_moment_with_its_own_offset_is_converted(self, text: str, expected: str) -> None:
        assert parse_once_at(text, now=clock()).isoformat() == expected


class TestItHasToLieAhead:
    def test_a_moment_that_has_passed_says_so_and_what_time_it_is_here(self) -> None:
        with pytest.raises(ValidationError, match="has already passed") as refused:
            parse_once_at("2026-11-12 09:59", now=clock())

        assert refused.value.field == "once_at"
        assert "2026-11-12 10:00" in refused.value.details
        assert "UTC+01:00" in refused.value.details

    def test_the_present_moment_is_a_moment_that_has_passed(self) -> None:
        with pytest.raises(ValidationError, match="has already passed"):
            parse_once_at("2026-11-12 10:00", now=clock())

    def test_a_minute_ahead_is_enough(self) -> None:
        assert parse_once_at("2026-11-12 10:01", now=clock()).minute == 1

    def test_the_past_in_another_zone_is_the_past(self) -> None:
        # 10:30 in Madrid is 09:30 UTC: the clock says 10:00 there.
        with pytest.raises(ValidationError, match="has already passed"):
            parse_once_at("2026-11-12 09:00Z", now=clock())

    def test_more_than_two_years_ahead_is_refused(self) -> None:
        far = (clock() + MAX_AHEAD + timedelta(days=2)).strftime("%Y-%m-%d %H:%M")

        with pytest.raises(ValidationError, match="too far ahead"):
            parse_once_at(far, now=clock())


class TestWhatIsNotAMoment:
    @pytest.mark.parametrize(
        "text",
        [
            "",
            "tomorrow",
            "2026-12-01",
            "14:30",
            "2026-13-01 14:30",
            "2026-02-30 14:30",
            "2026-12-01 25:30",
            "2026-12-01 14:30 and then some",
            "2026-12-01 14:30\nOnBootSec=1",
            "2026-12-01 14:30\n",
            "*-*-* 14:30:00",
            "2026-12-01 14:30+25:00",
            "2026-12-01 14:30+01:99",
            "\uff12\uff10\uff12\uff16-12-01 14:30",
        ],
    )
    def test_it_is_refused_whole(self, text: str) -> None:
        with pytest.raises(ValidationError) as refused:
            parse_once_at(text, now=clock())

        assert refused.value.field == "once_at"

    def test_a_value_that_is_not_text_is_refused_cleanly(self) -> None:
        with pytest.raises(ValidationError):
            parse_once_at(None, now=clock())  # type: ignore[arg-type]


def test_the_zone_is_named_the_way_the_console_names_it() -> None:
    assert zone_label(datetime(2026, 12, 1, 14, 30).astimezone()) == "UTC+01:00"
    assert zone_label(datetime(2027, 7, 1, 14, 30).astimezone()) == "UTC+02:00"
