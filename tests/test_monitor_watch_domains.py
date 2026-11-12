# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The monitor tells the operator when the registration of a domain is about to lapse (item 173).

Off unless ``monitor.domain_expiry`` is on, because the question goes to a third party (the registry
of the domain). Once on, one question a day for each registrable domain Noust serves, and a message
at 30 days, at 7 days and when it expired: once each, and again if the registration is renewed and
then comes close again.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from noust.core.config import DEFAULT_CONFIG
from noust.core.domain_registrations import ERROR, NO_RDAP, OK, Registration
from noust.core.messages import MESSAGES
from noust.core.notifications.composers_watch import compose_domain_registration
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import ALL_KINDS, EVENT_KINDS, State
from noust.monitor.watch_domains import STAGES, DomainExpiryWatch, stage_of
from noust.monitor.watches import run_due
from tests.support.monitor_rig import NOW, Rig

DAY = 86400.0


def registration(domain: str, days: float, *, status: str = OK) -> Registration:
    when = datetime.fromtimestamp(NOW, timezone.utc)
    return Registration(
        domain=domain,
        status=status,
        expires_at=(when + timedelta(days=days)).isoformat(timespec="seconds")
        if status == OK
        else None,
        rdap_base="https://rdap.verisign.com/com/v1/",
        checked_at=when.isoformat(timespec="seconds"),
        next_check_at=(when + timedelta(days=1)).isoformat(timespec="seconds"),
    )


class Registry:
    """What the registries say, which names are served, and what the manager was asked."""

    def __init__(self, *registrations: Registration) -> None:
        self.registrations = list(registrations)
        self.served = [r.domain for r in registrations]
        self.checked: list[list[str]] = []
        self.forgotten: list[list[str]] = []

    def check_names(self, names: list[str], *, force: bool = False) -> list[Registration]:
        self.checked.append(list(names))
        return list(self.registrations)

    def forget_unserved(self, served: list[str]) -> None:
        self.forgotten.append(list(served))

    @property
    def records(self) -> Registry:
        return self

    def all(self) -> list[Registration]:
        return list(self.registrations)

    def names_of(self, registrable: str) -> list[str]:
        return [name for name in self.served if name.endswith(registrable)]


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    instance = Rig(tmp_path, monitor__domain_expiry=True)
    yield instance
    instance.close()


def watch(registry: Registry) -> DomainExpiryWatch:
    return DomainExpiryWatch(
        manager=lambda services: registry, names=lambda services: registry.served
    )


class TestOffUntilItIsTurnedOn:
    def test_it_is_off_by_default(self) -> None:
        assert DEFAULT_CONFIG["monitor"]["domain_expiry"] is False

    def test_it_is_off_under_the_ens_profile_too(self) -> None:
        """No profile turns a request to a third party on: it is the operator's to switch."""
        from noust.core.ens.profile import ENS_MEDIUM, STANDARD

        assert not hasattr(ENS_MEDIUM, "domain_expiry") and not hasattr(STANDARD, "domain_expiry")
        assert DEFAULT_CONFIG["monitor"]["domain_expiry"] is False

    def test_a_watch_with_the_switch_off_runs_nothing(self, tmp_path: Path) -> None:
        off = Rig(tmp_path)
        try:
            registry = Registry(registration("example.com", 3))

            ran = run_due([watch(registry)], off.services)

            assert ran == [] and registry.checked == []
        finally:
            off.close()

    def test_a_watch_with_the_switch_on_runs(self, rig: Rig) -> None:
        registry = Registry(registration("example.com", 90))

        assert run_due([watch(registry)], rig.services) == ["domain_expiry"]
        assert registry.checked == [["example.com"]]

    def test_it_looks_every_few_hours_and_the_manager_keeps_it_to_a_question_a_day(
        self, rig: Rig
    ) -> None:
        registry = Registry(registration("example.com", 90))
        w = watch(registry)
        run_due([w], rig.services)

        rig.now += 3 * 3600
        assert run_due([w], rig.services) == []
        rig.now += 4 * 3600
        assert run_due([w], rig.services) == ["domain_expiry"]


class TestTheStages:
    @pytest.mark.parametrize(
        ("days", "stage"),
        [(400, None), (31, None), (30, "30"), (8, "30"), (7, "7"), (0, "7"), (-1, "expired")],
    )
    def test_a_registration_is_at_30_days_at_7_days_or_expired(
        self, days: int, stage: str | None
    ) -> None:
        assert stage_of(days) == stage

    def test_the_stages_come_in_order(self) -> None:
        assert STAGES == ("30", "7", "expired")


class TestWhatIsSaid:
    def test_a_registration_far_from_its_end_says_nothing(self, rig: Rig) -> None:
        watch(Registry(registration("example.com", 200))).check(rig.services)

        assert rig.sent == []

    def test_30_days_out_it_is_said_once(self, rig: Rig) -> None:
        w = watch(Registry(registration("example.com", 29)))

        w.check(rig.services)
        w.check(rig.services)

        [note] = rig.sent
        assert (note.kind, note.code, note.state) == (
            "domain_expiring",
            "domain.expiring",
            State.WARNING,
        )
        assert note.subject == "example.com"
        assert "29 days" in note.summary

    def test_each_stage_is_said_in_turn(self, rig: Rig) -> None:
        registry = Registry(registration("example.com", 29))
        w = watch(registry)
        w.check(rig.services)

        registry.registrations = [registration("example.com", 6)]
        w.check(rig.services)
        registry.registrations = [registration("example.com", 2)]
        w.check(rig.services)
        registry.registrations = [registration("example.com", -1)]
        w.check(rig.services)

        assert rig.codes() == ["domain.expiring", "domain.expiring", "domain.expired"]
        assert rig.sent[-1].kind == "domain_expired"
        assert rig.sent[-1].state == State.FAILED

    def test_a_registration_found_at_7_days_is_said_without_the_30_day_message(
        self, rig: Rig
    ) -> None:
        watch(Registry(registration("example.com", 5))).check(rig.services)

        assert rig.codes() == ["domain.expiring"]
        assert "5 days" in rig.sent[0].summary

    def test_a_renewed_registration_is_news_again_when_it_comes_close(self, rig: Rig) -> None:
        registry = Registry(registration("example.com", 20))
        w = watch(registry)
        w.check(rig.services)

        registry.registrations = [registration("example.com", 400)]
        w.check(rig.services)
        registry.registrations = [registration("example.com", 20)]
        w.check(rig.services)

        assert rig.codes() == ["domain.expiring", "domain.expiring"]

    def test_each_domain_is_judged_on_its_own(self, rig: Rig) -> None:
        w = watch(Registry(registration("a.example.com", 20), registration("b.example.org", 200)))

        w.check(rig.services)

        assert [n.subject for n in rig.sent] == ["a.example.com"]

    def test_the_names_that_are_served_under_it_are_listed(self, rig: Rig) -> None:
        registry = Registry(registration("example.com", 20))
        registry.served = ["www.example.com", "shop.example.com", "other.example.org"]

        watch(registry).check(rig.services)

        facts = {f.key: f.value for f in rig.sent[0].facts}
        assert facts["domain_names"] == "www.example.com, shop.example.com"

    def test_a_registry_that_publishes_nothing_or_cannot_be_asked_is_not_news(
        self, rig: Rig
    ) -> None:
        w = watch(
            Registry(
                registration("example.es", 0, status=NO_RDAP),
                registration("example.org", 0, status=ERROR),
            )
        )

        w.check(rig.services)

        assert rig.sent == []

    def test_a_domain_nobody_serves_any_more_is_forgotten(self, rig: Rig) -> None:
        registry = Registry(registration("example.com", 20))
        w = watch(registry)
        w.check(rig.services)
        assert rig.services.state.items("domain_expiry") != {}

        registry.registrations = []
        registry.served = []
        w.check(rig.services)

        assert rig.services.state.items("domain_expiry") == {}
        assert registry.forgotten[-1] == []

    def test_a_manager_that_cannot_keep_its_records_does_not_stop_the_monitor(
        self, rig: Rig
    ) -> None:
        from noust.core.exceptions import ValidationError

        class Broken(Registry):
            def check_names(self, names: list[str], *, force: bool = False) -> list[Registration]:
                raise ValidationError("not a status")

        assert run_due([watch(Broken())], rig.services) == []


class TestTheMessage:
    def test_it_says_which_domain_when_and_how_to_see_them_all(self, rig: Rig) -> None:
        note = compose_domain_registration(
            "example.com",
            "2026-11-30",
            20,
            rig.context,
            names=["www.example.com"],
            registry="rdap.verisign.com",
        )

        assert (note.kind, note.code) == ("domain_expiring", "domain.expiring")
        facts = {f.key: f.value for f in note.facts}
        assert facts["domain_names"] == "www.example.com"
        assert facts["domain_registry"] == "rdap.verisign.com"
        assert note.command is not None and note.command.value == "noust domain expiry"

    def test_the_day_it_expires_and_after_it_are_said_as_such(self, rig: Rig) -> None:
        today = compose_domain_registration("example.com", "2026-11-10", 0, rig.context)
        gone = compose_domain_registration("example.com", "2026-11-07", -3, rig.context)

        assert (today.kind, today.code) == ("domain_expiring", "domain.expiring")
        assert "today" in today.summary
        assert (gone.kind, gone.code, gone.state) == (
            "domain_expired",
            "domain.expired",
            State.FAILED,
        )
        assert "3 days ago" in gone.summary

    @pytest.mark.parametrize("locale", ["en", "es"])
    def test_everything_is_said_in_both_languages_with_nothing_left_unfilled(
        self, locale: str
    ) -> None:
        for key in (
            "title.domain.expiring",
            "title.domain.expired",
            "summary.domain.expiring",
            "summary.domain.expiring_today",
            "summary.domain.expired",
            "fact.domain_names",
            "fact.domain_registry",
            "fact.domain_expiry",
        ):
            assert MESSAGES[key][locale].strip(), (key, locale)
        context = NotificationContext(locale=locale, server="web-1")  # type: ignore[arg-type]
        for days in (20, 1, 0, -3):
            note = compose_domain_registration("example.com", "2026-11-30", days, context)
            assert "{" not in note.summary and "{" not in note.title

    def test_both_are_events_an_operator_can_switch_off(self) -> None:
        for kind in ("domain_expiring", "domain_expired"):
            assert kind in EVENT_KINDS and kind in ALL_KINDS
            assert DEFAULT_CONFIG["notifications"]["events"][kind] is True


class TestTheMonitorRunsIt:
    def test_it_is_one_of_the_watches(self) -> None:
        from noust.core.runner import FakeRunner
        from noust.monitor.process_monitor import ProcessMonitor

        monitor = ProcessMonitor(verbose=False, runner=FakeRunner())

        assert "domain_expiry" in [w.name for w in monitor._watches]
