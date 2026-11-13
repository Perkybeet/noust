# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Noust creates the topics of a forum group, when somebody asks and the bot is allowed to.

Item 190. Nothing here talks to Telegram: the opener is a model of the Bot API. What is proved is
what the operator sees: the topics that were missing are created and their numbers saved, repeating
it creates nothing twice, and when the group cannot take them (it is not a forum, the bot may not
manage topics, Telegram refuses) the answer says which and in Telegram's own words.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import noust.core.notifier as notifier_module
from noust.core.config import Config
from noust.core.exceptions import ValidationError
from noust.core.notification_channels import ChannelAdmin
from noust.core.notification_ledger import DeliveryLedger
from noust.core.notifications import grouping
from noust.core.notifier import Notifier
from noust.core.store import NoustStore
from noust.core.telegram_topics import TelegramTopics, TopicsResult
from tests.support.telegram_double import BOT_TOKEN, CHAT_ID, Clock, FakeTelegram


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "noust" / "config.yaml"
    )
    Config.reset_instance()
    try:
        yield Config()
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


def channel(config: Config, **extra: Any) -> None:
    config.set(
        "notifications.channels.telegram", {"bot_token": BOT_TOKEN, "chat_id": CHAT_ID, **extra}
    )


def topics_of(
    config: Config, telegram: FakeTelegram, ledger: DeliveryLedger | None, clock: Clock
) -> TelegramTopics:
    notifier = Notifier(
        config,
        opener=telegram,
        ledger=ledger,
        record=ledger is not None,
        clock=clock,
        placement=lambda _domain: None,
    )
    return TelegramTopics(notifier)


def create(
    config: Config,
    telegram: FakeTelegram,
    ledger: DeliveryLedger | None,
    clock: Clock,
    **options: Any,
) -> TopicsResult:
    return topics_of(config, telegram, ledger, clock).create(
        "telegram", ChannelAdmin(config), **options
    )


def saved(config: Config) -> dict[str, int]:
    return dict(config.get("notifications.channels.telegram.topics") or {})


class TestCreatingTheTopics:
    def test_the_topics_of_every_subject_are_created_and_their_numbers_saved(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        telegram = FakeTelegram()

        result = create(config, telegram, ledger, clock)

        assert result.problem is None and result.failed == ()
        assert set(result.created) == set(grouping.SUBJECTS)
        assert saved(config) == result.created == result.topics
        assert len(set(result.created.values())) == len(grouping.SUBJECTS)
        assert set(result.created.values()) == telegram.topics

    def test_they_are_named_for_what_they_hold_in_the_operators_language(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        telegram = FakeTelegram()

        create(config, telegram, ledger, clock)

        names = [body["name"] for body in telegram.of("createForumTopic")]
        assert names == [
            "Noust: deploys",
            "Noust: alerts",
            "Noust: backups",
            "Noust: security",
            "Noust: system",
        ]
        assert all(body["chat_id"] == CHAT_ID for body in telegram.of("createForumTopic"))

    def test_in_spanish(self, config: Config, ledger: DeliveryLedger, clock: Clock) -> None:
        config.set("notifications.language", "es")
        channel(config)
        telegram = FakeTelegram()

        create(config, telegram, ledger, clock)

        assert [body["name"] for body in telegram.of("createForumTopic")] == [
            "Noust: despliegues",
            "Noust: alertas",
            "Noust: copias",
            "Noust: seguridad",
            "Noust: sistema",
        ]

    def test_asking_again_creates_nothing_twice(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        telegram = FakeTelegram()
        first = create(config, telegram, ledger, clock)

        again = create(config, telegram, ledger, clock)

        assert again.created == {} and again.kept == first.created
        assert len(telegram.of("createForumTopic")) == len(grouping.SUBJECTS)
        assert saved(config) == first.created

    def test_only_the_subjects_with_no_topic_get_one(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config, topics={"deploys": 31, "backups": 32})
        telegram = FakeTelegram()

        result = create(config, telegram, ledger, clock)

        assert result.kept == {"deploys": 31, "backups": 32}
        assert set(result.created) == {"alerts", "security", "system"}
        assert saved(config)["deploys"] == 31 and saved(config)["backups"] == 32
        assert len(telegram.of("createForumTopic")) == 3

    def test_the_owner_of_the_group_needs_no_right_to_manage_topics(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)

        result = create(config, FakeTelegram(status="creator"), ledger, clock)

        assert result.problem is None and len(result.created) == 5

    def test_the_channels_other_settings_are_left_as_they_were(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config, thread_id=7, threads={"backup_failed": 9})

        create(config, FakeTelegram(), ledger, clock)

        assert config.get("notifications.channels.telegram.thread_id") == 7
        assert config.get("notifications.channels.telegram.threads") == {"backup_failed": 9}
        assert config.get("notifications.channels.telegram.bot_token") == BOT_TOKEN

    def test_what_a_topic_does_not_get_a_number_is_said(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        # An answer that does not carry the number cannot be used: the subject stays without one.
        channel(config)
        telegram = FakeTelegram()
        original = telegram._create_forum_topic

        def without_a_number(url: str, body: dict[str, Any]) -> dict[str, Any]:
            created = original(url, body)
            created.pop("message_thread_id")
            return created

        telegram._create_forum_topic = without_a_number  # type: ignore[method-assign]

        result = create(config, telegram, ledger, clock)

        assert result.created == {} and set(result.failed) == set(grouping.SUBJECTS)
        assert saved(config) == {}
        assert result.problem == "refused"


class TestWhenTheGroupCannotTakeThem:
    def test_a_group_that_is_not_a_forum_says_how_to_make_it_one(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        telegram = FakeTelegram(is_forum=False)

        result = create(config, telegram, ledger, clock)

        assert result.problem == "not_forum"
        assert result.created == {} and saved(config) == {}
        assert telegram.of("createForumTopic") == []

    @pytest.mark.parametrize("status", ["member", "left", "kicked"])
    def test_a_bot_that_is_not_an_administrator_is_told_what_to_do(
        self, status: str, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        telegram = FakeTelegram(status=status)

        result = create(config, telegram, ledger, clock)

        assert result.problem == ("not_member" if status in ("left", "kicked") else "no_right")
        assert telegram.of("createForumTopic") == []

    def test_an_administrator_who_may_not_manage_topics_is_told_which_right(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        telegram = FakeTelegram(can_manage_topics=False)

        result = create(config, telegram, ledger, clock)

        assert result.problem == "no_right"
        assert saved(config) == {} and telegram.of("createForumTopic") == []

    def test_a_chat_the_bot_cannot_see_says_what_telegram_said(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)

        result = create(config, FakeTelegram(is_member=False), ledger, clock)

        assert result.problem == "chat_unreachable"
        assert result.detail == "Bad Request: chat not found"

    def test_a_refusal_in_the_middle_keeps_what_was_made_and_the_rest_comes_later(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        telegram = FakeTelegram(rate_limit_after=2)

        first = create(config, telegram, ledger, clock)

        assert first.problem == "refused"
        assert first.detail == "Too Many Requests: retry after 3"
        assert len(first.created) == 2 and len(first.failed) == 3
        assert saved(config) == first.created

        telegram.rate_limit_after = None
        second = create(config, telegram, ledger, clock)

        assert second.problem is None and set(second.created) == set(first.failed)
        assert set(saved(config)) == set(grouping.SUBJECTS)
        assert len(telegram.topics) == 5, "no topic was made twice"

    def test_a_refusal_to_create_says_telegrams_own_words(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        telegram = FakeTelegram(creation_failure=(400, "Bad Request: TOPIC_NAME_INVALID"))

        result = create(config, telegram, ledger, clock)

        assert result.problem == "refused"
        assert result.detail == "Bad Request: TOPIC_NAME_INVALID"
        assert saved(config) == {}


class TestRecreatingWhatWasLost:
    def lose(self, config: Config, ledger: DeliveryLedger, clock: Clock) -> FakeTelegram:
        config.set("notifications.enabled", True)
        channel(config, topics={"deploys": 11, "alerts": 12})
        telegram = FakeTelegram(topics={12})
        notifier = Notifier(
            config,
            opener=telegram,
            ledger=ledger,
            clock=clock,
            placement=lambda _domain: None,
        )
        from tests.support.telegram_double import note

        notifier.notify(note("deploy_failed"))
        return telegram

    def test_a_topic_that_stopped_taking_messages_is_made_again_when_asked(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        telegram = self.lose(config, ledger, clock)

        result = create(config, telegram, ledger, clock, recreate=True)

        assert result.problem is None
        assert result.created["deploys"] not in (11, 12)
        assert saved(config)["deploys"] == result.created["deploys"]
        assert saved(config)["alerts"] == 12
        assert result.kept == {"alerts": 12}

    def test_without_asking_it_is_left_alone(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        telegram = self.lose(config, ledger, clock)

        result = create(config, telegram, ledger, clock)

        assert saved(config)["deploys"] == 11
        assert "deploys" in result.kept and "deploys" not in result.created

    def test_the_mark_goes_with_the_topic_it_was_about(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        telegram = self.lose(config, ledger, clock)
        notes = Notifier(config, ledger=ledger, clock=clock).topic_notes()
        assert notes is not None
        before = notes.standing("telegram", dict(config.get("notifications.channels.telegram")))
        assert [n.topic for n in before] == [11]

        create(config, telegram, ledger, clock, recreate=True)

        after = notes.standing("telegram", dict(config.get("notifications.channels.telegram")))
        assert after == []


class TestWhatItWillNotDo:
    def test_a_channel_that_is_not_telegram_is_refused(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set("notifications.channels.slack", {"webhook_url": "https://hooks.slack.test/x"})

        with pytest.raises(ValidationError, match="not a Telegram channel"):
            topics_of(config, FakeTelegram(), ledger, clock).create("slack", ChannelAdmin(config))

    def test_a_channel_that_does_not_exist_is_refused(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        with pytest.raises(ValidationError, match="no notification channel"):
            topics_of(config, FakeTelegram(), ledger, clock).create("nope", ChannelAdmin(config))

    def test_a_channel_with_no_bot_or_no_chat_says_what_it_lacks(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set("notifications.channels.telegram", {"bot_token": BOT_TOKEN})
        telegram = FakeTelegram()

        with pytest.raises(ValidationError, match="bot token and a chat"):
            create(config, telegram, ledger, clock)

        assert telegram.calls == []

    def test_nothing_is_asked_of_telegram_until_somebody_asks(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        channel(config)
        telegram = FakeTelegram()

        topics_of(config, telegram, ledger, clock)

        assert telegram.calls == []


class TestNothingPersonalLeaks:
    def test_neither_the_answer_nor_the_log_holds_the_bot_or_the_chat(
        self,
        config: Config,
        ledger: DeliveryLedger,
        clock: Clock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        channel(config)
        telegram = FakeTelegram(creation_failure=(400, "Bad Request: nope"))

        with caplog.at_level(logging.DEBUG):
            result = create(config, telegram, ledger, clock)

        shown = caplog.text + json.dumps(result.to_dict())
        for secret in (BOT_TOKEN, CHAT_ID, CHAT_ID.lstrip("-")):
            assert secret not in shown

    def test_a_network_failure_never_quotes_the_address_with_the_token(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        from urllib.error import URLError

        channel(config)

        def down(request: Any, timeout: float | None = None) -> Any:
            raise URLError(f"cannot reach {request.full_url}")

        result = create(config, down, ledger, clock)  # type: ignore[arg-type]

        assert result.problem == "unreachable"
        assert BOT_TOKEN not in json.dumps(result.to_dict())
