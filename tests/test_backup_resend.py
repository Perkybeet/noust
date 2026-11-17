# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What was made and not sent is sent again, by itself (item 201).

A pass looks at what is owed and tries what is due: a copy at a time, oldest first, one destination
that does not answer ending the tries to it for that pass (the rest would only wait the same). The
next scheduled run of what a copy belongs to sends its owed copies first, without waiting for the
clock. A copy that was deleted, or a destination that left the schedule, is not owed to anybody.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core import timeutil
from noust.core.config import Config
from noust.core.exceptions import BackupError
from noust.core.notification_state import NotificationState
from noust.core.notifications.model import Notification
from noust.core.pending_sends import PendingSend, PendingSends
from noust.core.store import NoustStore
from noust.managers.backup_resend import Resender
from noust.managers.backup_sending import Sender
from tests.support.notifier import config  # noqa: F401  (pytest resolves fixtures by name)

# ruff: noqa: F811

START = datetime(2026, 11, 17, 1, 0, tzinfo=timezone.utc)


class Pushers:
    """A pusher of every kind that records what it was asked to send and answers as told."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.failing: set[str] = set()
        self.gone: set[str] = set()
        self.dropped: set[str] = set()

    def prepare(self, row: PendingSend) -> Callable[[], dict[str, Any]] | None:
        if row.item_id in self.gone or row.destination in self.dropped:
            return None

        def push() -> dict[str, Any]:
            if row.destination in self.failing:
                raise BackupError("Failed to upload", details="connection refused")
            self.sent.append((row.kind, row.item_id, row.destination))
            return {"destination": row.destination}

        return push


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    NoustStore._instance = instance
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


class Rig:
    def __init__(self, store: NoustStore, config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
        self.now = START
        monkeypatch.setattr(timeutil, "utc_now", lambda: self.now)
        self.heard: list[Notification] = []
        self.pending = PendingSends(store)
        self.sender = Sender(
            pending=self.pending,
            config=config,
            notify=self.heard.append,
            state=NotificationState(store, clock=lambda: self.now.timestamp()),
            kept_in="/var/backups/noust",
        )
        self.pushers = Pushers()
        self.resender = Resender(
            sender=self.sender,
            pending=self.pending,
            pushers=dict.fromkeys(("app", "volume", "database", "noust"), self.pushers),
        )

    def owe(
        self, item: str, destination: str = "nas", kind: str = "app", owner: str = "a.example.com"
    ) -> None:
        self.pending.defer(
            kind,
            item,
            destination,
            owner=owner,
            copy_created_at=self.now,
            reason="i/o timeout",
            permanent=False,
            failure_id=None,
            now=self.now,
            next_attempt_at=self.now + timedelta(minutes=15),
        )

    def later(self, **delta: float) -> None:
        self.now = self.now + timedelta(**delta)


@pytest.fixture
def rig(store: NoustStore, config: Config, monkeypatch: pytest.MonkeyPatch) -> Rig:
    return Rig(store, config, monkeypatch)


class TestAPass:
    def test_sends_what_is_due_and_nothing_else(self, rig: Rig) -> None:
        rig.owe("early")
        rig.later(minutes=20)
        rig.owe("late")

        report = rig.resender.run()

        assert [item for _, item, _ in rig.pushers.sent] == ["early"]
        assert report.sent == 1
        assert [row.item_id for row in rig.pending.all()] == ["late"]

    def test_with_force_it_sends_what_is_not_due_yet(self, rig: Rig) -> None:
        rig.owe("early")

        report = rig.resender.run(force=True)

        assert report.sent == 1 and rig.pending.all() == []

    def test_the_oldest_goes_first(self, rig: Rig) -> None:
        rig.owe("one")
        rig.later(minutes=1)
        rig.owe("two")
        rig.later(hours=1)

        rig.resender.run()

        assert [item for _, item, _ in rig.pushers.sent] == ["one", "two"]

    def test_a_destination_that_does_not_answer_ends_the_tries_to_it_for_this_pass(
        self, rig: Rig
    ) -> None:
        for item in ("one", "two", "three"):
            rig.owe(item, destination="nas")
        rig.owe("elsewhere", destination="cloud")
        rig.pushers.failing = {"nas"}
        rig.later(minutes=20)

        report = rig.resender.run()

        # One try to the destination that is down, not three; the other destination is still tried.
        assert [item for _, item, _ in rig.pushers.sent] == ["elsewhere"]
        assert report.failed == 1 and report.skipped == 2 and report.sent == 1
        owed = {row.item_id: row for row in rig.pending.all()}
        assert owed["one"].attempts == 1
        assert owed["two"].attempts == 0 and owed["three"].attempts == 0

    def test_what_is_still_owed_is_said_again_when_the_window_has_passed(self, rig: Rig) -> None:
        rig.owe("one")
        rig.pushers.failing = {"nas"}
        rig.later(minutes=20)
        rig.resender.run()
        assert len(rig.heard) == 1

        rig.later(hours=25)
        rig.resender.run(force=True)

        assert [note.code for note in rig.heard] == ["backup.sends_failed", "backup.sends_failed"]

    def test_a_pass_that_sends_everything_says_so_once_when_something_was_said(
        self, rig: Rig
    ) -> None:
        rig.owe("one")
        rig.owe("two")
        rig.pushers.failing = {"nas"}
        rig.later(minutes=20)
        rig.resender.run()
        rig.pushers.failing = set()
        rig.later(hours=2)

        rig.resender.run()

        assert [note.code for note in rig.heard] == [
            "backup.sends_failed",
            "backup.sends_recovered",
        ]

    def test_a_copy_that_was_deleted_is_not_owed_any_more(self, rig: Rig) -> None:
        rig.owe("gone")
        rig.pushers.gone = {"gone"}
        rig.later(minutes=20)

        report = rig.resender.run()

        assert report.dropped == 1 and rig.pending.all() == []

    def test_a_destination_that_left_the_schedule_is_not_owed_any_more(self, rig: Rig) -> None:
        rig.owe("one", destination="old")
        rig.pushers.dropped = {"old"}
        rig.later(minutes=20)

        report = rig.resender.run()

        assert report.dropped == 1 and rig.pending.all() == []
        assert rig.pushers.sent == []


class TestWhatNobodyOwesAnyMore:
    def test_a_destination_whose_last_owed_copy_was_forgotten_is_not_left_said(
        self, rig: Rig
    ) -> None:
        rig.owe("one")
        rig.pushers.failing = {"nas"}
        rig.later(minutes=20)
        rig.resender.run()
        assert [note.code for note in rig.heard] == ["backup.sends_failed"]
        rig.pushers.failing = set()
        rig.pushers.gone = {"one"}
        rig.later(hours=2)

        rig.resender.run()
        rig.owe("two")
        rig.pushers.failing = {"nas"}
        rig.pushers.gone = set()
        rig.later(hours=1)
        rig.resender.run()

        # Nothing was owed in between, so the trouble that followed is a new one and not the old one.
        assert [note.code for note in rig.heard] == ["backup.sends_failed", "backup.sends_failed"]


class TestACopyThatCannotBeLookedAt:
    def test_one_that_cannot_be_prepared_stays_owed_and_the_others_still_go(self, rig: Rig) -> None:
        rig.owe("broken")
        rig.owe("fine")
        rig.later(minutes=20)
        prepare = rig.pushers.prepare

        def picky(row: PendingSend) -> Callable[[], dict[str, Any]] | None:
            if row.item_id == "broken":
                raise BackupError("The sidecar of this backup cannot be read")
            return prepare(row)

        rig.pushers.prepare = picky  # type: ignore[method-assign]

        report = rig.resender.run()

        assert report.sent == 1 and report.failed == 1
        assert [row.item_id for row in rig.pending.all()] == ["broken"]


class TestTheNextRun:
    def test_sends_what_its_own_thing_owes_without_waiting_for_the_clock(self, rig: Rig) -> None:
        rig.owe("mine", owner="a.example.com")
        rig.owe("not-mine", owner="b.example.com")

        report = rig.resender.run(only=("app", "a.example.com"))

        assert [item for _, item, _ in rig.pushers.sent] == ["mine"]
        assert report.sent == 1
        assert [row.item_id for row in rig.pending.all()] == ["not-mine"]

    def test_a_run_whose_destination_is_down_goes_on_and_takes_its_new_copy(self, rig: Rig) -> None:
        rig.owe("mine", owner="a.example.com")
        rig.pushers.failing = {"nas"}

        report = rig.resender.run(only=("app", "a.example.com"))

        # It did not raise: what could not be sent stays owed and the run goes on.
        assert report.failed == 1
        assert [row.item_id for row in rig.pending.all()] == ["mine"]


def test_the_report_says_what_it_did_as_plain_data(rig: Rig) -> None:
    rig.owe("one")

    report = rig.resender.run(force=True)

    assert report.to_dict() == {"sent": 1, "failed": 0, "skipped": 0, "dropped": 0, "owed": 0}
