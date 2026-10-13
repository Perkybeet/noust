# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Channel instances: several of one type, each with its own events, route and hours.

Checklist K-27 (more than one channel of the same type, the base of K-26) and
the shape K-25 needs (``notifications.channels.<name>.events``).
"""

from __future__ import annotations

import pytest

from noust.core.config import DEFAULT_CONFIG
from noust.core.notifications.instances import (
    MAX_INSTANCES,
    TYPE_NAMES,
    TYPES,
    instance_names,
    resolve_instances,
    validate_channels,
)

SLACK = "https://hooks.slack.test/services/T0/B0/secret"


def test_the_default_blocks_are_the_default_instances() -> None:
    names = instance_names(DEFAULT_CONFIG["notifications"]["channels"])

    assert names == TYPE_NAMES
    assert TYPE_NAMES[:5] == ("webhook", "slack", "discord", "telegram", "email")


def test_a_block_named_like_a_type_is_an_instance_of_it() -> None:
    (instance,) = resolve_instances({"slack": {"webhook_url": SLACK}})

    assert (instance.name, instance.type) == ("slack", "slack")
    assert instance.configured and instance.enabled


def test_a_second_instance_has_its_own_name_and_type() -> None:
    instances = resolve_instances(
        {
            "slack": {"webhook_url": SLACK},
            "slack-staging": {"type": "slack", "webhook_url": SLACK + "2"},
            "alerts": {"type": "ntfy", "webhook_url": "https://ntfy.test/noust"},
        }
    )

    assert [(i.name, i.type) for i in instances] == [
        ("slack", "slack"),
        ("alerts", "ntfy"),
        ("slack-staging", "slack"),
    ]


def test_the_operators_instances_come_after_the_defaults_by_name() -> None:
    channels = {
        "zeta": {"type": "teams", "webhook_url": "https://x.test/a"},
        "alpha": {"type": "teams", "webhook_url": "https://x.test/b"},
        "telegram": {},
        "webhook": {},
    }

    assert instance_names(channels) == ("webhook", "telegram", "alpha", "zeta")


def test_an_unconfigured_instance_is_not_configured() -> None:
    instances = {i.name: i for i in resolve_instances(DEFAULT_CONFIG["notifications"]["channels"])}

    assert not any(i.configured for name, i in instances.items() if name != "email")


@pytest.mark.parametrize(
    ("name", "block", "configured"),
    [
        ("webhook", {"webhook_url": SLACK}, True),
        ("telegram", {"bot_token": "1:abc"}, False),
        ("telegram", {"bot_token": "1:abc", "chat_id": "-1001234567890"}, True),
        ("pushover", {"user_key": "u" * 30}, False),
        ("pushover", {"user_key": "u" * 30, "app_token": "a" * 30}, True),
        ("gotify", {"webhook_url": "https://g.test"}, False),
        ("gotify", {"webhook_url": "https://g.test", "token": "t"}, True),
        ("ntfy", {"webhook_url": "https://ntfy.test/x"}, True),
        ("teams", {"webhook_url": "https://x.test/x"}, True),
        ("email", {"enabled": True}, True),
        ("email", {}, False),
    ],
)
def test_what_each_type_needs(name: str, block: dict, configured: bool) -> None:
    (instance,) = resolve_instances({name: block})

    assert instance.configured is configured


def test_email_is_off_unless_enabled_and_the_rest_are_on_unless_disabled() -> None:
    slack, email = resolve_instances({"email": {}, "slack": {"webhook_url": SLACK}})
    off = resolve_instances({"slack": {"webhook_url": SLACK, "enabled": False}})[0]

    assert not email.enabled and slack.enabled and not off.enabled


def test_secrets_are_the_types_credentials() -> None:
    (instance,) = resolve_instances({"webhook": {"webhook_url": SLACK, "secret": "signing-secret"}})
    (pushover,) = resolve_instances(
        {"pushover": {"user_key": "ukey", "app_token": "atok", "device": "phone"}}
    )

    assert set(instance.secrets) == {SLACK, "signing-secret"}
    assert set(pushover.secrets) == {"ukey", "atok"}


def test_the_shared_settings_are_read() -> None:
    (instance,) = resolve_instances(
        {
            "oncall": {
                "type": "pushover",
                "user_key": "u",
                "app_token": "a",
                "events": {"deploy_failed": True, "backup_success": False},
                "route": {"projects": ["shop"], "min_severity": "failed"},
                "quiet": {"start": "22:00", "end": "07:00", "timezone": "Europe/Madrid"},
            }
        }
    )

    assert instance.routing.events == {"deploy_failed": True, "backup_success": False}
    assert instance.routing.route.projects == ("shop",)
    assert instance.routing.quiet is not None
    assert instance.routing.quiet.timezone == "Europe/Madrid"


def test_an_unreadable_block_is_left_out_not_fatal(caplog: pytest.LogCaptureFixture) -> None:
    instances = resolve_instances(
        {"slack": {"webhook_url": SLACK}, "Bad Name": {"type": "slack"}, "ghost": {"type": "fax"}}
    )

    assert [i.name for i in instances] == ["slack"]
    assert "Bad Name" in caplog.text and "ghost" in caplog.text


def test_a_setting_written_by_hand_that_means_nothing_is_ignored_and_the_channel_stays(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An alert matters more than the narrowing someone typed wrongly."""
    (instance,) = resolve_instances(
        {
            "slack": {
                "webhook_url": SLACK,
                "route": {"apps": ["bad name"]},
                "quiet": {"start": "late", "end": "07:00"},
                "events": {"no_such_event": True},
                "enabled": "yes",
            }
        }
    )

    assert instance.enabled and instance.configured
    assert not instance.routing.route.narrows
    assert instance.routing.quiet is None
    assert instance.routing.events == {}
    for what in ("route", "quiet", "events"):
        assert f"notifications.channels.slack.{what}" in caplog.text


class TestValidation:
    def test_the_defaults_and_a_sensible_setup_pass(self) -> None:
        validate_channels(DEFAULT_CONFIG["notifications"]["channels"])
        validate_channels(
            {
                "slack": {"webhook_url": SLACK},
                "slack-staging": {"type": "slack", "webhook_url": SLACK, "enabled": False},
                "tg": {
                    "type": "telegram",
                    "bot_token": "110201543:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
                    "chat_id": "-1001234567890",
                    "thread_id": 7,
                    "threads": {"deploy_failed": 11, "backup_failed": "12"},
                },
            }
        )

    def test_nothing_is_valid(self) -> None:
        validate_channels(None)
        validate_channels({})

    @pytest.mark.parametrize(
        ("channels", "needle"),
        [
            ("nope", "must be a mapping"),
            ({"Slack": {}}, "named with lower-case"),
            ({"x" * 40: {"type": "slack"}}, "at most 32"),
            ({"slack": "http://x"}, "mapping of settings"),
            ({"mine": {}}, "not a channel type"),
            ({"mine": {"type": "fax"}}, "not a channel type"),
            ({"slack": {"type": "webhook"}}, "cannot be a webhook"),
            ({"slack": {"colour": "red"}}, "no setting 'colour'"),
            ({"slack": {"webhook_url": "ftp://x/y"}}, "http:// or https://"),
            ({"slack": {"enabled": "yes"}}, "enabled must be true or false"),
            ({"slack": {"events": {"no_such_event": True}}}, "no event 'no_such_event'"),
            ({"slack": {"events": {"deploy_failed": "yes"}}}, "deploy_failed must be true"),
            ({"slack": {"events": ["deploy_failed"]}}, "events must be a mapping"),
            ({"slack": {"route": {"apps": ["bad name"]}}}, "slack.route.apps"),
            ({"slack": {"quiet": {"start": "22:00", "end": "22:00"}}}, "slack.quiet"),
            ({"telegram": {"chat_id": "not-a-chat"}}, "not a Telegram chat id"),
            ({"telegram": {"thread_id": "abc"}}, "thread_id must be a topic number"),
            ({"telegram": {"thread_id": 0}}, "positive topic number"),
            ({"telegram": {"threads": {"nope": 1}}}, "no event 'nope'"),
            ({"discord": {"mention_role_id": "everyone"}}, "mention_role_id"),
            ({"email": {"recipients": 5}}, "recipients must be a list"),
        ],
    )
    def test_what_cannot_be_read_is_refused_with_the_place_and_the_reason(
        self, channels: object, needle: str
    ) -> None:
        with pytest.raises(ValueError, match=needle):
            validate_channels(channels)

    def test_there_is_a_ceiling(self) -> None:
        many = {f"c{i}": {"type": "slack"} for i in range(MAX_INSTANCES + 1)}

        with pytest.raises(ValueError, match="at most"):
            validate_channels(many)


def test_every_type_has_its_secrets_marked_as_credentials() -> None:
    from noust.core.config import is_secret_key

    for spec in TYPES.values():
        for key in spec.secret_keys:
            assert is_secret_key(key), f"{spec.name}.{key} would be shown in the clear"
        for field in spec.fields:
            if not field.secret:
                assert not is_secret_key(field.key), f"{spec.name}.{field.key} would be hidden"
