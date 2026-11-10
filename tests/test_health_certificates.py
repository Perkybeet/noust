# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust health`` judges a certificate on its own life, and says when its renewal is not working (item 172).

The thresholds of before were days (warn under 30, fail under 7). With certificates of 64 and then
45 days, a healthy one would sit in warning for half its life, and one of 6 days would be critical
from the day it was issued.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from noust.managers import health as health_module
from noust.managers.cert_renewal import RenewalStatus
from tests.support.cli_health import (  # noqa: F401
    _FakeCerts,
    _FakeServices,
    _FakeStore,
    _FakeWebServer,
    server,
)

# ruff: noqa: F811


def _cert(
    life_days: float,
    days_left: float,
    *,
    name: str = "shop.example.com",
    source: str = "letsencrypt",
) -> dict[str, Any]:
    """A certificate of ``life_days`` with ``days_left`` still to go, as the manager lists it."""
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days_left)
    return {
        "name": name,
        "domains": [name],
        "expiry": end.strftime("%Y-%m-%d"),
        "not_after": end.isoformat(timespec="seconds"),
        "not_before": (end - timedelta(days=life_days)).isoformat(timespec="seconds"),
        "source": source,
    }


def _check(server: Any, *certs: dict[str, Any]) -> tuple[Any, health_module.HealthReport]:
    server.certs = _FakeCerts(list(certs))
    report = health_module.collect_health_report()
    return report.certificates, report


@pytest.mark.parametrize(
    ("life", "left", "state"),
    [
        (90, 80, "ok"),
        (90, 25, "warning"),  # under a third of 90 days: renewal pending
        (90, 10, "error"),  # under a sixth: critical
        (64, 30, "ok"),
        (64, 20, "warning"),
        (64, 9, "error"),
        (45, 25, "ok"),  # a warning for 30 days under the old thresholds
        (45, 14, "warning"),
        (45, 7, "error"),
        (6, 4.5, "ok"),
        (6, 1.5, "warning"),
        (6, 0.5, "error"),
    ],
)
def test_a_certificate_is_judged_on_its_own_life(
    server: Any, life: int, left: float, state: str
) -> None:
    check, _ = _check(server, _cert(life, left))

    assert check.status == state


def test_a_healthy_certificate_of_45_days_is_not_a_warning(server: Any) -> None:
    check, report = _check(server, _cert(45, 25))

    assert check.value == "1 total, all valid"
    assert report.warnings == []
    assert report.issues == []


def test_a_certificate_past_its_renewal_point_says_it_is_waiting_for_renewal(server: Any) -> None:
    _, report = _check(server, _cert(64, 18))

    assert any(
        "renewal is pending" in line and "shop.example.com" in line for line in report.warnings
    )


def test_a_critical_certificate_is_an_issue_and_says_its_renewal_is_overdue(server: Any) -> None:
    check, report = _check(server, _cert(64, 8))

    assert any("renewal is overdue" in line for line in report.issues)
    assert check.status == "error"


def test_an_uploaded_certificate_follows_the_same_calculation(server: Any) -> None:
    check, _ = _check(server, _cert(365, 200, name="corp.example.com", source="custom"))

    assert check.status == "ok"
    check, report = _check(server, _cert(365, 100, name="corp.example.com", source="custom"))
    assert check.status == "warning"
    assert any("corp.example.com" in line for line in report.warnings)


def test_a_certificate_with_no_known_start_is_judged_on_90_days(server: Any) -> None:
    """What certbot's listing gives when openssl could not be asked: the thresholds of before."""
    entry = _cert(90, 20)
    entry.pop("not_before")

    check, _ = _check(server, entry)

    assert check.status == "warning"


class TestTheRenewalItself:
    def test_a_failed_renewal_is_a_warning_with_certbots_own_words(
        self, server: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        said = "Failed to renew certificate shop.example.com with error: Timeout during connect"
        monkeypatch.setattr(
            health_module,
            "read_renewal",
            lambda runner: RenewalStatus(
                mechanism="timer",
                unit="certbot.timer",
                timer_active=True,
                last_ok=False,
                last_run="Tue 2026-11-10 12:11:09 UTC",
                output=said,
            ),
        )

        check, report = _check(server, _cert(90, 70))

        assert check.status == "warning"
        assert "renewal failing" in check.value
        assert any(said in line for line in report.warnings)
        assert any("Tue 2026-11-10 12:11:09 UTC" in line for line in report.warnings)

    def test_a_failed_renewal_of_a_critical_certificate_is_an_issue_too(
        self, server: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            health_module,
            "read_renewal",
            lambda runner: RenewalStatus(
                mechanism="timer", timer_active=True, last_ok=False, output="boom"
            ),
        )

        check, report = _check(server, _cert(64, 5))

        assert check.status == "error"
        assert any("boom" in line for line in report.issues + report.warnings)

    def test_a_stopped_timer_is_a_warning(
        self, server: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            health_module,
            "read_renewal",
            lambda runner: RenewalStatus(
                mechanism="timer", unit="certbot.timer", timer_active=False
            ),
        )

        check, report = _check(server, _cert(90, 70))

        assert check.status == "warning"
        assert any("certbot.timer" in line and "not running" in line for line in report.warnings)

    def test_a_working_renewal_adds_nothing(
        self, server: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            health_module,
            "read_renewal",
            lambda runner: RenewalStatus(
                mechanism="timer", timer_active=True, last_ok=True, last_run="Tue"
            ),
        )

        check, report = _check(server, _cert(90, 70))

        assert check.status == "ok"
        assert report.warnings == []

    def test_a_server_without_certificates_does_not_ask_how_they_renew(
        self, server: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(runner: object) -> RenewalStatus:
            raise AssertionError("nothing to renew, nothing to ask")

        monkeypatch.setattr(health_module, "read_renewal", boom)

        check, _ = _check(server)

        assert check.status == "info"
