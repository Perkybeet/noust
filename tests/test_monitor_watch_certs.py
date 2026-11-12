# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The monitor tells the operator when certificates stopped renewing (item 172).

certbot renews certificates by itself, and a renewal that fails (a closed port 80, a rule that
refuses the validation, a record that moved) is silent until the certificate is close to its end.
With certificates of 45 days that is not much time. The watch reads the result of the last run, says
it once with certbot's own words, reminds after three days if it still does not work, and says
nothing of a server that renews some other way.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.config import DEFAULT_CONFIG
from noust.core.messages import MESSAGES
from noust.core.notifications.composers_watch import compose_cert_renewal
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import ALL_KINDS, EVENT_KINDS, State
from noust.managers.cert_renewal import RenewalStatus
from noust.monitor.watch_certs import RENOTIFY_SECONDS, CertRenewalWatch
from tests.support.monitor_rig import Rig

HOUR = 3600.0
CERTBOT_SAYS = (
    "Failed to renew certificate shop.example.com with error: Some challenges have failed.\n"
    "Detail: 203.0.113.9: Timeout during connect (likely firewall problem)"
)


def failed(stamp: str = "Tue 2026-11-10 12:11:09 UTC") -> RenewalStatus:
    return RenewalStatus(
        mechanism="timer",
        unit="certbot.timer",
        timer_active=True,
        last_run=stamp,
        last_ok=False,
        next_run="Wed 2026-11-11 00:41:10 UTC",
        output=CERTBOT_SAYS,
    )


def working(stamp: str = "Wed 2026-11-11 00:41:12 UTC") -> RenewalStatus:
    return RenewalStatus(
        mechanism="timer", unit="certbot.timer", timer_active=True, last_run=stamp, last_ok=True
    )


STOPPED = RenewalStatus(mechanism="timer", unit="certbot.timer", timer_active=False)

#: What ``certbot certificates`` prints for a server with one lineage.
CERTBOT_LISTING = (
    "Found the following certs:\n"
    "  Certificate Name: shop.example.com\n"
    "    Domains: shop.example.com\n"
    "    Expiry Date: 2026-11-30 12:00:00+00:00 (VALID: 18 days)\n"
    "    Certificate Path: /etc/letsencrypt/live/shop.example.com/fullchain.pem\n"
    "    Private Key Path: /etc/letsencrypt/live/shop.example.com/privkey.pem\n"
)


class Shelf:
    """What certbot's renewal reads as, and which certificates it renews, replaceable by a test."""

    def __init__(self, status: RenewalStatus, names: tuple[str, ...] = ("shop.example.com",)):
        self.status = status
        self.names = names

    def read(self, runner: object) -> RenewalStatus:
        return self.status

    def certificates(self) -> list[str]:
        return list(self.names)


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    instance = Rig(tmp_path)
    yield instance
    instance.close()


def watch(shelf: Shelf) -> CertRenewalWatch:
    return CertRenewalWatch(renewal=shelf.read, certificates=shelf.certificates)


class TestTheMessages:
    def test_a_failed_renewal_carries_certbots_own_words(self, rig: Rig) -> None:
        note = compose_cert_renewal(
            "failed",
            rig.context,
            unit="certbot.timer",
            last_run="Tue 2026-11-10 12:11:09 UTC",
            output=CERTBOT_SAYS,
            certificates=["shop.example.com"],
        )

        assert (note.kind, note.code, note.state) == (
            "cert_renewal_failed",
            "cert.renewal_failed",
            State.FAILED,
        )
        assert note.excerpt is not None
        assert "Detail: 203.0.113.9" in "\n".join(note.excerpt.lines)
        facts = {f.key: f.value for f in note.facts}
        assert facts["renewal_last"] == "Tue 2026-11-10 12:11:09 UTC"
        assert "shop.example.com" in facts["certificates"]

    def test_a_stopped_timer_is_a_warning_and_says_how_to_start_it(self, rig: Rig) -> None:
        note = compose_cert_renewal("timer_stopped", rig.context, unit="certbot.timer")

        assert (note.kind, note.code, note.state) == (
            "cert_renewal_failed",
            "cert.renewal_stopped",
            State.WARNING,
        )
        assert note.command is not None
        assert note.command.value == "systemctl enable --now certbot.timer"

    def test_a_failure_says_what_to_run_to_see_it_again(self, rig: Rig) -> None:
        note = compose_cert_renewal("failed", rig.context, unit="certbot.timer", output="x")

        assert note.command is not None
        assert note.command.value == "noust cert renew"

    @pytest.mark.parametrize("locale", ["en", "es"])
    def test_both_are_said_in_both_languages_with_nothing_left_unfilled(self, locale: str) -> None:
        for key in (
            "title.cert.renewal_failed",
            "title.cert.renewal_stopped",
            "summary.cert.renewal_failed",
            "summary.cert.renewal_stopped",
            "fact.renewal_last",
            "fact.renewal_next",
            "fact.certificates",
            "fact.enable_timer",
            "excerpt.cert.renewal",
        ):
            assert MESSAGES[key][locale].strip(), (key, locale)
        context = NotificationContext(locale=locale, server="web-1")  # type: ignore[arg-type]
        for problem in ("failed", "timer_stopped"):
            note = compose_cert_renewal(problem, context, unit="certbot.timer", output="boom")
            assert "{" not in note.summary and "{" not in note.title

    def test_it_is_an_event_an_operator_can_switch_off(self) -> None:
        assert "cert_renewal_failed" in EVENT_KINDS and "cert_renewal_failed" in ALL_KINDS
        assert DEFAULT_CONFIG["notifications"]["events"]["cert_renewal_failed"] is True


class TestTheWatch:
    def test_a_failed_run_is_said_once_with_what_certbot_said(self, rig: Rig) -> None:
        certbot = Shelf(failed())

        watch(certbot).check(rig.services)
        rig.now += 3 * HOUR
        watch(certbot).check(rig.services)

        assert rig.codes() == ["cert.renewal_failed"]

    def test_the_certificates_it_renews_are_named(self, rig: Rig) -> None:
        certbot = Shelf(failed(), names=("a.example.com", "b.example.com"))

        watch(certbot).check(rig.services)

        [note] = rig.sent
        facts = {f.key: f.value for f in note.facts}
        assert facts["certificates"] == "a.example.com, b.example.com"

    def test_it_is_said_again_after_three_days_if_it_still_does_not_work(self, rig: Rig) -> None:
        certbot = Shelf(failed())
        watch(certbot).check(rig.services)

        rig.now += RENOTIFY_SECONDS - 60
        watch(certbot).check(rig.services)
        assert rig.codes() == ["cert.renewal_failed"]

        rig.now += 120
        certbot.status = failed("Fri 2026-11-13 12:11:09 UTC")
        watch(certbot).check(rig.services)
        assert rig.codes() == ["cert.renewal_failed", "cert.renewal_failed"]

    def test_a_renewal_that_works_again_closes_the_episode(self, rig: Rig) -> None:
        certbot = Shelf(failed())
        watch(certbot).check(rig.services)

        certbot.status = working()
        rig.now += 3 * HOUR
        watch(certbot).check(rig.services)
        certbot.status = failed("Thu 2026-11-12 00:41:09 UTC")
        rig.now += 3 * HOUR
        watch(certbot).check(rig.services)

        assert rig.codes() == ["cert.renewal_failed", "cert.renewal_failed"]

    def test_a_stopped_timer_is_said_once(self, rig: Rig) -> None:
        certbot = Shelf(STOPPED)

        watch(certbot).check(rig.services)
        rig.now += 3 * HOUR
        watch(certbot).check(rig.services)

        assert rig.codes() == ["cert.renewal_stopped"]

    def test_the_timer_stopping_after_a_failure_is_a_new_thing_to_say(self, rig: Rig) -> None:
        certbot = Shelf(failed())
        watch(certbot).check(rig.services)

        certbot.status = STOPPED
        watch(certbot).check(rig.services)

        assert rig.codes() == ["cert.renewal_failed", "cert.renewal_stopped"]

    def test_a_server_that_renews_some_other_way_is_not_missed(self, rig: Rig) -> None:
        watch(Shelf(RenewalStatus())).check(rig.services)

        assert rig.sent == []

    def test_a_server_with_no_certificate_that_certbot_renews_says_nothing(self, rig: Rig) -> None:
        """An uploaded certificate is not renewed by anyone, and a timer is nobody's business."""
        watch(Shelf(failed(), names=())).check(rig.services)

        assert rig.sent == []

    def test_not_knowing_is_not_a_change(self, rig: Rig) -> None:
        certbot = Shelf(failed())
        watch(certbot).check(rig.services)

        certbot.status = RenewalStatus(mechanism="none")
        rig.now += 3 * HOUR
        watch(certbot).check(rig.services)
        certbot.status = failed("Thu 2026-11-12 00:41:09 UTC")
        rig.now += 3 * HOUR
        watch(certbot).check(rig.services)

        assert rig.codes() == ["cert.renewal_failed"]

    def test_the_certificates_it_renews_are_asked_of_certbot_through_the_runner_it_is_lent(
        self, rig: Rig
    ) -> None:
        """The monitor's runner is the one way a process runs: a watch never makes a second."""
        rig.runner.script(["certbot", "certificates"], stdout=CERTBOT_LISTING)

        CertRenewalWatch(renewal=Shelf(failed()).read).check(rig.services)

        assert ("certbot", "certificates") in rig.runner.calls
        [note] = rig.sent
        assert {f.key: f.value for f in note.facts}["certificates"] == "shop.example.com"

    def test_certbot_that_cannot_be_asked_leaves_the_watch_silent(self, rig: Rig) -> None:
        rig.runner.script(
            ["certbot", "certificates"], stderr="certbot: command not found", exit_code=127
        )

        CertRenewalWatch(renewal=Shelf(failed()).read).check(rig.services)

        assert ("certbot", "certificates") in rig.runner.calls
        assert rig.sent == []

    def test_the_monitor_runs_it(self) -> None:
        from noust.core.runner import FakeRunner
        from noust.monitor.process_monitor import ProcessMonitor

        monitor = ProcessMonitor(verbose=False, runner=FakeRunner())

        assert "cert_renewal" in [watch.name for watch in monitor._watches]
