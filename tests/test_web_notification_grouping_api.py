# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What ``/api/notifications`` says about grouping: the subject of each event, what each channel type
can do, the topics of a Telegram channel and the mark of one that stopped taking messages.

Item 190. The console keeps no table of its own: it reads the subject of every event and the
capabilities of every channel from here, so a new event or channel cannot be offered wrongly.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.config import Config
from noust.core.notification_state import NotificationState
from noust.core.notifications import grouping
from noust.core.notifications.model import EVENT_KINDS
from noust.core.notifications.topic_notes import TopicNotes
from noust.core.store import NoustStore
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager
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
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def client(tmp_path: Path, store: NoustStore, config_path: Path) -> TestClient:
    app: FastAPI = create_app(
        SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000)
    )
    signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    response = signed_in.post("/api/auth/login", json={"token": token})
    assert response.status_code == 200, response.text
    signed_in.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return signed_in


def elevate(client: TestClient) -> None:
    response = client.post(
        "/api/auth/elevate", json={"token": get_token_manager().generate_master_token()}
    )
    assert response.status_code == 200, response.text


def telegram(client: TestClient) -> dict:
    listed = client.get("/api/notifications/channels").json()
    return next(item for item in listed["items"] if item["name"] == "telegram")


def save_telegram(client: TestClient, **settings: object) -> None:
    elevate(client)
    response = client.put(
        "/api/notifications/channels/telegram",
        json={"settings": {"bot_token": BOT_TOKEN, "chat_id": CHAT_ID, **settings}},
    )
    assert response.status_code == 200, response.text


class TestTheSubjectOfEachEvent:
    def test_every_event_says_what_it_is_about(self, client: TestClient) -> None:
        items = client.get("/api/notifications/events").json()["items"]

        assert [item["kind"] for item in items] == list(EVENT_KINDS)
        assert {item["subject"] for item in items} == set(grouping.SUBJECTS)
        by_kind = {item["kind"]: item["subject"] for item in items}
        assert by_kind["deploy_failed"] == "deploys"
        assert by_kind["backup_failed"] == "backups"
        assert by_kind["app_recovered"] == "alerts"

    def test_the_subjects_come_in_the_order_the_console_offers_them(
        self, client: TestClient
    ) -> None:
        body = client.get("/api/notifications/events").json()

        assert body["subjects"] == ["deploys", "alerts", "backups", "security", "system"]

    def test_the_answer_is_the_one_table(self, client: TestClient) -> None:
        items = client.get("/api/notifications/events").json()["items"]

        assert {item["kind"]: item["subject"] for item in items} == {
            kind: grouping.subject_of(kind) for kind in EVENT_KINDS
        }


class TestWhatEachTypeCanDo:
    def test_every_type_says_it(self, client: TestClient) -> None:
        types = {
            t["name"]: t["grouping"]
            for t in client.get("/api/notifications/channels").json()["types"]
        }

        assert types["telegram"] == {"topics": True, "replies": True, "reason": None}
        assert types["email"] == {"topics": False, "replies": True, "reason": None}
        assert types["slack"] == {"topics": False, "replies": False, "reason": "incoming_webhook"}
        assert types["pagerduty"]["reason"] == "incidents"
        assert set(types) == set(grouping.GROUPING)


class TestTheTopicsOfATelegramChannel:
    def test_a_topic_per_subject_is_saved_and_read_back(self, client: TestClient) -> None:
        save_telegram(client, topics={"deploys": 11, "alerts": "12"})

        channel = telegram(client)

        assert channel["settings"]["topics"] == {"deploys": 11, "alerts": "12"}
        assert channel["settings"]["bot_token"] == "***"

    def test_a_subject_that_does_not_exist_is_refused_with_the_subjects(
        self, client: TestClient
    ) -> None:
        elevate(client)

        response = client.put(
            "/api/notifications/channels/telegram",
            json={"settings": {"topics": {"cooking": 4}}},
        )

        assert response.status_code == 400
        assert "no subject 'cooking'" in response.text
        assert "deploys, alerts, backups, security, system" in response.text

    def test_a_topic_that_is_not_a_number_is_refused_naming_the_subject(
        self, client: TestClient
    ) -> None:
        elevate(client)

        response = client.put(
            "/api/notifications/channels/telegram",
            json={"settings": {"topics": {"deploys": "twelve"}}},
        )

        assert response.status_code == 400
        assert "topics.deploys must be a topic number" in response.text

    def test_the_topics_are_cleared_by_writing_none(self, client: TestClient) -> None:
        save_telegram(client, topics={"deploys": 11})

        client.put("/api/notifications/channels/telegram", json={"settings": {"topics": {}}})

        assert telegram(client)["settings"]["topics"] == {}


class TestAChannelWhoseTopicStoppedTakingMessages:
    def mark(self, store: NoustStore, topic: int, subject: str | None, reason: str) -> None:
        TopicNotes(NotificationState(store)).lost("telegram", topic, subject, reason)

    def test_a_channel_with_no_trouble_has_no_notices(self, client: TestClient) -> None:
        save_telegram(client, topics={"deploys": 11})

        assert telegram(client)["notices"] == []

    def test_the_topic_that_failed_is_said_in_telegrams_words(
        self, client: TestClient, store: NoustStore
    ) -> None:
        save_telegram(client, topics={"deploys": 11, "alerts": 12})
        self.mark(store, 11, "deploys", "Bad Request: message thread not found")

        (notice,) = telegram(client)["notices"]

        assert notice["code"] == "topic_lost"
        assert (notice["topic"], notice["subject"]) == (11, "deploys")
        assert notice["reason"] == "Bad Request: message thread not found"
        assert notice["at"] > 0

    def test_a_topic_the_channel_no_longer_uses_is_not_said(
        self, client: TestClient, store: NoustStore
    ) -> None:
        save_telegram(client, topics={"deploys": 21})
        self.mark(store, 11, "deploys", "gone")

        assert telegram(client)["notices"] == []

    def test_the_one_topic_of_the_channel_and_the_topic_of_an_event_count(
        self, client: TestClient, store: NoustStore
    ) -> None:
        save_telegram(client, thread_id=7, threads={"backup_failed": 9})
        self.mark(store, 7, None, "gone")
        self.mark(store, 9, None, "closed")

        notices = telegram(client)["notices"]

        assert sorted(notice["topic"] for notice in notices) == [7, 9]

    def test_other_channels_have_none(self, client: TestClient, store: NoustStore) -> None:
        self.mark(store, 7, None, "gone")
        listed = client.get("/api/notifications/channels").json()["items"]

        assert all(item["notices"] == [] for item in listed if item["name"] != "telegram")


class TestCreatingTheTopics:
    @pytest.fixture
    def telegram_bot(self, monkeypatch: pytest.MonkeyPatch) -> FakeTelegram:
        """A group the bot is in, behind the notifier the endpoint builds."""
        import noust.core.notifier as notifier_module
        import noust.web.api.notifications as api
        from noust.core.notifier import Notifier

        fake = FakeTelegram()
        monkeypatch.setattr(notifier_module, "_resolve_host", lambda host: ("93.184.216.34",))
        monkeypatch.setattr(api, "_notifier", lambda: Notifier(Config(), opener=fake))
        return fake

    def post(self, client: TestClient, **body: object) -> object:
        return client.post("/api/notifications/channels/telegram/topics", json=body)

    def test_it_needs_sudo_mode(self, client: TestClient, telegram_bot: FakeTelegram) -> None:
        response = self.post(client)

        assert response.status_code == 403  # type: ignore[attr-defined]
        assert response.json()["error"] == "elevation_required"  # type: ignore[attr-defined]
        assert telegram_bot.calls == []

    def test_the_topics_are_created_and_their_numbers_saved(
        self, client: TestClient, telegram_bot: FakeTelegram
    ) -> None:
        save_telegram(client)

        response = self.post(client)

        assert response.status_code == 200, response.text  # type: ignore[attr-defined]
        body = response.json()  # type: ignore[attr-defined]
        assert body["problem"] is None and body["failed"] == []
        assert set(body["created"]) == set(grouping.SUBJECTS)
        assert telegram(client)["settings"]["topics"] == body["topics"]

    def test_a_second_time_creates_nothing(
        self, client: TestClient, telegram_bot: FakeTelegram
    ) -> None:
        save_telegram(client)
        first = self.post(client).json()  # type: ignore[attr-defined]

        again = self.post(client).json()  # type: ignore[attr-defined]

        assert again["created"] == {} and again["kept"] == first["created"]
        assert len(telegram_bot.of("createForumTopic")) == len(grouping.SUBJECTS)

    def test_a_group_that_is_not_a_forum_is_an_answer_and_not_an_error(
        self, client: TestClient, telegram_bot: FakeTelegram
    ) -> None:
        save_telegram(client)
        telegram_bot.is_forum = False

        response = self.post(client)

        assert response.status_code == 200  # type: ignore[attr-defined]
        assert response.json()["problem"] == "not_forum"  # type: ignore[attr-defined]
        assert telegram(client)["settings"]["topics"] in ({}, "")

    def test_what_was_made_before_a_refusal_is_kept(
        self, client: TestClient, telegram_bot: FakeTelegram
    ) -> None:
        save_telegram(client)
        telegram_bot.rate_limit_after = 2

        body = self.post(client).json()  # type: ignore[attr-defined]

        assert body["problem"] == "refused" and len(body["created"]) == 2
        assert telegram(client)["settings"]["topics"] == body["created"]

    def test_a_channel_that_is_not_telegram_is_refused(
        self, client: TestClient, telegram_bot: FakeTelegram
    ) -> None:
        elevate(client)

        response = client.post("/api/notifications/channels/slack/topics", json={})

        assert response.status_code == 400
        assert "not a Telegram channel" in response.text

    def test_a_channel_with_no_bot_yet_says_so(
        self, client: TestClient, telegram_bot: FakeTelegram
    ) -> None:
        elevate(client)

        response = self.post(client)

        assert response.status_code == 400  # type: ignore[attr-defined]
        assert "bot token and a chat" in response.text  # type: ignore[attr-defined]

    def test_it_is_audited_without_the_chat_or_the_numbers(
        self,
        client: TestClient,
        telegram_bot: FakeTelegram,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import noust.web.api.notifications as api

        events: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            api.audit, "record", lambda event, **fields: events.append((event, fields))
        )
        save_telegram(client)
        events.clear()

        self.post(client)

        ((event, fields),) = events
        assert event == "notify.topics.create"
        assert fields["target"] == "channel:telegram"
        assert fields["details"]["created"] == sorted(grouping.SUBJECTS)
        shown = str(fields)
        assert CHAT_ID not in shown and BOT_TOKEN not in shown

    def test_the_answer_never_holds_the_bot_or_the_chat(
        self, client: TestClient, telegram_bot: FakeTelegram
    ) -> None:
        save_telegram(client)

        text = self.post(client).text  # type: ignore[attr-defined]

        assert BOT_TOKEN not in text and CHAT_ID not in text

    def test_recreating_is_asked_for(
        self, client: TestClient, telegram_bot: FakeTelegram, store: NoustStore
    ) -> None:
        save_telegram(client, topics={"deploys": 11})
        TopicNotes(NotificationState(store)).lost("telegram", 11, "deploys", "gone")

        kept = self.post(client).json()  # type: ignore[attr-defined]
        assert kept["kept"]["deploys"] == 11

        made = self.post(client, recreate=True).json()  # type: ignore[attr-defined]
        assert made["created"]["deploys"] != 11
