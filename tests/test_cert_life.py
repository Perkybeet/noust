# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How a certificate is doing, measured on its own life (item 172).

Let's Encrypt issues for 90 days now, for 64 from 2027-02-10 and for 45 from 2028-02-16, and
offers a profile of 6 days. A threshold in days is wrong for at least one of them, so the answer
is a fraction of the certificate's life: renewal pending under a third, critical under a sixth.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from noust.core import cert_life
from noust.core.cert_life import CRITICAL, EXPIRED, HEALTHY, RENEWAL_PENDING, UNKNOWN, assess

ISSUED = datetime(2027, 3, 1, 12, 0, tzinfo=timezone.utc)


def _at(life_days: float, elapsed: float) -> cert_life.CertLife:
    """A certificate of ``life_days``, looked at when ``elapsed`` of that life has gone."""
    life = timedelta(days=life_days)
    return assess(
        not_after=ISSUED + life,
        not_before=ISSUED,
        now=ISSUED + life * elapsed,
    )


#: Where each stage of a life starts: (elapsed fraction, state).
STAGES = [
    (0.0, HEALTHY),
    (0.5, HEALTHY),
    (0.66, HEALTHY),
    (0.67, RENEWAL_PENDING),
    (0.8, RENEWAL_PENDING),
    (0.83, RENEWAL_PENDING),
    (0.84, CRITICAL),
    (0.99, CRITICAL),
    (1.01, EXPIRED),
]


@pytest.mark.parametrize("life_days", [90, 64, 45, 6])
@pytest.mark.parametrize(("elapsed", "state"), STAGES)
def test_the_state_follows_the_fraction_of_the_life_left_whatever_the_life(
    life_days: int, elapsed: float, state: str
) -> None:
    assert _at(life_days, elapsed).state == state


def test_25_days_left_is_a_warning_for_a_90_day_certificate_and_none_for_a_45_day_one() -> None:
    ninety = assess(
        not_after=ISSUED + timedelta(days=90),
        not_before=ISSUED,
        now=ISSUED + timedelta(days=65),
    )
    forty_five = assess(
        not_after=ISSUED + timedelta(days=45),
        not_before=ISSUED,
        now=ISSUED + timedelta(days=20),
    )

    assert (ninety.days_left, forty_five.days_left) == (25, 25)
    assert ninety.state == RENEWAL_PENDING
    assert forty_five.state == HEALTHY


def test_a_certificate_of_6_days_does_not_live_in_alarm() -> None:
    """Four of its six days it is healthy, and it is never critical until the last day."""
    states = [_at(6, day / 6).state for day in range(0, 5)]

    assert states == [HEALTHY] * 5
    assert _at(6, 4.2 / 6).state == RENEWAL_PENDING
    assert _at(6, 5.2 / 6).state == CRITICAL


def test_the_fraction_and_the_days_are_reported() -> None:
    life = _at(90, 0.5)

    assert life.days_left == 45
    assert life.life_days == pytest.approx(90.0)
    assert life.fraction_left == pytest.approx(0.5)
    assert life.assumed_life is False


def test_days_left_is_negative_once_the_certificate_expired() -> None:
    expired = assess(
        not_after=ISSUED,
        not_before=ISSUED - timedelta(days=90),
        now=ISSUED + timedelta(days=3, hours=2),
    )

    assert expired.state == EXPIRED
    assert expired.days_left == -4  # floor, as the days of every other reader of a date


def test_without_a_start_the_life_is_assumed_to_be_90_days() -> None:
    """What certbot's own listing gives when openssl could not be asked."""
    late = assess(not_after=ISSUED + timedelta(days=20), not_before=None, now=ISSUED)
    early = assess(not_after=ISSUED + timedelta(days=40), not_before=None, now=ISSUED)

    assert late.assumed_life is True
    assert late.life_days == 90.0
    assert late.state == RENEWAL_PENDING
    assert early.state == HEALTHY


def test_a_start_after_the_end_is_not_a_life() -> None:
    nonsense = assess(
        not_after=ISSUED + timedelta(days=10),
        not_before=ISSUED + timedelta(days=20),
        now=ISSUED,
    )

    assert nonsense.assumed_life is True


def test_a_certificate_with_no_end_is_unknown() -> None:
    unknown = assess(not_after=None, not_before=ISSUED, now=ISSUED)

    assert unknown.state == UNKNOWN
    assert unknown.days_left is None
    assert unknown.announces(cap_days=None) is False


class TestWhatTheMonitorAnnounces:
    """``monitor.cert_warning_days`` is the operator's ceiling, and only counts past the renewal point."""

    def test_nothing_is_said_of_a_healthy_certificate_however_low_the_ceiling_is_set(self) -> None:
        assert _at(45, 0.5).announces(cap_days=60) is False

    def test_a_certificate_past_its_renewal_point_is_said_when_it_is_under_the_ceiling(
        self,
    ) -> None:
        pending = _at(90, 0.75)  # 22.5 days left

        assert pending.announces(cap_days=30) is True
        assert pending.announces(cap_days=14) is False

    def test_no_ceiling_means_every_certificate_past_its_renewal_point(self) -> None:
        assert _at(90, 0.75).announces(cap_days=None) is True

    def test_an_expired_certificate_is_always_said(self) -> None:
        assert _at(90, 1.5).announces(cap_days=1) is True

    def test_a_certificate_of_45_days_is_said_a_day_and_a_half_after_its_renewal_point_with_the_default_ceiling(
        self,
    ) -> None:
        """Its renewal point is 15 days left, the default ceiling 14: it speaks once it is overdue."""
        assert _at(45, 31.5 / 45).announces(cap_days=14) is True
        assert _at(45, 30.5 / 45).announces(cap_days=14) is False
        assert _at(45, 29 / 45).announces(cap_days=14) is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2027-03-01", datetime(2027, 3, 1, tzinfo=timezone.utc)),
        ("2027-03-01T10:15:00+00:00", datetime(2027, 3, 1, 10, 15, tzinfo=timezone.utc)),
        ("2027-03-01T10:15:00Z", datetime(2027, 3, 1, 10, 15, tzinfo=timezone.utc)),
        ("2027-03-01 10:15:00+00:00", datetime(2027, 3, 1, 10, 15, tzinfo=timezone.utc)),
        (
            "2027-03-01 12:15:00+02:00 (VALID: 59 days)",
            datetime(2027, 3, 1, 10, 15, tzinfo=timezone.utc),
        ),
        ("Mar  1 10:15:00 2027 GMT", datetime(2027, 3, 1, 10, 15, tzinfo=timezone.utc)),
        ("Mar 11 10:15:00 2027 GMT", datetime(2027, 3, 11, 10, 15, tzinfo=timezone.utc)),
        ("notAfter=Mar 11 10:15:00 2027 GMT", datetime(2027, 3, 11, 10, 15, tzinfo=timezone.utc)),
    ],
)
def test_a_moment_is_read_the_way_certbot_openssl_and_the_store_write_it(
    text: str, expected: datetime
) -> None:
    assert cert_life.parse_moment(text) == expected


@pytest.mark.parametrize("text", ["", None, "soon", "2027-13-45", "Mar 99 10:15:00 2027 GMT"])
def test_a_moment_that_cannot_be_read_is_none(text: str | None) -> None:
    assert cert_life.parse_moment(text) is None


def test_a_date_without_a_time_is_good_until_the_end_of_that_day() -> None:
    """An expiry written as a date alone: the whole of the day counts, as it always did."""
    moment = cert_life.parse_moment("2027-03-01", end_of_day=True)

    assert moment == datetime(2027, 3, 2, tzinfo=timezone.utc)
    assert cert_life.parse_moment("2027-03-01 10:15:00", end_of_day=True) == datetime(
        2027, 3, 1, 10, 15, tzinfo=timezone.utc
    )


def test_a_record_with_only_a_date_is_judged_to_the_end_of_it() -> None:
    today = datetime(2027, 3, 1, 15, 0, tzinfo=timezone.utc)

    on_the_day = cert_life.life_of({"expiry": "2027-03-01"}, now=today)
    the_day_after = cert_life.life_of({"expiry": "2027-02-28"}, now=today)
    in_five_days = cert_life.life_of({"expiry": "2027-03-06"}, now=today)

    assert (on_the_day.days_left, on_the_day.state) == (0, "critical")
    assert (the_day_after.days_left, the_day_after.state) == (-1, "expired")
    assert in_five_days.days_left == 5
