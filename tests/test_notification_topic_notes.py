# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a Telegram channel remembers of a topic that stopped taking messages.

The mark is what lets the console say so, the warning in the message is said once an hour and not
in every message, and a topic that works again clears its own mark.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.notification_state import NotificationState
from noust.core.notifications.topic_notes import WARN_EVERY_SECONDS, TopicNote, TopicNotes
from noust.core.store import NoustStore
from tests.support.telegram_double import Clock


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def state(tmp_path: Path, clock: Clock) -> Iterator[NotificationState]:
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    yield NotificationState(store, clock=clock.epoch)
    NoustStore.reset_instance()


@pytest.fixture
def notes(state: NotificationState, clock: Clock) -> TopicNotes:
    return TopicNotes(state, clock=clock.epoch)


def test_a_channel_with_no_trouble_has_no_marks(notes: TopicNotes) -> None:
    assert notes.of("telegram") == []


def test_a_lost_topic_is_marked_with_what_telegram_said(notes: TopicNotes, clock: Clock) -> None:
    notes.lost("telegram", 12, "deploys", "Bad Request: message thread not found")

    assert notes.of("telegram") == [
        TopicNote(
            topic=12,
            subject="deploys",
            reason="Bad Request: message thread not found",
            at=clock.epoch(),
            warned_at=clock.epoch(),
        )
    ]


def test_the_first_time_the_message_says_so(notes: TopicNotes) -> None:
    assert notes.lost("telegram", 12, "deploys", "gone") is True


def test_the_next_messages_of_the_hour_do_not(notes: TopicNotes, clock: Clock) -> None:
    notes.lost("telegram", 12, "deploys", "gone")
    clock.advance(seconds=WARN_EVERY_SECONDS - 1)

    assert notes.lost("telegram", 12, "deploys", "gone") is False


def test_after_the_hour_it_says_so_again(notes: TopicNotes, clock: Clock) -> None:
    notes.lost("telegram", 12, "deploys", "gone")
    clock.advance(seconds=WARN_EVERY_SECONDS)

    assert notes.lost("telegram", 12, "deploys", "gone") is True


def test_each_topic_has_its_own_hour(notes: TopicNotes) -> None:
    notes.lost("telegram", 12, "deploys", "gone")

    assert notes.lost("telegram", 13, "alerts", "gone") is True


def test_each_channel_has_its_own_marks(notes: TopicNotes) -> None:
    notes.lost("telegram", 12, "deploys", "gone")

    assert notes.of("telegram-staging") == []
    assert notes.lost("telegram-staging", 12, "deploys", "gone") is True


def test_the_mark_keeps_when_it_first_happened(notes: TopicNotes, clock: Clock) -> None:
    notes.lost("telegram", 12, "deploys", "gone")
    first = clock.epoch()
    clock.advance(hours=2)
    notes.lost("telegram", 12, "deploys", "gone again")

    (note,) = notes.of("telegram")
    assert note.at == first
    assert note.reason == "gone again"
    assert note.warned_at == clock.epoch()


def test_a_topic_that_works_again_loses_its_mark(notes: TopicNotes) -> None:
    notes.lost("telegram", 12, "deploys", "gone")
    notes.lost("telegram", 13, "alerts", "gone")

    assert notes.worked("telegram", 12) is True

    assert [note.topic for note in notes.of("telegram")] == [13]
    assert notes.worked("telegram", 12) is False


def test_a_channel_can_be_cleared_when_its_topics_change(notes: TopicNotes) -> None:
    notes.lost("telegram", 12, "deploys", "gone")
    notes.lost("telegram", 13, "alerts", "gone")

    notes.clear("telegram")

    assert notes.of("telegram") == []


def test_a_topic_of_the_whole_channel_has_no_subject(notes: TopicNotes) -> None:
    notes.lost("telegram", 7, None, "gone")

    assert notes.of("telegram")[0].subject is None


def test_the_reason_is_one_line_and_bounded(notes: TopicNotes) -> None:
    notes.lost("telegram", 12, None, "first line\nsecond\x1b[31m line " + "x" * 1000)

    (note,) = notes.of("telegram")
    assert "\n" not in note.reason and "\x1b" not in note.reason
    assert len(note.reason) <= 300


def test_a_value_somebody_edited_by_hand_reads_as_nothing(
    notes: TopicNotes, state: NotificationState
) -> None:
    state.put("telegram-topics", "telegram", "not json")
    assert notes.of("telegram") == []

    state.put_json("telegram-topics", "telegram", {"x": {"topic": "y"}, "12": 3})
    assert notes.of("telegram") == []


def test_nothing_in_the_store_names_the_chat_or_the_bot(
    notes: TopicNotes, state: NotificationState
) -> None:
    notes.lost("telegram", 12, "deploys", "Bad Request: message thread not found")

    stored = json.dumps(state.items("telegram-topics"))
    assert "1234567890" not in stored and "AAHdq" not in stored


def test_only_the_topics_still_in_use_stand(notes: TopicNotes) -> None:
    notes.lost("telegram", 12, "deploys", "gone")
    notes.lost("telegram", 13, "alerts", "gone")

    standing = notes.standing("telegram", {"topics": {"deploys": 12, "alerts": 99}})

    assert [note.topic for note in standing] == [12]
