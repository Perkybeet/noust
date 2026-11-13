# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a channel remembers of a conversation: the first message about a thing, which every later one
answers.

Item 190. The rule is short and has to hold in every channel that can answer a message: the first
message about a thing is its root, every later message about it (a recovery, the end of a deploy)
answers the root, and a message that says it is fine again answers and ends the conversation. A
root that is old, or was posted somewhere else than where the answer would go, is not answered.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.notification_state import NotificationState
from noust.core.notifications import grouping
from noust.core.notifications.conversations import TTL_SECONDS, Conversations, Root, settles
from noust.core.notifications.model import State
from noust.core.store import NoustStore
from tests.support.telegram_double import Clock, note


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
def memory(state: NotificationState, clock: Clock) -> Conversations:
    return Conversations(state, clock=clock.epoch)


FAILED = note("app_unreachable", State.FAILED)
FIXED = note("app_recovered", State.OK)


class TestWhatAMessageDoesToItsConversation:
    @pytest.mark.parametrize("state", [State.FAILED, State.WARNING, State.PROGRESS])
    def test_a_problem_or_a_start_opens_it(self, state: State) -> None:
        assert settles(note("app_unreachable", state)) == "opens"

    def test_being_fine_again_closes_it(self) -> None:
        assert settles(FIXED) == "closes"

    def test_news_that_is_neither_continues_it(self) -> None:
        assert settles(note("alert_acknowledged", State.INFO)) == "continues"

    @pytest.mark.parametrize(
        "kind", ["cert_expiring", "approval_requested", "os_patches", "token_expiring", "test"]
    )
    def test_what_has_no_way_back_has_no_conversation(self, kind: str) -> None:
        assert settles(note(kind, State.WARNING)) is None
        assert settles(note(kind, State.OK)) is None

    def test_every_kind_that_has_a_family_has_a_rule(self) -> None:
        for kind in grouping.FAMILY:
            for state in State:
                assert settles(note(kind, state)) in ("opens", "closes", "continues")


class TestRemembering:
    def test_nothing_is_remembered_before_anything_is_sent(self, memory: Conversations) -> None:
        assert memory.root("telegram", FIXED) is None

    def test_the_first_message_about_a_thing_is_its_root(
        self, memory: Conversations, clock: Clock
    ) -> None:
        memory.sent("telegram", FAILED, "101")

        assert memory.root("telegram", FIXED) == Root(
            ref="101", at=clock.epoch(), thread=None, chat=""
        )

    def test_a_later_message_does_not_replace_the_root(self, memory: Conversations) -> None:
        memory.sent("telegram", FAILED, "101")

        memory.sent("telegram", note("app_unreachable", State.WARNING), "102")

        assert memory.root("telegram", FIXED).ref == "101"  # type: ignore[union-attr]

    def test_being_fine_again_ends_the_conversation(self, memory: Conversations) -> None:
        memory.sent("telegram", FAILED, "101")

        memory.sent("telegram", FIXED, "102")

        assert memory.root("telegram", FIXED) is None
        # And the next problem is a new conversation, not an answer to the old one.
        memory.sent("telegram", FAILED, "103")
        assert memory.root("telegram", FIXED).ref == "103"  # type: ignore[union-attr]

    def test_news_that_continues_changes_nothing(self, memory: Conversations) -> None:
        memory.sent("telegram", FAILED, "101")
        taken = note("alert_acknowledged", State.INFO)

        memory.sent("telegram", taken, "102")

        assert memory.root("telegram", FIXED).ref == "101"  # type: ignore[union-attr]

    def test_news_that_continues_does_not_open_one(self, memory: Conversations) -> None:
        memory.sent("telegram", note("alert_acknowledged", State.INFO), "102")

        assert memory.root("telegram", FIXED) is None

    def test_a_message_without_an_id_cannot_be_a_root(self, memory: Conversations) -> None:
        memory.sent("telegram", FAILED, None)

        assert memory.root("telegram", FIXED) is None

    def test_a_message_without_an_id_still_ends_a_conversation(self, memory: Conversations) -> None:
        memory.sent("telegram", FAILED, "101")

        memory.sent("telegram", FIXED, None)

        assert memory.root("telegram", FIXED) is None

    def test_what_has_no_conversation_is_never_kept(
        self, memory: Conversations, state: NotificationState
    ) -> None:
        memory.sent("telegram", note("cert_expiring", State.WARNING), "101")

        assert state.items("threads:telegram") == {}


class TestWhichThingItIs:
    def test_each_application_has_its_own(self, memory: Conversations) -> None:
        memory.sent("telegram", FAILED, "101")

        assert memory.root("telegram", note("app_recovered", State.OK, "blog.example.com")) is None

    def test_each_server_has_its_own(self, memory: Conversations) -> None:
        memory.sent("telegram", FAILED, "101")

        assert memory.root("telegram", note("app_recovered", State.OK, server="web-2")) is None

    def test_a_deploy_is_not_what_the_application_does(self, memory: Conversations) -> None:
        memory.sent("telegram", FAILED, "101")

        assert memory.root("telegram", note("deploy_success", State.OK)) is None

    def test_each_channel_has_its_own(self, memory: Conversations) -> None:
        memory.sent("telegram", FAILED, "101")

        assert memory.root("telegram-staging", FIXED) is None

    def test_the_start_of_a_deploy_is_answered_by_its_end(self, memory: Conversations) -> None:
        memory.sent("telegram", note("deploy_started", State.PROGRESS), "201")

        assert memory.root("telegram", note("deploy_success", State.OK)).ref == "201"  # type: ignore[union-attr]
        assert memory.root("telegram", note("deploy_failed", State.FAILED)).ref == "201"  # type: ignore[union-attr]


class TestWhereTheRootWas:
    def test_a_root_in_another_chat_is_not_answered(self, memory: Conversations) -> None:
        memory.sent("telegram", FAILED, "101", chat="aaaa")

        assert memory.root("telegram", FIXED, chat="bbbb") is None
        assert memory.root("telegram", FIXED, chat="aaaa") is not None

    def test_a_root_in_another_topic_is_not_answered(self, memory: Conversations) -> None:
        memory.sent("telegram", FAILED, "101", thread=7)

        assert memory.root("telegram", FIXED, thread=8) is None
        assert memory.root("telegram", FIXED, thread=None) is None
        assert memory.root("telegram", FIXED, thread=7) is not None


class TestHowLongItIsKept:
    def test_a_root_that_is_old_is_not_answered(self, memory: Conversations, clock: Clock) -> None:
        memory.sent("telegram", FAILED, "101")

        clock.advance(seconds=TTL_SECONDS - 1)
        assert memory.root("telegram", FIXED) is not None
        clock.advance(seconds=2)
        assert memory.root("telegram", FIXED) is None

    def test_a_problem_after_an_old_root_is_a_new_conversation(
        self, memory: Conversations, clock: Clock
    ) -> None:
        memory.sent("telegram", FAILED, "101")
        clock.advance(seconds=TTL_SECONDS + 1)

        memory.sent("telegram", FAILED, "102")

        assert memory.root("telegram", FIXED).ref == "102"  # type: ignore[union-attr]

    def test_what_has_expired_is_forgotten_when_something_is_remembered(
        self, memory: Conversations, state: NotificationState, clock: Clock
    ) -> None:
        memory.sent("telegram", FAILED, "101")
        clock.advance(seconds=TTL_SECONDS + 1)

        memory.sent("telegram", note("unit_failed", State.FAILED, "x.service"), "105")

        assert len(state.items("threads:telegram")) == 1


class TestForgetting:
    def test_a_root_that_is_gone_is_forgotten(self, memory: Conversations) -> None:
        memory.sent("telegram", FAILED, "101")

        memory.forget("telegram", FIXED)

        assert memory.root("telegram", FIXED) is None

    def test_forgetting_what_is_not_there_is_nothing(self, memory: Conversations) -> None:
        memory.forget("telegram", FIXED)


class TestWhatIsStored:
    def test_a_value_somebody_edited_by_hand_reads_as_nothing(
        self, memory: Conversations, state: NotificationState
    ) -> None:
        key = grouping.thing_key(FIXED)
        for bad in ("not json", '{"ref": 5}', '{"at": "x", "ref": "1"}', "[]", '{"ref": ""}'):
            state.put("threads:telegram", key, bad)
            assert memory.root("telegram", FIXED) is None

    def test_nothing_in_the_store_names_the_chat_or_the_bot(
        self, memory: Conversations, state: NotificationState
    ) -> None:
        memory.sent("telegram", FAILED, "101", chat="a1b2c3", thread=7)

        stored = json.dumps(state.items("threads:telegram"))
        assert "1234567890" not in stored and "AAHdq" not in stored
        assert "a1b2c3" in stored, "the chat is kept only as the short fingerprint it was given"
