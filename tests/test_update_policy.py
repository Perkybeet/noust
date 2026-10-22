# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The limits of Noust updating itself unattended (checklist N-28): a window, days, a channel,
and a major version that no channel takes.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from noust.core import update_policy as policy
from noust.core.update_policy import Window


def at(text: str) -> datetime:
    """2026-10-14 is a Wednesday."""
    return datetime.fromisoformat(f"2026-10-{text}")


class TestWindow:
    def test_reads_a_window_and_writes_it_back(self) -> None:
        assert str(policy.parse_window("03:00-05:00")) == "03:00-05:00"

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "3-5",
            "03:00",
            "24:00-05:00",
            "03:60-05:00",
            "03:00-05:00 ",
            "03:00 - 05:00",
            "03:00-03:00",
            None,
            3,
        ],
    )
    def test_refuses_what_is_not_a_window(self, text: object) -> None:
        if text == "03:00-05:00 ":
            assert (
                str(policy.parse_window(text)) == "03:00-05:00"
            )  # surrounding blanks are not a mistake
            return
        with pytest.raises(ValueError):
            policy.parse_window(text)

    def test_is_open_from_its_first_minute_to_the_last_before_it_closes(self) -> None:
        window = policy.parse_window("03:00-05:00")
        assert window.opened_on(at("14T02:59")) is None
        assert window.opened_on(at("14T03:00")) == date(2026, 10, 14)
        assert window.opened_on(at("14T04:59:59")) == date(2026, 10, 14)
        assert window.opened_on(at("14T05:00")) is None

    def test_a_window_across_midnight_belongs_to_the_day_it_opened_on(self) -> None:
        window = policy.parse_window("23:00-01:00")
        assert window.crosses_midnight
        assert window.opened_on(at("14T22:59")) is None
        assert window.opened_on(at("14T23:30")) == date(2026, 10, 14)
        assert window.opened_on(at("15T00:30")) == date(2026, 10, 14)
        assert window.opened_on(at("15T01:00")) is None

    @pytest.mark.parametrize(
        ("window", "hours"),
        [
            ("03:00-05:00", (3, 4)),
            ("03:30-04:15", (3, 4)),
            ("03:00-03:30", (3,)),
            ("23:00-01:00", (23, 0)),
            ("22:30-02:15", (22, 23, 0, 1, 2)),
            ("23:30-00:00", (23,)),
        ],
    )
    def test_names_the_hours_it_touches(self, window: str, hours: tuple[int, ...]) -> None:
        assert policy.parse_window(window).hours() == hours


class TestDays:
    def test_daily_is_every_day(self) -> None:
        assert policy.parse_days("daily") == policy.WEEKDAYS
        assert policy.days_text(policy.WEEKDAYS) == "daily"

    def test_a_list_is_kept_in_the_order_of_the_week(self) -> None:
        assert policy.parse_days("Thu, mon") == ("mon", "thu")
        assert policy.days_text(("mon", "thu")) == "mon,thu"

    @pytest.mark.parametrize("text", ["", "weekdays", "mon,funday", "daily,mon", None])
    def test_refuses_what_is_not_a_day(self, text: object) -> None:
        with pytest.raises(ValueError):
            policy.parse_days(text)

    def test_a_closed_day_does_not_open_the_window(self) -> None:
        window = policy.parse_window("03:00-05:00")
        wednesday_only = policy.parse_days("wed")
        assert policy.window_day(window, wednesday_only, at("14T03:30")) == date(2026, 10, 14)
        assert policy.window_day(window, wednesday_only, at("15T03:30")) is None

    def test_the_hours_after_midnight_follow_the_day_the_window_opened_on(self) -> None:
        window = policy.parse_window("23:00-01:00")
        wednesday_only = policy.parse_days("wed")
        assert policy.window_day(window, wednesday_only, at("15T00:30")) == date(2026, 10, 14)
        assert (
            policy.window_day(window, wednesday_only, at("14T00:30")) is None
        )  # Tuesday night's window


class TestChannel:
    @pytest.mark.parametrize("text", ["security", "patch", "stable", " Patch "])
    def test_reads_the_three(self, text: str) -> None:
        assert policy.parse_channel(text) == text.strip().lower()

    @pytest.mark.parametrize("text", ["", "next", "lts", None])
    def test_refuses_what_there_is_not(self, text: object) -> None:
        with pytest.raises(ValueError):
            policy.parse_channel(text)


class TestWhatAChannelTakes:
    @pytest.mark.parametrize(
        ("channel", "target", "security", "taken"),
        [
            ("security", "3.4.1", False, False),
            ("security", "3.4.1", True, True),
            ("security", "3.5.0", True, True),
            ("patch", "3.4.1", False, True),
            ("patch", "3.5.0", False, False),
            ("patch", "3.5.0", True, True),
            ("stable", "3.4.1", False, True),
            ("stable", "3.5.0", False, True),
        ],
    )
    def test_each_channel(self, channel: str, target: str, security: bool, taken: bool) -> None:
        assert policy.allows(channel, "3.4.0", target, security=security)[0] is taken

    @pytest.mark.parametrize("channel", policy.CHANNELS)
    def test_no_channel_takes_a_new_major_even_to_fix_a_vulnerability(self, channel: str) -> None:
        allowed, why = policy.allows(channel, "3.4.0", "4.0.0", security=True)
        assert not allowed
        assert "major" in why

    @pytest.mark.parametrize("target", ["3.4.0", "3.3.9", "garbage", ""])
    def test_takes_nothing_that_is_not_newer(self, target: str) -> None:
        assert not policy.allows("stable", "3.4.0", target, security=True)[0]

    def test_a_packaged_version_is_read_by_its_upstream_number(self) -> None:
        assert policy.version_parts("3.4.1-1") == (3, 4, 1)
        assert policy.version_parts("v3.4.1") == (3, 4, 1)
        assert policy.allows("patch", "3.4.0", "3.4.1-1", security=False)[0]

    def test_says_why_in_a_sentence_an_operator_can_act_on(self) -> None:
        assert "upgrade guide" in policy.allows("patch", "3.4.0", "3.5.0", security=False)[1]


class TestTheTimersCalendar:
    def test_looks_every_ten_minutes_through_the_hours_of_the_window(self) -> None:
        window = policy.parse_window("03:00-05:00")
        assert policy.calendars(window, policy.WEEKDAYS) == ["*-*-* 03,04:00/10:00"]

    def test_names_the_days_the_window_opens_on(self) -> None:
        window = policy.parse_window("03:00-05:00")
        assert policy.calendars(window, ("mon", "thu")) == ["Mon,Thu *-*-* 03,04:00/10:00"]

    def test_a_window_across_midnight_is_two_expressions_and_the_second_is_a_day_later(
        self,
    ) -> None:
        window = policy.parse_window("23:00-01:00")
        assert policy.calendars(window, policy.WEEKDAYS) == [
            "*-*-* 23:00/10:00",
            "*-*-* 00:00/10:00",
        ]
        assert policy.calendars(window, ("sun",)) == [
            "Sun *-*-* 23:00/10:00",
            "Mon *-*-* 00:00/10:00",
        ]

    def test_every_expression_is_one_the_scheduler_accepts(self) -> None:
        from noust.managers.backup_scheduler import validate_calendar

        for window in ("03:00-05:00", "23:00-01:00", "22:30-02:15"):
            for days in (policy.WEEKDAYS, ("mon", "thu"), ("sun",)):
                for line in policy.calendars(policy.parse_window(window), days):
                    assert validate_calendar(line) == line

    def test_the_window_type_prints_as_it_is_written(self) -> None:
        assert (
            str(
                Window(
                    policy.parse_window("03:00-05:00").start, policy.parse_window("03:00-05:00").end
                )
            )
            == "03:00-05:00"
        )
