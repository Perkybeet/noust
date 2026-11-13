# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust notify``: the topics of a Telegram group, by subject.

The command line says the same as the console: a topic per subject written without editing the
configuration, and what the events are about. Item 190.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.config import Config
from noust.core.notifications import grouping
from noust.core.store import NoustStore
from tests.support.telegram_double import BOT_TOKEN, CHAT_ID, FakeTelegram


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "etc" / "noust" / "config.yaml"
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", path)
    Config.reset_instance()
    try:
        yield path
    finally:
        Config.reset_instance()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        NoustStore.reset_instance()


@pytest.fixture
def run(config_path: Path, store: NoustStore):  # type: ignore[no-untyped-def]
    runner = CliRunner()

    def invoke(*args: str, input: str | None = None):  # type: ignore[no-untyped-def]
        Config.reset_instance()
        return runner.invoke(root_cli, list(args), input=input)

    return invoke


def telegram(run) -> None:  # type: ignore[no-untyped-def]
    result = run(
        "notify",
        "channel",
        "set",
        "telegram",
        "--set",
        f"chat_id={CHAT_ID}",
        "--secret-stdin",
        "bot_token",
        input=BOT_TOKEN + "\n",
    )
    assert result.exit_code == 0, result.output


def topics() -> dict:
    Config.reset_instance()
    return dict(Config().get("notifications.channels.telegram.topics") or {})


class TestATopicPerSubject:
    def test_subjects_are_given_their_topics(self, run) -> None:  # type: ignore[no-untyped-def]
        telegram(run)

        result = run(
            "notify", "channel", "set", "telegram", "--topic", "deploys=11", "--topic", "alerts=12"
        )

        assert result.exit_code == 0, result.output
        assert topics() == {"deploys": 11, "alerts": 12}

    def test_what_is_not_mentioned_is_kept(self, run) -> None:  # type: ignore[no-untyped-def]
        telegram(run)
        run("notify", "channel", "set", "telegram", "--topic", "deploys=11", "--topic", "alerts=12")

        run("notify", "channel", "set", "telegram", "--topic", "alerts=15")

        assert topics() == {"deploys": 11, "alerts": 15}

    def test_an_empty_number_stops_a_subject_having_a_topic(self, run) -> None:  # type: ignore[no-untyped-def]
        telegram(run)
        run("notify", "channel", "set", "telegram", "--topic", "deploys=11", "--topic", "alerts=12")

        result = run("notify", "channel", "set", "telegram", "--topic", "deploys=")

        assert result.exit_code == 0, result.output
        assert topics() == {"alerts": 12}

    def test_every_topic_can_be_cleared(self, run) -> None:  # type: ignore[no-untyped-def]
        telegram(run)
        run("notify", "channel", "set", "telegram", "--topic", "deploys=11")

        result = run("notify", "channel", "set", "telegram", "--clear-topics")

        assert result.exit_code == 0, result.output
        assert topics() == {}

    def test_a_subject_that_does_not_exist_is_refused_with_the_subjects(self, run) -> None:  # type: ignore[no-untyped-def]
        telegram(run)

        result = run("notify", "channel", "set", "telegram", "--topic", "cooking=4")

        assert result.exit_code == 1
        assert "no subject 'cooking'" in result.output
        assert "deploys, alerts, backups, security, system" in result.output
        assert topics() == {}

    @pytest.mark.parametrize("bad", ["deploys=abc", "deploys", "=4", "deploys=-1", "deploys=1.5"])
    def test_a_number_that_is_not_one_is_a_usage_error(self, run, bad: str) -> None:  # type: ignore[no-untyped-def]
        telegram(run)

        result = run("notify", "channel", "set", "telegram", "--topic", bad)

        assert result.exit_code == 2
        assert "--topic" in result.output

    def test_a_channel_that_has_no_topics_says_so(self, run) -> None:  # type: ignore[no-untyped-def]
        result = run("notify", "channel", "set", "slack", "--topic", "deploys=1")

        assert result.exit_code == 1
        assert "no setting 'topics'" in result.output


class TestTheSubjectOfTheEvents:
    def test_the_json_carries_the_subject_of_each_event(self, run) -> None:  # type: ignore[no-untyped-def]
        result = run("--json", "notify", "events")

        events = {e["kind"]: e for e in json.loads(result.output)}
        assert events["deploy_failed"]["subject"] == "deploys"
        assert events["app_recovered"]["subject"] == "alerts"

    def test_the_table_has_a_subject_column(self, run) -> None:  # type: ignore[no-untyped-def]
        result = run("notify", "events")

        assert result.exit_code == 0
        header = result.output.splitlines()[0]
        assert "Subject" in header
        assert all(subject in result.output for subject in grouping.SUBJECTS)


@pytest.fixture
def telegram_bot(monkeypatch: pytest.MonkeyPatch) -> FakeTelegram:
    """A group the bot is in, behind the notifier the command builds."""
    import noust.cli.commands.notify as notify_module
    import noust.core.notifier as notifier_module

    fake = FakeTelegram()
    real = notifier_module.Notifier
    monkeypatch.setattr(notifier_module, "_resolve_host", lambda host: ("93.184.216.34",))
    monkeypatch.setattr(
        notify_module,
        "Notifier",
        lambda config=None, **options: real(config, opener=fake, **options),
    )
    return fake


class TestListingTheTopics:
    def test_each_subject_is_listed_with_its_topic_and_what_it_covers(
        self, run, telegram_bot
    ) -> None:  # type: ignore[no-untyped-def]
        telegram(run)
        run("notify", "channel", "set", "telegram", "--topic", "deploys=11")

        result = run("notify", "telegram-topics")

        assert result.exit_code == 0, result.output
        for subject in grouping.SUBJECTS:
            assert subject in result.output
        assert "11" in result.output and "deploy_failed" in result.output
        assert telegram_bot.calls == [], "listing asks nothing of Telegram"

    def test_the_json_form(self, run, telegram_bot) -> None:  # type: ignore[no-untyped-def]
        telegram(run)
        run("notify", "channel", "set", "telegram", "--topic", "alerts=12")

        result = run("--json", "notify", "telegram-topics")

        rows = {row["subject"]: row for row in json.loads(result.output)["subjects"]}
        assert rows["alerts"]["topic"] == 12 and rows["deploys"]["topic"] is None
        assert "app_unreachable" in rows["alerts"]["events"]

    def test_a_topic_that_stopped_taking_messages_is_said_with_telegrams_words(
        self, run, telegram_bot, store
    ) -> None:  # type: ignore[no-untyped-def]
        from noust.core.notification_state import NotificationState
        from noust.core.notifications.topic_notes import TopicNotes

        telegram(run)
        run("notify", "channel", "set", "telegram", "--topic", "deploys=11")
        TopicNotes(NotificationState(store)).lost(
            "telegram", 11, "deploys", "Bad Request: message thread not found"
        )

        listed = run("notify", "telegram-topics")
        as_json = run("--json", "notify", "telegram-topics")

        assert listed.exit_code == 0, listed.output
        assert "11 (lost)" in listed.output
        assert "Bad Request: message thread not found" in listed.output
        rows = {row["subject"]: row for row in json.loads(as_json.output)["subjects"]}
        assert rows["deploys"]["lost"] is True and rows["alerts"]["lost"] is False

    def test_a_channel_that_is_not_telegram_is_refused(self, run, telegram_bot) -> None:  # type: ignore[no-untyped-def]
        result = run("notify", "telegram-topics", "--channel", "slack")

        assert result.exit_code == 1
        assert "not a Telegram channel" in result.output


class TestCreatingTheTopics:
    def test_the_missing_topics_are_created_and_saved(self, run, telegram_bot) -> None:  # type: ignore[no-untyped-def]
        telegram(run)

        result = run("notify", "telegram-topics", "--create")

        assert result.exit_code == 0, result.output
        assert set(topics()) == set(grouping.SUBJECTS)
        assert len(telegram_bot.of("createForumTopic")) == 5
        assert "created" in result.output.lower()

    def test_asking_again_makes_nothing_twice(self, run, telegram_bot) -> None:  # type: ignore[no-untyped-def]
        telegram(run)
        run("notify", "telegram-topics", "--create")

        result = run("notify", "telegram-topics", "--create")

        assert result.exit_code == 0, result.output
        assert len(telegram_bot.of("createForumTopic")) == 5

    def test_a_rehearsal_asks_nothing(self, run, telegram_bot) -> None:  # type: ignore[no-untyped-def]
        telegram(run)

        result = run("notify", "telegram-topics", "--create", "--dry-run")

        assert result.exit_code == 0, result.output
        assert telegram_bot.calls == [] and topics() == {}
        assert "would" in result.output.lower()

    def test_a_group_that_is_not_a_forum_says_how_to_make_it_one(self, run, telegram_bot) -> None:  # type: ignore[no-untyped-def]
        telegram(run)
        telegram_bot.is_forum = False

        result = run("notify", "telegram-topics", "--create")

        assert result.exit_code == 1
        assert "Topics" in result.output and "owner" in result.output
        assert topics() == {}

    def test_a_bot_without_the_right_says_which(self, run, telegram_bot) -> None:  # type: ignore[no-untyped-def]
        telegram(run)
        telegram_bot.can_manage_topics = False

        result = run("notify", "telegram-topics", "--create")

        assert result.exit_code == 1
        assert "Manage topics" in result.output

    def test_what_telegram_said_is_shown_as_it_said_it(self, run, telegram_bot) -> None:  # type: ignore[no-untyped-def]
        telegram(run)
        telegram_bot.creation_failure = (400, "Bad Request: TOPIC_NAME_INVALID")

        result = run("notify", "telegram-topics", "--create")

        assert result.exit_code == 1
        assert "Bad Request: TOPIC_NAME_INVALID" in result.output

    def test_what_was_made_before_a_refusal_is_saved(self, run, telegram_bot) -> None:  # type: ignore[no-untyped-def]
        telegram(run)
        telegram_bot.rate_limit_after = 2

        result = run("notify", "telegram-topics", "--create")

        assert result.exit_code == 1
        assert len(topics()) == 2

    def test_neither_the_bot_nor_the_chat_is_printed(self, run, telegram_bot) -> None:  # type: ignore[no-untyped-def]
        telegram(run)

        result = run("--json", "notify", "telegram-topics", "--create")

        assert BOT_TOKEN not in result.output and CHAT_ID not in result.output

    def test_a_channel_with_no_bot_says_so(self, run, telegram_bot) -> None:  # type: ignore[no-untyped-def]
        result = run("notify", "telegram-topics", "--create")

        assert result.exit_code == 1
        assert "bot token and a chat" in result.output
