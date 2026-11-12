# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A change to the firewall or to SSH that nobody confirmed and was undone is said through the channels (item 138).

The change is undone by a timer, in a process of its own and often when nobody is looking, and
until now the only trace was an audit entry. Somebody who opened a port, saw nothing happen and
went to bed found it closed again. The undoing publishes one notification, of its own kind so a
channel can switch it off, and only when it is the timer (or the overdue sweep) that undid it:
a person who asks for the undo knows, and a change that was confirmed first has nothing to say.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.config import DEFAULT_CONFIG
from noust.core.messages import MESSAGES
from noust.core.notifications.composers_access import compose_access_change_reverted
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import ALL_KINDS, EVENT_KINDS, Notification, State
from noust.managers.server import security_notifications
from noust.managers.server.security_pending import ChangeLedger, PendingChange
from tests.support.server_security_support import NOW, FakeHost, FakeSshd

APPLIED = NOW - 600.0


def _context(locale: str = "en") -> NotificationContext:
    return NotificationContext(
        server="shop-1", locale=locale, public_url="https://noust.example.com"
    )  # type: ignore[arg-type]


class TestTheNotification:
    def test_a_firewall_change_says_what_was_undone_and_who_made_it(self) -> None:
        note = compose_access_change_reverted(
            "firewall",
            "Allow 5432 from 10.0.4.12",
            _context(),
            actor="yago",
            applied_at=APPLIED,
            window_s=600,
            output="Rule deleted",
        )

        assert note.kind == "access_change_reverted"
        assert note.code == "access.firewall_reverted"
        assert note.state is State.WARNING
        assert "Allow 5432 from 10.0.4.12" in note.summary
        assert "nobody confirmed" in note.summary
        assert "10 minutes" in note.summary
        facts = {fact.key: fact.value for fact in note.facts}
        assert facts["server"] == "shop-1"
        assert facts["change_by"] == "yago"
        # What the undo printed is the system's own words: verbatim, not in a sentence of Noust's.
        assert note.excerpt is not None
        assert "Rule deleted" in "\n".join(note.excerpt.lines)

    def test_an_ssh_change_has_its_own_code_and_leads_to_the_ssh_page(self) -> None:
        note = compose_access_change_reverted(
            "sshd",
            "Disable password logins",
            _context(),
            actor="ana",
            applied_at=APPLIED,
            window_s=300,
        )

        assert note.code == "access.ssh_reverted"
        assert "SSH" in note.title
        assert "5 minutes" in note.summary
        assert note.links and note.links[0].url.endswith("/security/ssh")

    def test_it_is_said_in_spanish_when_the_server_speaks_it(self) -> None:
        note = compose_access_change_reverted(
            "firewall", "Allow 8443", _context("es"), actor="yago", applied_at=APPLIED, window_s=60
        )

        assert "cortafuegos" in note.title.lower()
        assert "nadie lo confirmó" in note.summary
        assert "1 minuto" in note.summary

    def test_a_change_with_no_undo_output_has_no_excerpt(self) -> None:
        note = compose_access_change_reverted(
            "firewall", "Allow 8443", _context(), actor="yago", applied_at=APPLIED, window_s=60
        )

        assert note.excerpt is None

    def test_the_kind_is_a_switch_that_ships_on(self) -> None:
        assert "access_change_reverted" in EVENT_KINDS
        assert "access_change_reverted" in ALL_KINDS
        assert DEFAULT_CONFIG["notifications"]["events"]["access_change_reverted"] is True

    @pytest.mark.parametrize("code", ["access.firewall_reverted", "access.ssh_reverted"])
    def test_every_code_has_its_title_and_summary_in_both_languages(self, code: str) -> None:
        for key in (f"title.{code}", f"summary.{code}"):
            assert set(MESSAGES[key]) == {"en", "es"}


class Recorder:
    """What the notifier was handed."""

    def __init__(self) -> None:
        self.sent: list[Notification] = []
        self.fail = False

    def __call__(self, config: Any) -> Recorder:
        return self

    def notify(self, event: Notification) -> None:
        if self.fail:
            raise OSError("the channel is down")
        self.sent.append(event)


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    recording = Recorder()
    monkeypatch.setattr(security_notifications, "Notifier", recording)
    return recording


@pytest.fixture
def host(tmp_path: Path) -> FakeHost:
    return FakeHost(tmp_path / "root")


@pytest.fixture
def ledger(tmp_path: Path, host: FakeHost) -> ChangeLedger:
    return ChangeLedger(
        tmp_path / "changes",
        runner=FakeSshd(host),
        host=host.paths,
        clock=lambda: NOW,
        python="/usr/bin/python3",
    )


def _pending(
    ledger: ChangeLedger, *, kind: str = "firewall", title: str = "Allow 8443"
) -> PendingChange:
    change = PendingChange(
        id="a1b2c3d4",
        kind=kind,
        title=title,
        actor="yago",
        applied_at=APPLIED,
        expires_at=NOW,
    )
    ledger.save(change)
    return change


class TestTheLedgerSaysIt:
    def test_the_timer_undoing_a_change_nobody_confirmed_publishes_it_once(
        self, ledger: ChangeLedger, recorder: Recorder
    ) -> None:
        change = _pending(ledger)

        ledger.revert(change.id, by="timer", expired=True)
        # The timer firing a second time finds it already resolved and says nothing more.
        ledger.revert(change.id, by="timer", expired=True)

        [note] = recorder.sent
        assert note.code == "access.firewall_reverted"
        assert "Allow 8443" in note.summary

    def test_the_overdue_sweep_says_it_too(self, ledger: ChangeLedger, recorder: Recorder) -> None:
        _pending(ledger, kind="sshd", title="Disable password logins")
        late = ChangeLedger(
            ledger.directory,
            runner=ledger.runner,
            host=ledger.host,
            clock=lambda: NOW + 3600,
            python="/usr/bin/python3",
        )

        undone = late.revert_overdue()

        assert [change.status for change in undone] == ["expired"]
        assert [note.code for note in recorder.sent] == ["access.ssh_reverted"]

    def test_a_person_asking_for_the_undo_gets_no_notification(
        self, ledger: ChangeLedger, recorder: Recorder
    ) -> None:
        change = _pending(ledger)

        ledger.revert(change.id, by="ana")

        assert recorder.sent == []

    def test_a_change_confirmed_before_the_timer_fired_says_nothing(
        self, ledger: ChangeLedger, recorder: Recorder
    ) -> None:
        change = _pending(ledger)
        change.status = "confirmed"
        ledger.save(change)

        ledger.revert(change.id, by="timer", expired=True)

        assert recorder.sent == []

    def test_a_channel_that_fails_never_stops_the_undoing(
        self, ledger: ChangeLedger, recorder: Recorder
    ) -> None:
        recorder.fail = True
        change = _pending(ledger)

        undone = ledger.revert(change.id, by="timer", expired=True)

        assert undone.status == "expired"

    def test_the_text_of_the_change_is_what_the_record_holds(
        self, ledger: ChangeLedger, recorder: Recorder
    ) -> None:
        change = _pending(ledger, title="Allow 5432 from 10.0.4.12")

        ledger.revert(change.id, by="timer", expired=True)

        [note] = recorder.sent
        assert {fact.key: fact.value for fact in note.facts}[
            "change"
        ] == "Allow 5432 from 10.0.4.12"
