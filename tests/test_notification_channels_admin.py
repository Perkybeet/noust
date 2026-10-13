# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one place the console, ``noust notify`` and the API change a channel.

Checklist K-27 (several channels of one type), K-25 (events per channel) and
K-26 (routes), seen from the operator: add, change, show, remove, with nothing
secret ever read back and a ``***`` that keeps the credential it stands for.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.config import Config, ConfigError
from noust.core.exceptions import ValidationError
from noust.core.notification_channels import ChannelAdmin, event_catalog
from noust.core.notifications.model import EVENT_KINDS

SLACK = "https://hooks.slack.test/services/T0/B0/prod-secret"
STAGING = "https://hooks.slack.test/services/T0/B1/staging-secret"


@pytest.fixture
def admin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ChannelAdmin]:
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "noust" / "config.yaml"
    )
    Config.reset_instance()
    try:
        yield ChannelAdmin(Config())
    finally:
        Config.reset_instance()


def names(admin: ChannelAdmin) -> list[str]:
    return [view.name for view in admin.channels()]


def test_a_fresh_server_shows_the_default_channel_of_each_type_unconfigured(
    admin: ChannelAdmin,
) -> None:
    views = admin.channels()

    assert [v.name for v in views] == [
        "webhook",
        "slack",
        "discord",
        "telegram",
        "email",
        "pushover",
        "ntfy",
        "gotify",
        "teams",
    ]
    assert not any(v.configured for v in views)
    assert all(v.default for v in views)


def test_a_credential_is_never_read_back(admin: ChannelAdmin) -> None:
    admin.save("slack", settings={"webhook_url": SLACK})
    admin.save(
        "pushover", settings={"user_key": "u" * 30, "app_token": "a" * 30, "device": "phone"}
    )

    slack = admin.get("slack")
    pushover = admin.get("pushover")

    assert slack is not None and slack.configured
    assert slack.settings == {"webhook_url": "***"}
    assert pushover is not None
    assert pushover.settings == {"user_key": "***", "app_token": "***", "device": "phone"}
    assert SLACK not in str(admin.get("slack").to_dict())  # type: ignore[union-attr]


def test_an_unset_credential_reads_empty_so_the_form_can_tell(admin: ChannelAdmin) -> None:
    assert admin.get("slack").settings == {"webhook_url": ""}  # type: ignore[union-attr]


def test_a_second_channel_of_a_type_is_added_with_its_own_name(admin: ChannelAdmin) -> None:
    admin.save("slack", settings={"webhook_url": SLACK})

    staging = admin.save("slack-staging", type="slack", settings={"webhook_url": STAGING})

    assert (staging.name, staging.type, staging.default) == ("slack-staging", "slack", False)
    assert names(admin)[-1] == "slack-staging"
    assert admin.config.get("notifications.channels.slack-staging.type") == "slack"


def test_a_channel_may_be_named_before_it_has_a_destination(admin: ChannelAdmin) -> None:
    """The console adds it by name and type, then asks for the destination in the next step."""
    empty = admin.save("oncall", type="pushover")

    assert (empty.name, empty.type, empty.configured) == ("oncall", "pushover", False)
    assert "oncall" in names(admin)

    filled = admin.save(
        "oncall", settings={"user_key": "u" * 30, "app_token": "a" * 30, "device": ""}
    )

    assert filled.configured


def test_changing_one_setting_keeps_the_rest(admin: ChannelAdmin) -> None:
    admin.save("slack-staging", type="slack", settings={"webhook_url": STAGING}, enabled=True)

    changed = admin.save("slack-staging", enabled=False)

    assert changed.enabled is False and changed.configured
    assert admin.config.get("notifications.channels.slack-staging.webhook_url") == STAGING


def test_a_star_keeps_the_stored_credential_and_empty_clears_it(admin: ChannelAdmin) -> None:
    admin.save("slack", settings={"webhook_url": SLACK})

    admin.save("slack", settings={"webhook_url": "***"})
    assert admin.config.get("notifications.channels.slack.webhook_url") == SLACK

    cleared = admin.save("slack", settings={"webhook_url": ""})
    assert not cleared.configured
    assert admin.config.get("notifications.channels.slack.webhook_url") == ""


def test_events_route_and_quiet_hours_are_the_channels_own(admin: ChannelAdmin) -> None:
    saved = admin.save(
        "oncall",
        type="pushover",
        settings={"user_key": "u" * 30, "app_token": "a" * 30},
        events={"deploy_failed": True, "backup_success": False},
        route={"projects": ["shop"], "min_severity": "failed"},
        quiet={"start": "22:00", "end": "07:00", "timezone": "Europe/Madrid"},
    )

    assert saved.events == {"deploy_failed": True, "backup_success": False}
    assert saved.route == {"projects": ["shop"], "min_severity": "failed"}
    assert saved.quiet is not None and saved.quiet["timezone"] == "Europe/Madrid"

    cleared = admin.save("oncall", events={}, route={}, quiet={})
    assert (cleared.events, cleared.route, cleared.quiet) == ({}, {}, None)
    assert "quiet" not in admin.config.get("notifications.channels.oncall")


def test_what_the_configuration_refuses_is_refused_with_the_place(admin: ChannelAdmin) -> None:
    with pytest.raises(ConfigError, match=r"notifications\.channels\.oncall\.route\.apps"):
        admin.save("oncall", type="slack", route={"apps": ["bad name"]})
    with pytest.raises(ConfigError, match="http:// or https://"):
        admin.save("oncall", type="slack", settings={"webhook_url": "ftp://x/y"})
    assert "oncall" not in names(admin)


@pytest.mark.parametrize(
    ("name", "kwargs", "field"),
    [
        ("Bad Name", {"type": "slack"}, "name"),
        ("mine", {}, "type"),
        ("mine", {"type": "fax"}, "type"),
        ("slack", {"type": "webhook"}, "type"),
        ("mine", {"type": "slack", "settings": {"colour": "red"}}, "colour"),
        ("mine", {"type": "slack", "settings": {"events": {}}}, "events"),
    ],
)
def test_what_cannot_be_a_channel_is_refused_naming_the_field(
    admin: ChannelAdmin, name: str, kwargs: dict, field: str
) -> None:
    with pytest.raises(ValidationError) as caught:
        admin.save(name, **kwargs)

    assert caught.value.field == field


def test_a_channel_of_the_operators_is_removed(admin: ChannelAdmin) -> None:
    admin.save("slack-staging", type="slack", settings={"webhook_url": STAGING})

    assert admin.remove("slack-staging") is True

    assert "slack-staging" not in names(admin)
    assert admin.remove("slack-staging") is False


def test_a_default_channel_is_emptied_not_removed(admin: ChannelAdmin) -> None:
    admin.save("slack", settings={"webhook_url": SLACK}, events={"deploy_failed": False})

    assert admin.remove("slack") is True

    emptied = admin.get("slack")
    assert emptied is not None and not emptied.configured and emptied.events == {}
    assert admin.remove("slack") is False  # nothing left to empty


def test_removing_one_leaves_the_credentials_of_the_others(admin: ChannelAdmin) -> None:
    admin.save("slack", settings={"webhook_url": SLACK})
    admin.save("slack-staging", type="slack", settings={"webhook_url": STAGING})

    admin.remove("slack-staging")

    assert admin.config.get("notifications.channels.slack.webhook_url") == SLACK


def test_the_event_catalog_names_every_event_and_what_is_on(admin: ChannelAdmin) -> None:
    admin.config.set("notifications.events.deploy_failed", False)

    catalog = {e["kind"]: e for e in event_catalog(admin.config)}

    assert list(catalog) == list(EVENT_KINDS)
    assert catalog["deploy_failed"] == {
        "kind": "deploy_failed",
        "default_on": True,
        "enabled": False,
    }
    assert catalog["cron_success"]["default_on"] is False
