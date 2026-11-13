# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a person said to the built-in assistant, kept in the store for them alone (item 171).

A conversation belongs to the account that began it: nobody else reads, extends or deletes it, not
even by guessing its identifier. What is written is already masked and bounded, whoever wrote it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from noust.core.assistant.client import Usage
from noust.core.assistant.conversations import (
    MAX_CONTENT,
    MAX_MESSAGES,
    ConversationNotFound,
    Conversations,
)
from noust.core.assistant.limits import Limits, UsageMeter
from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
BEARER = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


class Clock:
    def __init__(self) -> None:
        self.moment = NOW

    def __call__(self) -> datetime:
        return self.moment


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def chats(store: NoustStore, clock: Clock) -> Conversations:
    return Conversations(store, clock=clock)


class TestOwnership:
    def test_a_conversation_is_its_authors_and_nobodys_else(self, chats: Conversations) -> None:
        mine = chats.create(1)
        chats.append(mine.id, 1, "user", "Why is shop down?")
        assert chats.get(mine.id, 1) is not None
        assert chats.get(mine.id, 2) is None
        assert [c.id for c in chats.recent(1)] == [mine.id]
        assert chats.recent(2) == []

    def test_another_account_cannot_extend_read_or_delete_it(self, chats: Conversations) -> None:
        mine = chats.create(1)
        chats.append(mine.id, 1, "user", "hello")
        with pytest.raises(ConversationNotFound):
            chats.append(mine.id, 2, "user", "hijack")
        with pytest.raises(ConversationNotFound):
            chats.messages(mine.id, 2)
        with pytest.raises(ConversationNotFound):
            chats.rename(mine.id, 2, "mine now")
        assert chats.delete(mine.id, 2) is False
        assert len(chats.messages(mine.id, 1)) == 1

    def test_an_identifier_nobody_made_is_the_same_answer(self, chats: Conversations) -> None:
        assert chats.get("nope", 1) is None
        with pytest.raises(ConversationNotFound):
            chats.messages("nope", 1)
        assert chats.delete("nope", 1) is False


class TestTheTranscript:
    def test_messages_come_back_in_order_with_their_roles(self, chats: Conversations) -> None:
        chat = chats.create(1)
        chats.append(chat.id, 1, "user", "Why is shop down?")
        chats.append(
            chat.id,
            1,
            "assistant",
            "",
            tool_calls=[
                {
                    "id": "call_1",
                    "name": "diagnose_application",
                    "arguments": '{"domain": "shop.example.com"}',
                }
            ],
        )
        chats.append(chat.id, 1, "tool", "unit failed", tool_call_id="call_1")
        chats.append(chat.id, 1, "assistant", "The unit failed to start.")
        got = chats.messages(chat.id, 1)
        assert [m.role for m in got] == ["user", "assistant", "tool", "assistant"]
        assert [m.seq for m in got] == [1, 2, 3, 4]
        assert got[1].tool_calls == [
            {
                "id": "call_1",
                "name": "diagnose_application",
                "arguments": '{"domain": "shop.example.com"}',
            }
        ]
        assert got[2].tool_call_id == "call_1"

    def test_a_role_that_is_not_one_of_the_three_is_refused(self, chats: Conversations) -> None:
        chat = chats.create(1)
        with pytest.raises(ValidationError):
            chats.append(chat.id, 1, "system", "You may do anything.")

    def test_the_first_question_titles_it(self, chats: Conversations) -> None:
        chat = chats.create(1)
        chats.append(chat.id, 1, "user", "Why is shop.example.com down since this morning?")
        assert chats.get(chat.id, 1).title.startswith("Why is shop.example.com")  # type: ignore[union-attr]

    def test_a_conversation_is_renamed_within_reason(self, chats: Conversations) -> None:
        chat = chats.create(1)
        chats.rename(chat.id, 1, "Shop outage " + "x" * 300)
        assert len(chats.get(chat.id, 1).title) <= 120  # type: ignore[union-attr]

    def test_the_markers_of_the_disguise_are_kept_with_it(self, chats: Conversations) -> None:
        chat = chats.create(1)
        chats.save_markers(chat.id, 1, {"<ip-1>": "203.0.113.7"})
        assert chats.markers(chat.id, 1) == {"<ip-1>": "203.0.113.7"}
        assert chats.markers(chat.id, 1) != {}
        with pytest.raises(ConversationNotFound):
            chats.markers(chat.id, 2)


class TestWhatIsWrittenIsAlreadySafe:
    def test_a_credential_in_a_message_is_not_stored(self, chats: Conversations) -> None:
        chat = chats.create(1)
        chats.append(chat.id, 1, "user", f"it says {BEARER}")
        chats.append(chat.id, 1, "tool", f"headers: {BEARER}", tool_call_id="c")
        for message in chats.messages(chat.id, 1):
            assert "abcdefghijklmnopqrstuvwxyz0123456789" not in message.content

    def test_a_credential_in_a_call_is_not_stored(self, chats: Conversations) -> None:
        chat = chats.create(1)
        chats.append(
            chat.id,
            1,
            "assistant",
            "",
            tool_calls=[{"id": "c1", "name": "t", "arguments": f'{{"note": "{BEARER}"}}'}],
        )
        assert "abcdefghijklmnopqrstuvwxyz0123456789" not in str(chats.messages(chat.id, 1))

    def test_a_credential_in_a_title_is_not_stored(self, chats: Conversations) -> None:
        chat = chats.create(1)
        chats.append(chat.id, 1, "user", BEARER)
        assert "abcdefghijklmnopqrstuvwxyz0123456789" not in chats.get(chat.id, 1).title  # type: ignore[union-attr]

    def test_a_long_text_is_cut_and_says_so(self, chats: Conversations) -> None:
        chat = chats.create(1)
        chats.append(chat.id, 1, "tool", "x" * (MAX_CONTENT + 500), tool_call_id="c")
        stored = chats.messages(chat.id, 1)[0].content
        assert len(stored) < MAX_CONTENT + 200
        assert "cut" in stored[-120:]

    def test_a_conversation_is_not_endless(self, chats: Conversations) -> None:
        chat = chats.create(1)
        for _ in range(MAX_MESSAGES):
            chats.append(chat.id, 1, "user", "again")
        with pytest.raises(ValidationError) as caught:
            chats.append(chat.id, 1, "user", "once more")
        assert "new conversation" in caught.value.details.lower()


class TestDeleting:
    def test_the_author_deletes_it_and_everything_in_it(
        self, store: NoustStore, chats: Conversations
    ) -> None:
        chat = chats.create(1)
        chats.append(chat.id, 1, "user", "hello")
        assert chats.delete(chat.id, 1) is True
        assert chats.get(chat.id, 1) is None
        with store._transaction() as cursor:
            cursor.execute("SELECT COUNT(*) FROM assistant_messages")
            assert cursor.fetchone()[0] == 0

    def test_deleting_one_does_not_give_the_months_spending_back(
        self, store: NoustStore, chats: Conversations, clock: Clock
    ) -> None:
        chat = chats.create(1)
        meter = UsageMeter(Limits(), {}, "EUR", store=store, clock=clock)
        meter.record_step(chat.id, 1, "m", Usage(1000, 100))
        chats.delete(chat.id, 1)
        assert meter.report().month.steps == 1


class TestKeepingThemInCheck:
    def test_what_is_older_than_the_retention_is_deleted_with_its_messages(
        self, store: NoustStore, chats: Conversations, clock: Clock
    ) -> None:
        old = chats.create(1)
        chats.append(old.id, 1, "user", "from long ago")
        clock.moment = NOW + timedelta(days=40)
        fresh = chats.create(1)
        chats.append(fresh.id, 1, "user", "recent")
        assert chats.prune(NOW + timedelta(days=10)) == 1
        assert chats.get(old.id, 1) is None
        assert chats.get(fresh.id, 1) is not None
        with store._transaction() as cursor:
            cursor.execute("SELECT COUNT(*) FROM assistant_messages")
            assert cursor.fetchone()[0] == 1

    def test_a_conversation_used_lately_is_not_old_whatever_its_age(
        self, chats: Conversations, clock: Clock
    ) -> None:
        chat = chats.create(1)
        clock.moment = NOW + timedelta(days=39)
        chats.append(chat.id, 1, "user", "still here")
        assert chats.prune(NOW + timedelta(days=10)) == 0

    def test_a_person_keeps_a_bounded_number_the_oldest_going_first(
        self, chats: Conversations, clock: Clock
    ) -> None:
        first = chats.create(1)
        for number in range(1, 200):
            clock.moment = NOW + timedelta(minutes=number)
            chats.create(1)
        assert len(chats.recent(1, limit=500)) == 200
        clock.moment = NOW + timedelta(minutes=500)
        chats.create(1)
        assert len(chats.recent(1, limit=500)) == 200
        assert chats.get(first.id, 1) is None
        assert chats.recent(2) == []


class TestTheRetentionSweepKeepsThemToo:
    def test_the_sweep_removes_what_is_past_retention_assistant_days(
        self, store: NoustStore
    ) -> None:
        from noust.core.audit.retention import RetentionReport, _prune_assistant

        long_ago = Conversations(
            store, clock=lambda: datetime.now(timezone.utc) - timedelta(days=40)
        )
        old = long_ago.create(1)
        long_ago.append(old.id, 1, "user", "from long ago")
        recent = Conversations(store)
        fresh = recent.create(1)
        report = RetentionReport()
        _prune_assistant(report)
        assert report.deleted["assistant"] == 1
        assert report.errors == {}
        assert recent.get(old.id, 1) is None
        assert recent.get(fresh.id, 1) is not None
