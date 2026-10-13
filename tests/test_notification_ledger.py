# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The delivery ledger: a record of every delivery and the queue of the ones to retry.

Checklist K-30: retries with a growing wait, a history an operator can read, and
a failure that stays a row.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.notification_ledger import (
    KEEP_FAILED_SECONDS,
    KEEP_SENT_SECONDS,
    MAX_ATTEMPTS,
    MAX_ROWS,
    RETRY_DELAYS,
    DeliveryLedger,
    backoff,
)
from noust.core.notifications.model import Notification, State
from noust.core.store import NoustStore


class Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def ledger(tmp_path: Path, clock: Clock) -> Iterator[DeliveryLedger]:
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    yield DeliveryLedger(store, clock=clock)
    NoustStore.reset_instance()


def note(subject: str = "shop.example.com", kind: str = "deploy_failed") -> Notification:
    return Notification(
        kind=kind,
        code="deploy.failed",
        state=State.FAILED,
        locale="en",
        title="Deploy failed",
        subject=subject,
        summary="It stopped.",
        server="web-1",
    )


class TestBackoff:
    def test_the_wait_grows_after_each_failure(self) -> None:
        waits = [backoff(attempt) for attempt in range(1, MAX_ATTEMPTS)]

        assert waits == list(RETRY_DELAYS)
        assert waits == sorted(waits)

    def test_the_last_attempt_has_no_next(self) -> None:
        assert backoff(MAX_ATTEMPTS) is None
        assert backoff(0) is None


class TestRecording:
    def test_a_delivery_that_went_out_keeps_no_notification(self, ledger: DeliveryLedger) -> None:
        delivery_id = ledger.record_sent(note(), "slack", "slack")

        delivery = ledger.get(delivery_id)
        assert delivery is not None
        assert (delivery.state, delivery.attempts, delivery.channel) == ("sent", 1, "slack")
        assert delivery.payload is None
        assert delivery.subject == "shop.example.com"

    def test_a_failure_is_kept_with_the_notification_and_when_to_try_again(
        self, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        delivery_id = ledger.record_failure(
            note(), "slack", "slack", "HTTP 503 Service Unavailable", retry_in=60
        )

        delivery = ledger.get(delivery_id)
        assert delivery is not None
        assert delivery.state == "retrying"
        assert delivery.next_attempt_at == clock.now + 60
        assert delivery.last_error == "HTTP 503 Service Unavailable"
        assert delivery.notification() == note().__class__.from_dict(delivery.payload or {})

    def test_a_failure_that_cannot_pass_is_failed_at_once(self, ledger: DeliveryLedger) -> None:
        delivery_id = ledger.record_failure(
            note(), "slack", "slack", "HTTP 404 Not Found", retry_in=None
        )

        delivery = ledger.get(delivery_id)
        assert delivery is not None
        assert delivery.state == "failed" and delivery.next_attempt_at is None
        assert delivery.payload is not None

    def test_a_long_error_is_cut(self, ledger: DeliveryLedger) -> None:
        delivery_id = ledger.record_failure(note(), "slack", "slack", "x" * 5000, retry_in=None)

        delivery = ledger.get(delivery_id)
        assert delivery is not None and delivery.last_error is not None
        assert len(delivery.last_error) <= 1000


class TestTheQueue:
    def test_nothing_is_due_before_its_time(self, ledger: DeliveryLedger, clock: Clock) -> None:
        ledger.record_failure(note(), "slack", "slack", "down", retry_in=60)

        assert ledger.due() == []
        clock.now += 59
        assert ledger.due() == []
        clock.now += 1
        assert [d.channel for d in ledger.due()] == ["slack"]

    def test_a_held_one_is_due_when_the_quiet_hours_end(
        self, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        ledger.hold(note(), "oncall", "pushover", clock.now + 3600)

        assert ledger.due() == []
        clock.now += 3600
        (due,) = ledger.due()
        assert due.state == "held"

    def test_what_is_settled_is_not_due(self, ledger: DeliveryLedger, clock: Clock) -> None:
        ledger.record_sent(note(), "slack", "slack")
        ledger.record_failure(note(), "slack", "slack", "gone", retry_in=None)
        clock.now += 10**6

        assert ledger.due() == []

    def test_due_is_oldest_first_and_bounded(self, ledger: DeliveryLedger, clock: Clock) -> None:
        for index in range(5):
            ledger.record_failure(
                note(f"app{index}.example.com"), "slack", "slack", "x", retry_in=1
            )
        clock.now += 5

        assert [d.subject for d in ledger.due(limit=3)] == [
            "app0.example.com",
            "app1.example.com",
            "app2.example.com",
        ]

    def test_retrying_again_and_failing_moves_it_along(
        self, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        delivery_id = ledger.record_failure(note(), "slack", "slack", "down", retry_in=60)

        ledger.retry_later(delivery_id, "still down", 2, 300)
        again = ledger.get(delivery_id)
        assert again is not None
        assert (again.state, again.attempts, again.last_error) == ("retrying", 2, "still down")
        assert again.next_attempt_at == clock.now + 300

        ledger.failed(delivery_id, "gave up", 6)
        gave_up = ledger.get(delivery_id)
        assert gave_up is not None
        assert (gave_up.state, gave_up.attempts, gave_up.next_attempt_at) == ("failed", 6, None)
        assert gave_up.payload is not None

    def test_sent_after_retries_drops_the_notification(self, ledger: DeliveryLedger) -> None:
        delivery_id = ledger.record_failure(note(), "slack", "slack", "down", retry_in=60)

        ledger.sent(delivery_id)

        delivery = ledger.get(delivery_id)
        assert delivery is not None
        assert delivery.state == "sent" and delivery.payload is None
        assert delivery.attempts == 2
        assert delivery.last_error is None


class TestSendingAgainByHand:
    def test_a_failed_delivery_goes_back_in_the_queue_with_a_fresh_count(
        self, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        delivery_id = ledger.record_failure(note(), "slack", "slack", "HTTP 404", retry_in=None)
        ledger.failed(delivery_id, "HTTP 404", 6)

        again = ledger.retry_now(delivery_id)

        assert again is not None
        assert (again.state, again.attempts) == ("retrying", 0)
        assert [d.id for d in ledger.due()] == [delivery_id]

    def test_one_that_went_out_cannot_be_sent_again(self, ledger: DeliveryLedger) -> None:
        delivery_id = ledger.record_sent(note(), "slack", "slack")

        assert ledger.retry_now(delivery_id) is None

    def test_an_unknown_one_is_nothing(self, ledger: DeliveryLedger) -> None:
        assert ledger.retry_now(999) is None


class TestReading:
    def test_the_newest_come_first_and_can_be_filtered(self, ledger: DeliveryLedger) -> None:
        ledger.record_sent(note("a.example.com"), "slack", "slack")
        ledger.record_failure(note("b.example.com"), "teams", "teams", "down", retry_in=None)
        ledger.record_sent(note("c.example.com"), "teams", "teams")

        assert [d.subject for d in ledger.recent()] == [
            "c.example.com",
            "b.example.com",
            "a.example.com",
        ]
        assert [d.subject for d in ledger.recent(state="failed")] == ["b.example.com"]
        assert [d.subject for d in ledger.recent(channel="slack")] == ["a.example.com"]
        assert len(ledger.recent(limit=2)) == 2

    def test_the_counts_name_every_state(self, ledger: DeliveryLedger, clock: Clock) -> None:
        ledger.record_sent(note(), "slack", "slack")
        ledger.record_failure(note(), "slack", "slack", "x", retry_in=None)
        ledger.hold(note(), "slack", "slack", clock.now + 1)

        assert ledger.counts() == {"retrying": 0, "held": 1, "sent": 1, "failed": 1}

    def test_the_api_form_leaves_the_notification_out(self, ledger: DeliveryLedger) -> None:
        delivery_id = ledger.record_failure(note(), "slack", "slack", "down", retry_in=60)
        delivery = ledger.get(delivery_id)
        assert delivery is not None

        shown = delivery.to_dict()

        assert "payload" not in shown
        assert shown["can_retry"] is True
        assert shown["channel"] == "slack"

    def test_how_many_went_out_lately(self, ledger: DeliveryLedger, clock: Clock) -> None:
        ledger.record_sent(note(), "slack", "slack")
        clock.now += 1800
        ledger.record_sent(note(), "slack", "slack")
        ledger.record_sent(note(), "teams", "teams")

        assert ledger.sent_since("slack", clock.now - 3600) == 2
        assert ledger.sent_since("slack", clock.now - 60) == 1


class TestPurge:
    def test_old_history_goes_and_what_may_still_be_sent_stays(
        self, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        sent = ledger.record_sent(note(), "slack", "slack")
        failed = ledger.record_failure(note(), "slack", "slack", "x", retry_in=None)
        waiting = ledger.record_failure(note(), "slack", "slack", "x", retry_in=60)
        clock.now += KEEP_SENT_SECONDS + 1

        assert ledger.purge() == 1
        assert ledger.get(sent) is None
        assert ledger.get(failed) is not None and ledger.get(waiting) is not None

        clock.now += KEEP_FAILED_SECONDS
        assert ledger.purge() == 1
        assert ledger.get(failed) is None
        assert ledger.get(waiting) is not None

    def test_the_table_has_a_ceiling(
        self, ledger: DeliveryLedger, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("noust.core.notification_ledger.MAX_ROWS", 25)
        for _ in range(40):
            ledger.record_sent(note(), "slack", "slack")

        ledger.purge()

        assert ledger.counts()["sent"] == 25

    def test_the_ceiling_is_not_small(self) -> None:
        assert MAX_ROWS >= 1_000
