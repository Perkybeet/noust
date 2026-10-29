# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A server tells its operator when the central that manages it goes quiet.

Every server delivers its own notifications, so it keeps telling its operator about itself when
the central is down or the line between them is cut. What it cannot tell without help is that the
central is gone, because the central is who tells it things. A central whose monitor runs probes
every server every half minute and says so; when that stops for longer than a quarter of an hour
(``notifications.central_silent_minutes``) the server says so through its own channels, once, and
says when the central calls again. What is pinned: the wording of both messages in both languages;
that only a central that said it keeps watch is missed; the limit and that it is the operator's to
change or to switch off; that it is said once per silence and not at every look; that a central
the server forgot is not missed; and that the watch is one the monitor runs.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.messages import MESSAGES
from noust.core.notifications.composers_watch import compose_central
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import ALL_KINDS, EVENT_KINDS, State
from noust.fleet.central_calls import CentralCalls
from noust.monitor.watch_central import CentralWatch
from tests.support.monitor_rig import Rig

MINUTE = 60.0


@pytest.fixture(autouse=True)
def no_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """A watch that is not given the tokens judges by the calls, as a server without a console."""
    from noust.core.exceptions import DependencyError

    def missing() -> object:
        raise DependencyError("the console is not installed")

    monkeypatch.setattr("noust.core.web_gate.token_manager", missing)


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    instance = Rig(tmp_path)
    yield instance
    instance.close()


def calls(rig: Rig) -> CentralCalls:
    return CentralCalls(rig.store, clock=lambda: rig.now)


def quiet_for(rig: Rig, minutes: float) -> None:
    rig.now += minutes * MINUTE


class TestTheMessages:
    def test_a_central_that_stopped_calling_is_a_warning_under_its_own_switch(
        self, rig: Rig
    ) -> None:
        note = compose_central(
            "nas", True, rig.context, last_heard=rig.now - 20 * MINUTE, silent_for_s=20 * MINUTE
        )

        assert (note.kind, note.code, note.state) == (
            "central_silent",
            "central.silent",
            State.WARNING,
        )
        assert note.subject == "nas"
        assert "20" in note.summary
        facts = {f.key: f.value for f in note.facts}
        assert facts["server"] == "web-1", "said by the server, about the central"
        assert "last_heard" in facts and "silent_for" not in facts

    def test_the_way_back_closes_it(self, rig: Rig) -> None:
        note = compose_central("nas", False, rig.context, silent_for_s=25 * MINUTE)

        assert (note.kind, note.code, note.state) == ("central_silent", "central.heard", State.OK)
        assert {f.key for f in note.facts} >= {"server", "silent_for"}

    @pytest.mark.parametrize("locale", ["en", "es"])
    def test_both_are_said_in_both_languages_and_say_what_keeps_working(self, locale: str) -> None:
        for key in (
            "title.central.silent",
            "title.central.heard",
            "summary.central.silent",
            "summary.central.heard",
            "fact.last_heard",
            "fact.silent_for",
        ):
            assert MESSAGES[key][locale].strip(), (key, locale)
        context = NotificationContext(locale=locale, server="web-1")  # type: ignore[arg-type]
        text = compose_central("nas", True, context, silent_for_s=900).summary
        assert "{" not in text

    def test_it_is_an_event_an_operator_can_switch_off(self) -> None:
        from noust.core.config import DEFAULT_CONFIG

        assert "central_silent" in EVENT_KINDS and "central_silent" in ALL_KINDS
        assert DEFAULT_CONFIG["notifications"]["events"]["central_silent"] is True


class TestWhoIsMissed:
    def test_a_central_that_said_it_keeps_watch_and_stopped_is_missed_after_a_quarter_of_an_hour(
        self, rig: Rig
    ) -> None:
        calls(rig).note("nas", heartbeat_s=30)
        quiet_for(rig, 14)
        CentralWatch().check(rig.services)
        assert rig.sent == []

        quiet_for(rig, 2)
        CentralWatch().check(rig.services)

        [note] = rig.sent
        assert note.code == "central.silent"
        assert note.subject == "nas"

    def test_a_central_that_never_said_it_keeps_watch_is_not_missed(self, rig: Rig) -> None:
        # Its console probes only while somebody has it open: nobody promised to call again.
        calls(rig).note("nas")
        quiet_for(rig, 600)

        CentralWatch().check(rig.services)

        assert rig.sent == []

    def test_a_central_that_keeps_calling_is_not_missed(self, rig: Rig) -> None:
        note = calls(rig)
        note.note("nas", heartbeat_s=30)
        for _ in range(60):
            quiet_for(rig, 2)
            note.note("nas", heartbeat_s=30)
            CentralWatch().check(rig.services)

        assert rig.sent == []

    def test_each_central_is_judged_by_its_own_calls(self, rig: Rig) -> None:
        note = calls(rig)
        note.note("nas", heartbeat_s=30)
        note.note("backup", heartbeat_s=30)
        quiet_for(rig, 10)
        note.note("backup", heartbeat_s=30)
        quiet_for(rig, 10)

        CentralWatch().check(rig.services)

        assert [m.subject for m in rig.sent] == ["nas"]

    def test_the_limit_is_never_shorter_than_a_few_of_the_calls_it_was_promised(
        self, rig: Rig
    ) -> None:
        # A central that says it calls once an hour is not silent after sixteen minutes.
        calls(rig).note("nas", heartbeat_s=3600)
        quiet_for(rig, 16)

        CentralWatch().check(rig.services)

        assert rig.sent == []
        quiet_for(rig, 3 * 60)
        CentralWatch().check(rig.services)
        assert len(rig.sent) == 1


class TestACentralThatIsNoLongerLetIn:
    def live(self, *names: str) -> list[dict[str, object]]:
        return [{"name": name, "revoked_at": None, "expires_at": None} for name in names]

    def test_a_central_whose_token_was_revoked_is_not_missed(self, rig: Rig) -> None:
        calls(rig).note("nas", heartbeat_s=30)
        quiet_for(rig, 20)
        tokens = [{"name": "fleet-nas", "revoked_at": rig.now - 600, "expires_at": None}]

        CentralWatch(tokens=lambda: tokens).check(rig.services)

        assert rig.sent == []

    def test_a_central_whose_key_was_rotated_is_judged_by_the_token_that_replaced_it(
        self, rig: Rig
    ) -> None:
        calls(rig).note("nas", heartbeat_s=30)
        quiet_for(rig, 20)
        tokens = [
            {"name": "fleet-nas", "revoked_at": rig.now - 600, "expires_at": None},
            {"name": "fleet-nas.2", "revoked_at": None, "expires_at": None},
        ]

        CentralWatch(tokens=lambda: tokens).check(rig.services)

        assert rig.codes() == ["central.silent"]

    def test_a_token_that_expired_lets_nobody_in_either(self, rig: Rig) -> None:
        calls(rig).note("nas", heartbeat_s=30)
        quiet_for(rig, 20)
        tokens = [{"name": "fleet-nas", "revoked_at": None, "expires_at": rig.now - 1}]

        CentralWatch(tokens=lambda: tokens).check(rig.services)

        assert rig.sent == []

    def test_a_central_that_was_missed_and_then_revoked_is_forgotten_quietly(
        self, rig: Rig
    ) -> None:
        calls(rig).note("nas", heartbeat_s=30)
        quiet_for(rig, 20)
        CentralWatch(tokens=lambda: self.live("fleet-nas")).check(rig.services)
        assert rig.codes() == ["central.silent"]

        CentralWatch(tokens=lambda: []).check(rig.services)

        assert rig.codes() == ["central.silent"]
        assert rig.services.state.get_json("central", "nas") is None

    def test_a_server_that_cannot_list_its_tokens_judges_by_the_calls_it_noted(
        self, rig: Rig
    ) -> None:
        from noust.core.exceptions import DependencyError

        def cannot() -> list[dict[str, object]]:
            raise DependencyError("the console is not installed")

        calls(rig).note("nas", heartbeat_s=30)
        quiet_for(rig, 20)

        CentralWatch(tokens=cannot).check(rig.services)

        assert rig.codes() == ["central.silent"]


class TestOnceAndBack:
    def test_a_silence_is_said_once_and_not_at_every_look(self, rig: Rig) -> None:
        calls(rig).note("nas", heartbeat_s=30)
        quiet_for(rig, 20)
        watch = CentralWatch()

        for _ in range(5):
            watch.check(rig.services)
            quiet_for(rig, 1)

        assert rig.codes() == ["central.silent"]

    def test_when_it_calls_again_that_is_said_too_and_a_new_silence_is_a_new_message(
        self, rig: Rig
    ) -> None:
        note = calls(rig)
        note.note("nas", heartbeat_s=30)
        quiet_for(rig, 20)
        watch = CentralWatch()
        watch.check(rig.services)

        note.note("nas", heartbeat_s=30)
        watch.check(rig.services)
        quiet_for(rig, 20)
        watch.check(rig.services)

        assert rig.codes() == ["central.silent", "central.heard", "central.silent"]
        heard = rig.sent[1]
        assert any(f.key == "silent_for" for f in heard.facts)

    def test_a_restart_of_the_monitor_does_not_say_it_again(self, rig: Rig) -> None:
        calls(rig).note("nas", heartbeat_s=30)
        quiet_for(rig, 20)
        CentralWatch().check(rig.services)

        CentralWatch().check(rig.services)  # a new watch, as after the daemon restarted

        assert rig.codes() == ["central.silent"]

    def test_a_central_the_server_forgot_is_not_missed_and_not_welcomed_back(
        self, rig: Rig
    ) -> None:
        note = calls(rig)
        note.note("nas", heartbeat_s=30)
        quiet_for(rig, 20)
        watch = CentralWatch()
        watch.check(rig.services)

        note.forget("nas")
        quiet_for(rig, 5)
        watch.check(rig.services)

        assert rig.codes() == ["central.silent"]
        assert rig.services.state.get("central", "nas") is None


class TestTheLimit:
    def test_it_is_the_operators_to_change(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path, notifications__central_silent_minutes=5)
        try:
            calls(rig).note("nas", heartbeat_s=30)
            quiet_for(rig, 6)

            CentralWatch().check(rig.services)

            assert rig.codes() == ["central.silent"]
        finally:
            rig.close()

    def test_zero_switches_it_off(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path, notifications__central_silent_minutes=0)
        try:
            calls(rig).note("nas", heartbeat_s=30)
            quiet_for(rig, 600)

            assert CentralWatch().interval_s(rig.services) == 0
            CentralWatch().check(rig.services)
            assert rig.sent == []
        finally:
            rig.close()

    def test_the_default_is_a_quarter_of_an_hour_and_is_looked_at_every_minute(
        self, rig: Rig
    ) -> None:
        assert CentralWatch().interval_s(rig.services) == MINUTE

    def test_it_is_one_of_the_watches_the_monitor_runs(self) -> None:
        from noust.core.runner import FakeRunner
        from noust.monitor.process_monitor import ProcessMonitor

        monitor = ProcessMonitor(verbose=False, runner=FakeRunner())

        assert "central" in [watch.name for watch in monitor._watches]
