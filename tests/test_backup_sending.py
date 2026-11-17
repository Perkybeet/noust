# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What becomes of a send that did not arrive, decided in one place (item 201).

A send that fails in a scheduled run is not a failure to say: the copy is kept, it is owed to its
destination, and it is tried again (after quarter of an hour, an hour, four hours and then every
day, and by the next scheduled run first). It is said when it matters: when the retry fails too, or
at once when what was refused is of the kind waiting does not mend; and said once for a destination,
not once for each application, with a recovery that answers it when everything has arrived.
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
from noust.core.notifications import grouping
from noust.core.notifications.model import Notification, State
from noust.core.pending_sends import PendingSends
from noust.core.store import NoustStore
from noust.managers.backup_failures import BackupFailureLog
from noust.managers.backup_sending import (
    DEFERRED_DELAYS,
    LATER_DELAY,
    Sender,
    SendOutcome,
)
from tests.support.notifier import config  # noqa: F401  (pytest resolves fixtures by name)

# ruff: noqa: F811

START = datetime(2026, 11, 17, 1, 0, tzinfo=timezone.utc)
TIMED_OUT = "Failed to copyto: sftp: write: i/o timeout: connection lost"


class Rig:
    """A sender over a store of its own, a clock the test moves and a notifier that records."""

    def __init__(self, store: NoustStore, config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
        self.now = START
        self.store = store
        self.heard: list[Notification] = []
        monkeypatch.setattr(timeutil, "utc_now", lambda: self.now)
        self.pending = PendingSends(store)
        self.sender = Sender(
            pending=self.pending,
            config=config,
            notify=self.heard.append,
            state=NotificationState(store, clock=lambda: self.now.timestamp()),
            kept_in="/var/backups/noust",
        )

    def later(self, **delta: float) -> None:
        self.now = self.now + timedelta(**delta)

    def send(
        self,
        owner: str = "shop.example.com",
        destination: str = "nas",
        *,
        text: str | None = TIMED_OUT,
    ) -> SendOutcome:
        """One send of a copy of ``owner``'s, failing with ``text`` or going through when None."""
        item = f"{owner.replace('.', '-')}_{self.now:%Y%m%d_%H%M%S}"

        def push() -> dict[str, Any]:
            if text is not None:
                raise BackupError(f"Failed to upload {item} to {destination}", details=text)
            return {"destination": destination, "uploaded": [item]}

        return self.sender.send(
            "app", item, owner, destination, copy_created_at=self.now, push=push
        )

    def retry(self, row: Any, *, text: str | None = TIMED_OUT) -> SendOutcome:
        """The deferred try of a row, failing with ``text`` or going through when None."""

        def push() -> dict[str, Any]:
            if text is not None:
                raise BackupError(
                    f"Failed to upload {row.item_id} to {row.destination}", details=text
                )
            return {"destination": row.destination, "uploaded": [row.item_id]}

        return self.sender.retry(row, push)

    @property
    def codes(self) -> list[str]:
        return [note.code for note in self.heard]


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


@pytest.fixture
def rig(store: NoustStore, config: Config, monkeypatch: pytest.MonkeyPatch) -> Rig:
    return Rig(store, config, monkeypatch)


class TestAFailedSend:
    def test_is_kept_as_owed_and_is_not_said(self, rig: Rig) -> None:
        outcome = rig.send()

        assert outcome.ok is False and outcome.deferred is True
        (row,) = rig.pending.all()
        assert (row.owner, row.destination, row.attempts) == ("shop.example.com", "nas", 0)
        assert "i/o timeout" in row.reason
        assert rig.heard == []

    def test_is_tried_again_in_a_quarter_of_an_hour(self, rig: Rig) -> None:
        rig.send()

        (row,) = rig.pending.all()
        due = datetime.fromisoformat(row.next_attempt_at)
        assert due - START == timedelta(minutes=15)
        assert rig.pending.due(START + timedelta(minutes=14)) == []
        assert len(rig.pending.due(START + timedelta(minutes=15))) == 1

    def test_keeps_the_whole_of_what_the_destination_said_for_whoever_asks(self, rig: Rig) -> None:
        rig.send()

        (row,) = rig.pending.all()
        assert row.failure_id is not None
        record = BackupFailureLog(rig.store).get(row.failure_id)
        assert record is not None and record.kind == "upload" and record.destination == "nas"
        assert "i/o timeout" in record.output

    def test_the_outcome_says_what_a_run_reports(self, rig: Rig) -> None:
        outcome = rig.send()

        entry = outcome.entry()
        assert entry["ok"] is False and entry["pending"] is True
        assert "i/o timeout" in str(entry["error"])
        assert entry["next_attempt_at"] == (START + timedelta(minutes=15)).isoformat(
            timespec="seconds"
        )

    def test_the_delays_are_a_quarter_of_an_hour_an_hour_four_hours_and_then_every_day(
        self, rig: Rig
    ) -> None:
        assert DEFERRED_DELAYS == (15 * 60, 60 * 60, 4 * 60 * 60)
        assert LATER_DELAY == 24 * 60 * 60

        rig.send()
        waits = []
        for _ in range(5):
            (row,) = rig.pending.all()
            rig.now = datetime.fromisoformat(row.next_attempt_at)
            again = rig.retry(row)
            assert again.next_attempt_at is not None
            waits.append(again.next_attempt_at - rig.now)

        assert waits == [
            timedelta(hours=1),
            timedelta(hours=4),
            timedelta(days=1),
            timedelta(days=1),
            timedelta(days=1),
        ]

    def test_without_a_store_to_keep_it_in_it_is_said_at_once_as_before(
        self, rig: Rig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sqlite3

        def broken(*args: object, **kwargs: object) -> None:
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(PendingSends, "defer", broken)

        outcome = rig.send()

        assert outcome.ok is False and outcome.deferred is False
        assert rig.codes == ["backup.upload_failed"]


class TestWhatIsKept:
    def test_a_secret_in_what_the_destination_said_is_not_kept_nor_said(self, rig: Rig) -> None:
        rig.send(text="Failed to copyto: permission denied: password=hunter2-with-a-long-tail")

        (row,) = rig.pending.all()
        assert "hunter2-with-a-long-tail" not in row.reason
        (said,) = rig.heard
        assert said.excerpt is not None
        assert "hunter2-with-a-long-tail" not in " ".join(said.excerpt.lines)


class TestASendThatArrives:
    def test_ends_what_was_owed(self, rig: Rig) -> None:
        rig.send()
        (row,) = rig.pending.all()

        outcome = rig.retry(row, text=None)

        assert outcome.ok is True
        assert rig.pending.all() == []
        # Nobody was told it was owed, so nobody is told it arrived.
        assert rig.heard == []

    def test_a_send_that_never_failed_leaves_no_trace(self, rig: Rig) -> None:
        outcome = rig.send(text=None)

        assert outcome.ok is True and rig.pending.all() == []
        assert outcome.entry() == {"ok": True, **outcome.summary}


class TestWhenItIsSaid:
    def test_a_retry_that_fails_too_is_said_once_for_the_destination(self, rig: Rig) -> None:
        for index in range(9):
            rig.send(owner=f"app{index:02d}.example.com")
            rig.later(minutes=20)

        # Nothing was said while every send was waiting for its first retry.
        assert rig.heard == []

        rig.later(minutes=30)
        for row in rig.pending.due(rig.now):
            rig.retry(row)

        assert rig.codes == ["backup.sends_failed"]
        said = rig.heard[0]
        assert said.state is State.WARNING and said.kind == "backup_failed"
        assert said.subject == "nas"
        copies = next(fact for fact in said.facts if fact.key == "copies")
        assert copies.value.startswith("9")
        assert "app00.example.com" in copies.value

    def test_the_notice_says_what_was_tried_when_it_is_tried_next_and_where_the_copy_is(
        self, rig: Rig
    ) -> None:
        rig.send()
        rig.later(minutes=15)
        (row,) = rig.pending.all()
        rig.retry(row)

        (said,) = rig.heard
        facts = {fact.key: fact.value for fact in said.facts}
        assert facts["destination"] == "nas"
        assert "6" in facts["tried"]  # two rounds of three attempts
        assert "03:00" in facts["next_try"] or "02:15" in facts["next_try"]
        assert facts["kept_in"] == "/var/backups/noust"
        assert said.excerpt is not None and "i/o timeout" in " ".join(said.excerpt.lines)

    def test_a_refusal_that_waiting_does_not_mend_is_said_at_once(self, rig: Rig) -> None:
        rig.send(text="Failed to copyto: permission denied")

        assert rig.codes == ["backup.sends_failed"]

    def test_a_second_refusal_in_the_same_night_is_not_another_message(self, rig: Rig) -> None:
        rig.send(owner="one.example.com", text="Failed to copyto: permission denied")
        rig.later(hours=2)
        rig.send(owner="two.example.com", text="Failed to copyto: permission denied")

        assert rig.codes == ["backup.sends_failed"]
        assert all(row.noticed for row in rig.pending.all())

    def test_what_still_fails_a_day_later_is_said_again_with_everything_that_waits(
        self, rig: Rig
    ) -> None:
        rig.send(owner="one.example.com", text="Failed to copyto: permission denied")
        rig.later(hours=2)
        rig.send(owner="two.example.com", text="Failed to copyto: permission denied")
        rig.later(hours=25)

        rig.sender.remind("nas")

        assert rig.codes == ["backup.sends_failed", "backup.sends_failed"]
        copies = next(fact for fact in rig.heard[-1].facts if fact.key == "copies")
        assert copies.value.startswith("2")

    def test_another_destination_is_another_message(self, rig: Rig) -> None:
        rig.send(destination="nas", text="Failed to copyto: permission denied")
        rig.send(destination="cloud", text="Failed to copyto: permission denied")

        assert rig.codes == ["backup.sends_failed", "backup.sends_failed"]
        assert [note.subject for note in rig.heard] == ["nas", "cloud"]

    def test_when_the_monitor_already_said_the_destination_is_down_the_sends_are_covered(
        self, rig: Rig
    ) -> None:
        # The destination watch remembers the moment it announced under its own scope.
        NotificationState(rig.store, clock=lambda: rig.now.timestamp()).put(
            "destinations", "nas", str(rig.now.timestamp())
        )

        rig.send(text="Failed to copyto: permission denied")

        assert rig.heard == []
        assert all(row.noticed for row in rig.pending.all())


class TestWhenEverythingHasArrived:
    def test_one_message_says_so_and_answers_the_one_that_said_it_failed(self, rig: Rig) -> None:
        for index in range(3):
            rig.send(owner=f"app{index:02d}.example.com")
        rig.later(minutes=15)
        rows = rig.pending.all()
        for row in rows:
            rig.retry(row)
        assert rig.codes == ["backup.sends_failed"]

        rig.later(hours=1)
        for row in rig.pending.all():
            rig.retry(row, text=None)

        assert rig.codes == ["backup.sends_failed", "backup.sends_recovered"]
        failed, recovered = rig.heard
        assert recovered.state is State.OK and recovered.kind == "backup_failed"
        # The same thing to every channel that can answer: the recovery finds what the failure opened.
        assert grouping.thing_key(failed) == grouping.thing_key(recovered)
        delivered = next(fact for fact in recovered.facts if fact.key == "copies")
        assert delivered.value.startswith("3")
        assert rig.pending.all() == []

    def test_nothing_is_said_while_something_is_still_owed(self, rig: Rig) -> None:
        rig.send(owner="one.example.com")
        rig.send(owner="two.example.com")
        rig.later(minutes=15)
        first, second = rig.pending.all()
        rig.retry(first)
        rig.retry(second)

        rig.retry(first, text=None)

        assert rig.codes == ["backup.sends_failed"]

    def test_after_a_recovery_the_next_trouble_is_a_new_message(self, rig: Rig) -> None:
        rig.send(text="Failed to copyto: permission denied")
        (row,) = rig.pending.all()
        rig.retry(row, text=None)
        rig.later(minutes=5)

        rig.send(text="Failed to copyto: permission denied")

        assert rig.codes == ["backup.sends_failed", "backup.sends_recovered", "backup.sends_failed"]


class TestWhatIsForgotten:
    def test_a_destination_that_is_removed_takes_what_was_owed_to_it(self, rig: Rig) -> None:
        rig.send(destination="nas")
        rig.send(destination="cloud")

        rig.sender.forget_destination("nas")

        assert [row.destination for row in rig.pending.all()] == ["cloud"]


def test_the_things_of_a_destination_and_of_an_application_do_not_collide(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A destination notice is about the destination; the application's own backup is not it."""
    rig.send(text="Failed to copyto: permission denied")

    (said,) = rig.heard
    key: Callable[[Notification], str] = grouping.thing_key
    assert key(said).endswith("/backup/nas")


def test_removing_a_destination_through_its_manager_forgets_what_was_owed_to_it(
    rig: Rig,
) -> None:
    from noust.core.runner import FakeRunner
    from noust.managers.backup_destinations import BackupDestinationManager

    runner = FakeRunner()
    runner.only_knows("rclone")
    runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")
    manager = BackupDestinationManager(runner=runner, store=rig.store)
    manager.add("nas", "sftp", {"host": "nas.example.net", "user": "noust", "pass": "s3cr3t"})
    rig.send(destination="nas", text="Failed to copyto: permission denied")
    assert rig.pending.all() != []

    manager.remove("nas")

    assert rig.pending.all() == []
