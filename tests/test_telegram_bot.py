# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Bot API as Noust reads it: what a refusal means and what an answer carries.

A refusal is classified from Telegram's own words, which are not documented on one page and have
changed spelling between versions, so each meaning is proven against every spelling seen.
"""

from __future__ import annotations

import json

import pytest

from noust.core import telegram_bot
from noust.core.telegram_bot import Refusal
from tests.support.telegram_double import BOT_TOKEN, REPLY_REFUSALS, TOPIC_REFUSALS


class TestWhatARefusalMeans:
    @pytest.mark.parametrize("text", TOPIC_REFUSALS)
    def test_a_topic_that_cannot_take_a_message(self, text: str) -> None:
        assert telegram_bot.refusal_of(text) is Refusal.TOPIC

    @pytest.mark.parametrize("text", REPLY_REFUSALS)
    def test_a_message_that_is_not_there_to_answer(self, text: str) -> None:
        assert telegram_bot.refusal_of(text) is Refusal.REPLY

    @pytest.mark.parametrize(
        "text",
        [
            "Bad Request: can't parse entities: Can't find end of the entity at byte offset 12",
            "Bad Request: CAN'T PARSE ENTITIES: Unsupported start tag",
        ],
    )
    def test_markup_it_cannot_read(self, text: str) -> None:
        assert telegram_bot.refusal_of(text) is Refusal.MARKUP

    @pytest.mark.parametrize(
        "text",
        [
            "Bad Request: chat not found",
            "Forbidden: bot was kicked from the supergroup chat",
            "Bad Request: message is too long",
            "Unauthorized",
            "Too Many Requests: retry after 5",
            "",
            None,
        ],
    )
    def test_anything_else_is_nothing_to_work_around(self, text: str | None) -> None:
        assert telegram_bot.refusal_of(text) is None

    def test_the_spelling_with_underscores_or_spaces_means_the_same(self) -> None:
        assert telegram_bot.refusal_of("TOPIC_ID_INVALID") is Refusal.TOPIC
        assert telegram_bot.refusal_of("topic id invalid") is Refusal.TOPIC


class TestWhatAnAnswerCarries:
    def test_the_id_of_the_message_that_was_sent(self) -> None:
        body = json.dumps({"ok": True, "result": {"message_id": 412, "chat": {"id": 1}}})

        assert telegram_bot.message_id(body.encode()) == 412

    @pytest.mark.parametrize(
        "body",
        [
            b"ok",
            b"",
            b"{}",
            b"[]",
            b'{"ok": true}',
            b'{"ok": true, "result": true}',
            b'{"ok": true, "result": {"message_id": "x"}}',
            b'{"ok": true, "result": {"message_id": true}}',
            b'{"ok": true, "result": {"message_id": 0}}',
            b"\xff\xfe",
        ],
    )
    def test_an_answer_that_has_no_id_is_not_an_error(self, body: bytes) -> None:
        assert telegram_bot.message_id(body) is None

    def test_the_number_of_the_topic_that_was_created(self) -> None:
        body = json.dumps({"ok": True, "result": {"message_thread_id": 9, "name": "x"}})

        assert telegram_bot.created_topic(body.encode()) == 9
        assert telegram_bot.created_topic(b'{"ok": true, "result": {}}') is None
        assert telegram_bot.created_topic(b"nonsense") is None

    def test_the_result_of_any_call(self) -> None:
        assert telegram_bot.result(b'{"ok": true, "result": {"is_forum": true}}') == {
            "is_forum": True
        }
        assert telegram_bot.result(b'{"ok": false}') is None
        assert telegram_bot.result(b"nope") is None


class TestTheAddressOfAMethod:
    def test_it_is_the_fixed_host_with_the_token_in_the_path(self) -> None:
        assert telegram_bot.method_url(BOT_TOKEN, "sendMessage") == (
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        )

    def test_a_token_that_is_not_one_never_reshapes_the_address(self) -> None:
        with pytest.raises(ValueError, match="bot token"):
            telegram_bot.method_url("123:abc/../../x", "sendMessage")

    def test_a_method_is_a_word(self) -> None:
        with pytest.raises(ValueError, match="method"):
            telegram_bot.method_url(BOT_TOKEN, "send/Message")
