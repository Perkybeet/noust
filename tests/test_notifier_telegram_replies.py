# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
In Telegram a recovery is the answer to its failure, and the end of a deploy the answer to its start.

Item 190. Noust keeps the id of the message it posted (``sendMessage`` says it) and the next message
about the same thing is sent as a reply to it, in the same topic. Whatever goes wrong with that
(the message was deleted, the chat or the topic changed, the memory is gone) the alert is sent
anyway, on its own: losing the grouping never loses the alert.

Nothing here talks to Telegram: the opener is a model of the Bot API.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.request import Request

import pytest

import noust.core.notifier as notifier_module
from noust.core.config import Config
from noust.core.notification_ledger import DeliveryLedger
from noust.core.notification_state import NotificationState
from noust.core.notifications.conversations import TTL_SECONDS
from noust.core.notifications.model import State
from noust.core.notifier import Notifier
from noust.core.store import NoustStore
from tests.support.telegram_double import (
    BOT_TOKEN,
    CHAT_ID,
    REPLY_REFUSALS,
    Clock,
    FakeTelegram,
    note,
    refusal,
)


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "noust" / "config.yaml"
    )
    Config.reset_instance()
    try:
        cfg = Config()
        cfg.set("notifications.enabled", True)
        yield cfg
    finally:
        Config.reset_instance()


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notifier_module, "_resolve_host", lambda host: ("93.184.216.34",))


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def ledger(tmp_path: Path, clock: Clock) -> Iterator[DeliveryLedger]:
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    yield DeliveryLedger(store, clock=clock.epoch)
    NoustStore.reset_instance()


def channel(config: Config, name: str = "telegram", **extra: Any) -> None:
    config.set(
        f"notifications.channels.{name}",
        {"type": "telegram", "bot_token": BOT_TOKEN, "chat_id": CHAT_ID, **extra}
        if name != "telegram"
        else {"bot_token": BOT_TOKEN, "chat_id": CHAT_ID, **extra},
    )


def notifier(config: Config, opener: Any, ledger: DeliveryLedger | None, clock: Clock) -> Notifier:
    return Notifier(
        config,
        opener=opener,
        ledger=ledger,
        record=ledger is not None,
        clock=clock,
        placement=lambda _domain: None,
    )


def reply_of(body: dict[str, Any]) -> int | None:
    return (body.get("reply_parameters") or {}).get("message_id")


FAILED = ("app_unreachable", State.FAILED)
FIXED = ("app_recovered", State.OK)


class TestARecoveryAnswersItsFailure:
    def test_the_recovery_is_a_reply_to_the_message_of_the_failure(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        telegram = FakeTelegram()
        sender = notifier(config, telegram, ledger, clock)

        sender.notify(note(*FAILED))
        sender.notify(note(*FIXED))

        failure, recovery = telegram.sent
        assert reply_of(failure) is None
        assert reply_of(recovery) == telegram.next_message - 1

    def test_a_recovery_with_nothing_to_answer_goes_on_its_own(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        telegram = FakeTelegram()

        notifier(config, telegram, ledger, clock).notify(note(*FIXED))

        assert reply_of(telegram.sent[0]) is None

    def test_the_end_of_a_deploy_answers_its_start(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        config.set("notifications.events.deploy_started", True)
        telegram = FakeTelegram()
        sender = notifier(config, telegram, ledger, clock)

        sender.notify(note("deploy_started", State.PROGRESS))
        started = telegram.next_message
        sender.notify(note("deploy_success", State.OK))

        assert reply_of(telegram.sent[1]) == started

    def test_a_failed_deploy_answers_its_start_and_the_next_one_that_works_too(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        config.set("notifications.events.deploy_started", True)
        telegram = FakeTelegram()
        sender = notifier(config, telegram, ledger, clock)

        sender.notify(note("deploy_started", State.PROGRESS))
        root = telegram.next_message
        sender.notify(note("deploy_failed", State.FAILED))
        sender.notify(note("deploy_success", State.OK))

        assert [reply_of(body) for body in telegram.sent] == [None, root, root]

    def test_after_it_is_fine_again_the_next_problem_is_a_new_conversation(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        telegram = FakeTelegram()
        sender = notifier(config, telegram, ledger, clock)
        sender.notify(note(*FAILED))
        sender.notify(note(*FIXED))

        sender.notify(note(*FAILED))
        sender.notify(note(*FIXED))

        replies = [reply_of(body) for body in telegram.sent]
        assert replies[0] is None and replies[2] is None
        assert replies[1] == 101 and replies[3] == 103

    def test_two_applications_are_two_conversations(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        telegram = FakeTelegram()
        sender = notifier(config, telegram, ledger, clock)

        sender.notify(note(*FAILED, "shop.example.com"))
        sender.notify(note(*FAILED, "blog.example.com"))
        sender.notify(note(*FIXED, "blog.example.com"))
        sender.notify(note(*FIXED, "shop.example.com"))

        assert [reply_of(body) for body in telegram.sent] == [None, None, 102, 101]

    def test_the_answer_goes_in_the_topic_of_the_root(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config, topics={"alerts": 12})
        telegram = FakeTelegram(topics={12})
        sender = notifier(config, telegram, ledger, clock)

        sender.notify(note(*FAILED))
        sender.notify(note(*FIXED))

        assert [body["message_thread_id"] for body in telegram.sent] == [12, 12]
        assert reply_of(telegram.sent[1]) == 101

    def test_what_has_no_way_back_is_never_answered(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        telegram = FakeTelegram()
        sender = notifier(config, telegram, ledger, clock)

        sender.notify(note("cert_expiring", State.WARNING))
        sender.notify(note("cert_expiring", State.WARNING))

        assert [reply_of(body) for body in telegram.sent] == [None, None]

    def test_the_test_button_is_never_answered_and_never_answers(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        telegram = FakeTelegram()
        sender = notifier(config, telegram, ledger, clock)

        sender.test_channel("telegram")
        sender.test_channel("telegram")

        assert [reply_of(body) for body in telegram.sent] == [None, None]

    def test_each_channel_keeps_its_own_conversations(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        channel(config, "telegram-ops")
        telegram = FakeTelegram()
        sender = notifier(config, telegram, ledger, clock)

        sender.notify(note(*FAILED))
        sender.notify(note(*FIXED))

        # Two channels, one message each; each recovery answers its own channel's failure.
        assert [reply_of(body) for body in telegram.sent] == [None, None, 101, 102]


class TestWhenTheRootIsNotThere:
    @pytest.mark.parametrize("text", REPLY_REFUSALS)
    def test_a_root_that_was_deleted_costs_the_grouping_and_not_the_alert(
        self, text: str, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        telegram = FakeTelegram()
        sender = notifier(config, telegram, ledger, clock)
        sender.notify(note(*FAILED))
        telegram.gone_messages.add(101)

        def refuse_replies(request: Request, timeout: float | None = None) -> io.BytesIO:
            body = json.loads(request.data)  # type: ignore[arg-type]
            if reply_of(body) in telegram.gone_messages:
                raise refusal(request.full_url, 400, text)
            return telegram(request, timeout)

        notifier(config, refuse_replies, ledger, clock).notify(note(*FIXED))

        assert len(telegram.sent) == 2
        assert reply_of(telegram.sent[1]) is None
        assert ledger.counts()["failed"] == 0

    def test_after_that_the_next_problem_starts_again(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        telegram = FakeTelegram()
        sender = notifier(config, telegram, ledger, clock)
        sender.notify(note(*FAILED))
        telegram.gone_messages.add(101)
        sender.notify(note(*FAILED, title="Again"))  # answers 101: refused, sent on its own
        sender.notify(note(*FIXED))

        replies = [reply_of(body) for body in telegram.sent]
        # The failure that could not answer became the root, and the recovery answers it.
        assert replies == [None, None, 102]

    def test_a_topic_and_a_reply_both_gone_is_still_one_alert(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config, topics={"alerts": 12})
        telegram = FakeTelegram(topics={12})
        sender = notifier(config, telegram, ledger, clock)
        sender.notify(note(*FAILED))
        telegram.topics.clear()

        sender.notify(note(*FIXED))

        body = telegram.sent[-1]
        assert "message_thread_id" not in body and reply_of(body) is None

    def test_with_no_memory_nothing_is_answered_and_nothing_fails(
        self, config: Config, clock: Clock
    ) -> None:
        channel(config)
        telegram = FakeTelegram()
        sender = notifier(config, telegram, None, clock)

        sender.notify(note(*FAILED))
        sender.notify(note(*FIXED))

        assert [reply_of(body) for body in telegram.sent] == [None, None]


class TestWhereTheAnswerWouldNotReach:
    def test_a_root_in_another_chat_is_not_answered(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        telegram = FakeTelegram()
        notifier(config, telegram, ledger, clock).notify(note(*FAILED))

        channel(config, chat_id="-1009999999999")
        notifier(config, telegram, ledger, clock).notify(note(*FIXED))

        assert reply_of(telegram.sent[1]) is None

    def test_a_root_in_another_topic_is_not_answered(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config, topics={"alerts": 12})
        telegram = FakeTelegram(topics={12, 13})
        notifier(config, telegram, ledger, clock).notify(note(*FAILED))

        channel(config, topics={"alerts": 13})
        notifier(config, telegram, ledger, clock).notify(note(*FIXED))

        assert telegram.sent[1]["message_thread_id"] == 13
        assert reply_of(telegram.sent[1]) is None

    def test_a_root_that_is_a_week_old_is_not_answered(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        telegram = FakeTelegram()
        sender = notifier(config, telegram, ledger, clock)
        sender.notify(note(*FAILED))

        clock.advance(seconds=TTL_SECONDS + 60)
        sender.notify(note(*FIXED))

        assert reply_of(telegram.sent[1]) is None


class TestADeliveryThatFailsChangesNothing:
    def test_a_failure_that_never_reached_telegram_is_not_a_root(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        telegram = FakeTelegram()
        down = {"on": True}

        def flaky(request: Request, timeout: float | None = None) -> io.BytesIO:
            if down["on"]:
                raise refusal(request.full_url, 502, "Bad Gateway")
            return telegram(request, timeout)

        sender = notifier(config, flaky, ledger, clock)
        sender.notify(note(*FAILED))
        down["on"] = False
        sender.notify(note(*FIXED))

        assert [reply_of(body) for body in telegram.sent] == [None]

    def test_a_delivery_sent_again_later_answers_what_is_there_by_then(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        telegram = FakeTelegram()
        state = {"down": True}

        def flaky(request: Request, timeout: float | None = None) -> io.BytesIO:
            if state["down"]:
                raise refusal(request.full_url, 502, "Bad Gateway")
            return telegram(request, timeout)

        sender = notifier(config, flaky, ledger, clock)
        sender.notify(note(*FAILED))
        state["down"] = False
        clock.advance(minutes=2)
        report = sender.deliver_due()

        assert report.delivered == 1
        assert reply_of(telegram.sent[0]) is None
        sender.notify(note(*FIXED))
        assert reply_of(telegram.sent[1]) == 101


class TestNothingPersonalIsKept:
    def test_the_memory_the_log_and_the_requests_log_never_hold_the_chat_or_the_bot(
        self,
        config: Config,
        ledger: DeliveryLedger,
        clock: Clock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        channel(config)
        telegram = FakeTelegram()
        sender = notifier(config, telegram, ledger, clock)

        with caplog.at_level(logging.DEBUG):
            sender.notify(note(*FAILED))
            sender.notify(note(*FIXED))

        kept = json.dumps(NotificationState(ledger.store).items("threads:telegram"))
        for secret in (BOT_TOKEN, CHAT_ID, CHAT_ID.lstrip("-")):
            assert secret not in kept
            assert secret not in caplog.text


def test_an_unreadable_answer_from_telegram_is_not_an_error(
    config: Config, ledger: DeliveryLedger, clock: Clock
) -> None:
    channel(config)
    sent: list[Request] = []

    def plain_ok(request: Request, timeout: float | None = None) -> io.BytesIO:
        sent.append(request)
        return io.BytesIO(b"ok")

    sender = notifier(config, plain_ok, ledger, clock)
    sender.notify(note(*FAILED))
    sender.notify(replace(note(*FIXED)))

    assert len(sent) == 2
    assert reply_of(json.loads(sent[1].data)) is None  # type: ignore[arg-type]
