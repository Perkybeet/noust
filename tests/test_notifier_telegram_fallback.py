# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Telegram topic that is gone must not lose the alert.

A topic that was deleted, or closed, or whose group stopped being a forum, is refused by the Bot
API with a ``400``. That used to be a definitive failure: the alert stayed ``failed`` in the record
and was lost. Now it is sent once more without the topic, the channel is marked with what happened
so the console can say it, and the message says which topic failed, once an hour. The alert matters
more than its place.

Nothing here talks to Telegram: the opener is a model of the Bot API (``FakeTelegram``).
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.request import Request

import pytest

import noust.core.notifier as notifier_module
from noust.core.config import Config
from noust.core.notification_ledger import DeliveryLedger
from noust.core.notification_state import NotificationState
from noust.core.notifications.topic_notes import WARN_EVERY_SECONDS, TopicNotes
from noust.core.notifier import Notifier
from noust.core.store import NoustStore
from tests.support.telegram_double import (
    BOT_TOKEN,
    CHAT_ID,
    TOPIC_REFUSALS,
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


@pytest.fixture
def notes(ledger: DeliveryLedger, clock: Clock) -> TopicNotes:
    return TopicNotes(NotificationState(ledger.store, clock=clock.epoch), clock=clock.epoch)


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        notifier_module.audit,
        "record",
        lambda event, **fields: events.append({"event": event, **fields}),
    )
    return events


def channel(config: Config, **extra: Any) -> None:
    config.set(
        "notifications.channels.telegram", {"bot_token": BOT_TOKEN, "chat_id": CHAT_ID, **extra}
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


def text_of(body: dict[str, Any]) -> str:
    return str(body["text"])


class TestATopicThatIsGone:
    @pytest.mark.parametrize("reason", TOPIC_REFUSALS)
    def test_the_alert_still_arrives_in_the_general_thread(
        self,
        reason: str,
        config: Config,
        ledger: DeliveryLedger,
        clock: Clock,
        audited: list[dict[str, Any]],
    ) -> None:
        channel(config, thread_id=7)
        telegram = FakeTelegram(refusal_text=reason)

        notifier(config, telegram, ledger, clock).notify(note())

        (sent,) = telegram.sent
        assert "message_thread_id" not in sent
        counts = ledger.counts()
        assert (counts["failed"], counts["retrying"], counts["sent"]) == (0, 0, 1)
        assert audited == []

    def test_a_closed_topic_is_the_same(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config, thread_id=7)
        telegram = FakeTelegram(topics={7}, closed={7}, refusal_text="Bad Request: TOPIC_CLOSED")

        notifier(config, telegram, ledger, clock).notify(note())

        (sent,) = telegram.sent
        assert "message_thread_id" not in sent

    def test_the_topic_is_tried_first_every_time(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        # So a topic that is reopened, or made again with the same number, takes messages again
        # without anybody saying so.
        channel(config, thread_id=7)
        telegram = FakeTelegram()
        sender = notifier(config, telegram, ledger, clock)

        sender.notify(note())
        sender.notify(note(domain="blog.example.com"))

        tried = [body.get("message_thread_id") for body in telegram.of("sendMessage")]
        assert tried == [7, None, 7, None]

    def test_the_message_says_which_topic_failed(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config, thread_id=7)
        telegram = FakeTelegram()

        notifier(config, telegram, ledger, clock).notify(note())

        text = text_of(telegram.sent[0])
        assert "Topic 7" in text and "Settings" in text

    def test_in_spanish_it_says_so_in_spanish(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        from dataclasses import replace

        channel(config, thread_id=7)
        telegram = FakeTelegram()

        notifier(config, telegram, ledger, clock).notify(replace(note(), locale="es"))

        assert "El tema 7" in text_of(telegram.sent[0])

    def test_it_says_so_once_an_hour_and_not_in_every_message(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config, thread_id=7)
        telegram = FakeTelegram()
        sender = notifier(config, telegram, ledger, clock)

        sender.notify(note())
        clock.advance(minutes=30)
        sender.notify(note(domain="blog.example.com"))
        clock.advance(seconds=WARN_EVERY_SECONDS)
        sender.notify(note(domain="docs.example.com"))

        says = ["Topic 7" in text_of(body) for body in telegram.sent]
        assert says == [True, False, True]

    def test_the_line_is_in_the_plain_text_retry_too(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config, thread_id=7)
        telegram = FakeTelegram()
        scripted = Scripted(
            telegram,
            [
                (
                    "sendMessage",
                    "Bad Request: can't parse entities: Can't find end of the entity",
                )
            ],
        )

        notifier(config, scripted, ledger, clock).notify(note())

        # Refused for the markup first, then for the topic: sent plain, without topic, with the line.
        (sent,) = telegram.sent
        assert "parse_mode" not in sent and "message_thread_id" not in sent
        assert "Topic 7" in text_of(sent)

    def test_the_channel_is_marked_with_what_happened(
        self, config: Config, ledger: DeliveryLedger, clock: Clock, notes: TopicNotes
    ) -> None:
        channel(config, thread_id=7)
        telegram = FakeTelegram(refusal_text="Bad Request: message thread not found")

        notifier(config, telegram, ledger, clock).notify(note())

        (mark,) = notes.of("telegram")
        assert mark.topic == 7 and mark.subject is None
        assert mark.reason == "Bad Request: message thread not found"

    def test_the_mark_goes_when_the_topic_takes_a_message_again(
        self, config: Config, ledger: DeliveryLedger, clock: Clock, notes: TopicNotes
    ) -> None:
        channel(config, thread_id=7)
        telegram = FakeTelegram()
        sender = notifier(config, telegram, ledger, clock)
        sender.notify(note())
        assert notes.of("telegram") != []

        telegram.topics.add(7)
        sender.notify(note(domain="blog.example.com"))

        assert notes.of("telegram") == []
        assert telegram.sent[-1]["message_thread_id"] == 7

    def test_the_topic_of_a_subject_is_marked_with_its_subject(
        self, config: Config, ledger: DeliveryLedger, clock: Clock, notes: TopicNotes
    ) -> None:
        channel(config, topics={"deploys": 11, "alerts": 12})
        telegram = FakeTelegram(topics={12})

        sender = notifier(config, telegram, ledger, clock)
        sender.notify(note("deploy_failed"))
        sender.notify(note("app_unreachable"))

        (mark,) = notes.of("telegram")
        assert (mark.topic, mark.subject) == (11, "deploys")
        assert [body.get("message_thread_id") for body in telegram.sent] == [None, 12]

    def test_an_event_with_a_topic_of_its_own_is_judged_alone(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config, thread_id=7, threads={"backup_failed": 11})
        telegram = FakeTelegram(topics={7})
        sender = notifier(config, telegram, ledger, clock)

        sender.notify(note("backup_failed", domain=None))
        sender.notify(note())

        assert [body.get("message_thread_id") for body in telegram.sent] == [None, 7]

    def test_every_delivery_is_recorded_as_sent(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config, thread_id=7)

        notifier(config, FakeTelegram(), ledger, clock).notify(note())

        assert ledger.counts()["sent"] == 1 and ledger.counts()["failed"] == 0

    def test_without_a_record_the_alert_still_arrives(self, config: Config, clock: Clock) -> None:
        channel(config, thread_id=7)
        telegram = FakeTelegram()

        notifier(config, telegram, None, clock).notify(note())

        (sent,) = telegram.sent
        assert "message_thread_id" not in sent


class TestWhatIsNotAWorkaround:
    def test_a_refusal_that_is_not_about_the_topic_still_fails(
        self,
        config: Config,
        ledger: DeliveryLedger,
        clock: Clock,
        audited: list[dict[str, Any]],
    ) -> None:
        channel(config, thread_id=7)

        def not_found(request: Request, timeout: float | None = None) -> io.BytesIO:
            raise refusal(request.full_url, 400, "Bad Request: chat not found")

        notifier(config, not_found, ledger, clock).notify(note())

        assert ledger.counts()["failed"] == 1 and ledger.counts()["sent"] == 0
        assert [event["event"] for event in audited] == ["notify.delivery_failed"]

    def test_a_topic_that_is_not_there_and_a_chat_that_is_not_either_fails_once(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config, thread_id=7)
        calls: list[dict[str, Any]] = []

        def everything_refused(request: Request, timeout: float | None = None) -> io.BytesIO:
            body = json.loads(request.data)  # type: ignore[arg-type]
            calls.append(body)
            if "message_thread_id" in body:
                raise refusal(request.full_url, 400, "Bad Request: message thread not found")
            raise refusal(request.full_url, 400, "Bad Request: chat not found")

        notifier(config, everything_refused, ledger, clock).notify(note())

        assert len(calls) == 2
        assert ledger.counts()["failed"] == 1 and ledger.counts()["sent"] == 0

    def test_a_chat_with_no_topic_that_refuses_for_a_topic_is_not_retried_for_ever(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        calls = {"n": 0}

        def odd(request: Request, timeout: float | None = None) -> io.BytesIO:
            calls["n"] += 1
            raise refusal(request.full_url, 400, "Bad Request: message thread not found")

        notifier(config, odd, ledger, clock).notify(note())

        assert calls["n"] == 1
        assert ledger.counts()["failed"] == 1 and ledger.counts()["sent"] == 0


class TestNothingPersonalLeaks:
    def test_the_log_the_marks_and_the_audit_never_hold_the_chat_or_the_token(
        self,
        config: Config,
        ledger: DeliveryLedger,
        clock: Clock,
        notes: TopicNotes,
        audited: list[dict[str, Any]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        channel(config, thread_id=7)
        telegram = FakeTelegram()

        with caplog.at_level(logging.DEBUG):
            notifier(config, telegram, ledger, clock).notify(note())

        everything = caplog.text
        stored = json.dumps(NotificationState(ledger.store).items("telegram-topics"))
        for secret in (BOT_TOKEN, CHAT_ID, CHAT_ID.lstrip("-")):
            assert secret not in everything
            assert secret not in stored
            assert secret not in json.dumps(audited)
            assert secret not in text_of(telegram.sent[0])


class Scripted:
    """
    Answers the first calls with a refusal and then hands over to the model.

    Attributes:
        telegram: The model the calls that are not scripted go to.
    """

    def __init__(self, telegram: FakeTelegram, refusals: list[tuple[str, str]]) -> None:
        self.telegram = telegram
        self._refusals = list(refusals)

    def __call__(self, request: Request, timeout: float | None = None) -> io.BytesIO:
        method = request.full_url.rsplit("/", 1)[-1]
        if self._refusals and self._refusals[0][0] == method:
            _, text = self._refusals.pop(0)
            raise refusal(request.full_url, 400, text)
        return self.telegram(request, timeout)
