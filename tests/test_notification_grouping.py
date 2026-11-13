# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How notifications group: by subject (which topic of a chat) and by thing (which conversation).

One table says what each event is about, derived from the families the pager already used, and a
test fails on an event nobody ruled on or on a family whose failure and recovery would land in
different topics (a reply across topics is refused by Telegram).
"""

from __future__ import annotations

import pytest

from noust.core.notifications import grouping
from noust.core.notifications.instances import TYPES
from noust.core.notifications.model import ALL_KINDS, EVENT_KINDS, TEST_KIND, State
from tests.support.telegram_double import note


class TestSubjects:
    def test_every_event_is_about_one_subject(self) -> None:
        missing = [kind for kind in EVENT_KINDS if grouping.subject_of(kind) is None]

        assert missing == [], f"rule on the subject of: {missing}"
        assert {grouping.subject_of(kind) for kind in EVENT_KINDS} <= set(grouping.SUBJECTS)

    def test_the_subjects_are_the_five_the_console_offers(self) -> None:
        assert grouping.SUBJECTS == ("deploys", "alerts", "backups", "security", "system")
        assert {grouping.subject_of(kind) for kind in EVENT_KINDS} == set(grouping.SUBJECTS)

    def test_what_is_not_an_event_has_no_subject(self) -> None:
        for kind in ALL_KINDS:
            if kind not in EVENT_KINDS:
                assert grouping.subject_of(kind) is None, kind
        assert grouping.subject_of("no such kind") is None
        assert grouping.subject_of(TEST_KIND) is None

    def test_a_failure_and_its_recovery_are_in_the_same_subject(self) -> None:
        by_family: dict[str, set[str | None]] = {}
        for kind, family in grouping.FAMILY.items():
            by_family.setdefault(family, set()).add(grouping.subject_of(kind))

        split = {family: found for family, found in by_family.items() if len(found) != 1}

        assert split == {}

    def test_a_family_is_made_of_events(self) -> None:
        assert set(grouping.FAMILY) <= set(EVENT_KINDS)
        assert set(grouping.OPENING) == set(grouping.FAMILY.values())

    @pytest.mark.parametrize(
        ("kind", "subject"),
        [
            ("deploy_started", "deploys"),
            ("deploy_hook_failed", "deploys"),
            ("app_unreachable", "alerts"),
            ("app_recovered", "alerts"),
            ("alert_firing", "alerts"),
            ("node_unreachable", "alerts"),
            ("backup_failed", "backups"),
            ("restore_success", "backups"),
            ("destination_unreachable", "backups"),
            ("backup_missing", "backups"),
            ("node_host_key_changed", "security"),
            ("security_release", "security"),
            ("approval_requested", "security"),
            ("access_change_reverted", "security"),
            ("disk_threshold", "system"),
            ("server_rebooted", "system"),
            ("cert_expiring", "system"),
            ("os_patches", "system"),
        ],
    )
    def test_where_some_events_go(self, kind: str, subject: str) -> None:
        assert grouping.subject_of(kind) == subject

    def test_the_kinds_of_a_subject_follow_the_order_of_the_events(self) -> None:
        covered = [kind for subject in grouping.SUBJECTS for kind in grouping.kinds_of(subject)]

        assert sorted(covered) == sorted(EVENT_KINDS)
        assert len(covered) == len(set(covered))
        deploys = grouping.kinds_of("deploys")
        assert list(deploys) == [kind for kind in EVENT_KINDS if kind in deploys]

    def test_asking_for_a_subject_that_does_not_exist_is_empty(self) -> None:
        assert grouping.kinds_of("nothing") == ()


class TestTheSameThing:
    def test_the_failure_and_its_recovery_are_one_thing(self) -> None:
        failed = note("app_unreachable", State.FAILED)
        fixed = note("app_recovered", State.OK)

        assert grouping.thing_key(failed) == grouping.thing_key(fixed)

    def test_another_application_or_server_is_another_thing(self) -> None:
        base = grouping.thing_key(note("app_unreachable"))

        assert grouping.thing_key(note("app_unreachable", domain="blog.example.com")) != base
        assert grouping.thing_key(note("app_unreachable", server="web-2")) != base

    def test_a_deploy_and_what_the_app_does_are_not_one_thing(self) -> None:
        assert grouping.thing_key(note("deploy_failed")) != grouping.thing_key(
            note("app_unreachable")
        )

    def test_the_start_of_a_deploy_belongs_to_its_deploy(self) -> None:
        assert grouping.family_of("deploy_started") == "deploy"
        assert grouping.thing_key(note("deploy_started", State.PROGRESS)) == grouping.thing_key(
            note("deploy_success", State.OK)
        )

    def test_the_key_is_what_the_pager_has_always_used(self) -> None:
        assert grouping.thing_key(note("app_unreachable")) == "noust/web-1/app/shop.example.com"

    def test_an_event_with_no_way_back_has_no_family(self) -> None:
        assert grouping.family_of("cert_expiring") is None
        assert grouping.family_of("approval_requested") is None
        assert grouping.family_of(TEST_KIND) is None


class TestTheTopicOfAnEvent:
    def test_without_any_topic_it_is_the_chat_itself(self) -> None:
        assert grouping.topic_for({}, "deploy_failed") is None

    def test_the_channels_one_topic_is_the_last_resort(self) -> None:
        topic = grouping.topic_for({"thread_id": 7}, "deploy_failed")

        assert topic == grouping.Topic(7, None, "channel")

    def test_a_subject_has_its_own_topic(self) -> None:
        settings = {"thread_id": 7, "topics": {"deploys": 11}}

        assert grouping.topic_for(settings, "deploy_failed") == grouping.Topic(
            11, "deploys", "subject"
        )
        assert grouping.topic_for(settings, "backup_failed") == grouping.Topic(7, None, "channel")

    def test_an_event_of_its_own_beats_its_subject(self) -> None:
        settings = {"threads": {"deploy_failed": 5}, "topics": {"deploys": 11}}

        assert grouping.topic_for(settings, "deploy_failed") == grouping.Topic(5, None, "event")
        assert grouping.topic_for(settings, "deploy_success") == grouping.Topic(
            11, "deploys", "subject"
        )

    @pytest.mark.parametrize("bad", ["", "abc", 0, -3, None, True, 1.5])
    def test_a_value_that_is_not_a_topic_is_ignored(self, bad: object) -> None:
        assert grouping.topic_for({"thread_id": bad}, "deploy_failed") is None
        assert grouping.topic_for({"topics": {"deploys": bad}}, "deploy_failed") is None

    def test_a_topic_may_be_written_as_text(self) -> None:
        assert grouping.topic_for({"topics": {"alerts": " 12 "}}, "unit_failed") == grouping.Topic(
            12, "alerts", "subject"
        )

    def test_settings_that_are_not_mappings_are_ignored(self) -> None:
        assert grouping.topic_for({"topics": "deploys=3", "threads": [1]}, "deploy_failed") is None

    def test_what_is_not_an_event_goes_to_the_channels_topic(self) -> None:
        settings = {"thread_id": 7, "topics": {"deploys": 11}}

        assert grouping.topic_for(settings, TEST_KIND) == grouping.Topic(7, None, "channel")
        assert grouping.topic_for(settings, "digest") == grouping.Topic(7, None, "channel")


class TestTheTopicsInUse:
    def test_they_are_every_number_the_channel_can_post_in(self) -> None:
        settings = {"thread_id": 7, "threads": {"backup_failed": "9"}, "topics": {"deploys": 11}}

        assert grouping.topics_in_use(settings) == {7, 9, 11}

    def test_what_is_not_a_topic_is_not_in_use(self) -> None:
        settings = {"thread_id": "x", "threads": {"a": 0}, "topics": {"deploys": None}}

        assert grouping.topics_in_use(settings) == set()
        assert grouping.topics_in_use({}) == set()
        assert grouping.topics_in_use({"threads": 3, "topics": "x"}) == set()


class TestWhatEachChannelCanDo:
    def test_every_type_says_what_it_can_do(self) -> None:
        assert set(grouping.GROUPING) == set(TYPES)

    def test_telegram_has_topics_and_replies(self) -> None:
        telegram = grouping.GROUPING["telegram"]

        assert telegram.topics and telegram.replies and telegram.reason is None

    def test_mail_has_conversations_but_no_topics(self) -> None:
        email = grouping.GROUPING["email"]

        assert email.replies and not email.topics and email.reason is None

    def test_what_a_channel_cannot_do_says_why(self) -> None:
        for name, can in grouping.GROUPING.items():
            if not can.topics and not can.replies:
                assert can.reason in grouping.REASONS, name

    def test_the_webhooks_of_the_team_chats_cannot_make_threads(self) -> None:
        assert grouping.GROUPING["slack"].reason == "incoming_webhook"
        assert grouping.GROUPING["mattermost"].reason == "incoming_webhook"
        assert grouping.GROUPING["teams"].reason == "workflow_webhook"
        assert grouping.GROUPING["lark"].reason == "custom_bot"

    def test_a_pager_already_groups_by_incident(self) -> None:
        assert grouping.GROUPING["pagerduty"].reason == "incidents"

    def test_a_channel_that_can_do_something_gives_no_reason(self) -> None:
        for name, can in grouping.GROUPING.items():
            if can.topics or can.replies:
                assert can.reason is None, name
