# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Discord notifications: the embed, the optional mention and the plain-text fallback
(checklist K-03).

The embed itself (title, description, the state's colour, fields, a footer, a
timestamp and the link to the console) is covered with the other renderers in
``test_notification_renderers.py``; what is held here is what 3.4 adds:

- an operator may name a role and/or a user to ping, and only a **critical**
  event (state ``failed``) pings them, in the message ``content`` where Discord
  honours a mention, never in the embed;
- ``allowed_mentions`` still lets nothing else ping: text that someone else chose
  is defused, and only the two configured ids may notify;
- when Discord refuses the embed (HTTP 400) the alert is sent once more as plain
  text, with the link and the tail of the log inside the 2000 characters a
  message may have, because an alert must not be lost to a formatting quirk.

No test opens a socket: the notifier is given an opener and the DNS answer is a
stand-in.
"""

# ruff: noqa: F811

from __future__ import annotations

import io
import json
import logging
from dataclasses import replace
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from noust.core.config import DEFAULT_CONFIG, Config, redact_secrets
from noust.core.exceptions import ConfigError
from noust.core.notifications.model import CRITICAL_STATES, Excerpt, Fact, State
from noust.core.notifications.render import discord
from noust.core.notifications.render.discord import CONTENT_LIMIT, Mention
from noust.core.notifier import Notifier
from tests.notifications_support import catalog, hostile
from tests.test_notification_renderers import notification
from tests.test_notifier import (  # noqa: F401
    DISCORD_URL,
    CapturingOpener,
    config,
    make_event,
    public_dns,
)

ROLE = "123456789012345678"
USER = "876543210987654321"
LOG = tuple(f"error line {number}: something failed here" for number in range(1, 80))


def critical(**overrides: object) -> Any:
    """A failed deploy, which is critical."""
    return notification(**overrides)


class TestWhatIsCritical:
    def test_only_a_failure_is(self) -> None:
        assert CRITICAL_STATES == frozenset({State.FAILED})

    @pytest.mark.parametrize("state", list(State))
    def test_the_notification_knows(self, state: State) -> None:
        assert notification(state=state).critical is (state in CRITICAL_STATES)

    def test_a_critical_event_is_always_loud(self) -> None:
        """A mention on a message Discord was told not to notify about would be a lie."""
        assert all(notification(state=state).loud for state in CRITICAL_STATES)


class TestTheMentionIsValidated:
    @pytest.mark.parametrize("value", [ROLE, USER, "12345678901234567", "12345678901234567890"])
    def test_a_snowflake(self, value: str) -> None:
        assert Mention(role_id=value).role_id == value
        assert Mention(user_id=value).user_id == value

    @pytest.mark.parametrize(
        "value",
        [
            "1234567890123456",  # one digit short
            "123456789012345678901",  # one too many
            "<@&123456789012345678>",
            "@everyone",
            "123456789012345678 ",
            "12345678901234567a",
            "-123456789012345678",
        ],
    )
    def test_anything_else_is_refused(self, value: str) -> None:
        with pytest.raises(ValueError, match="Discord"):
            Mention(role_id=value)
        with pytest.raises(ValueError, match="Discord"):
            Mention(user_id=value)

    def test_nothing_named_is_no_mention(self) -> None:
        assert not Mention()
        assert Mention(role_id=ROLE)
        assert Mention(user_id=USER)


class TestACriticalEventPingsWhoIsNamed:
    def test_without_a_mention_nothing_changes(self) -> None:
        payload = discord.render(critical())

        assert "content" not in payload
        assert payload["allowed_mentions"] == {"parse": []}

    def test_the_role_and_the_user_are_pinged_in_the_content(self) -> None:
        payload = discord.render(critical(), mention=Mention(ROLE, USER))

        assert payload["content"].startswith(f"<@&{ROLE}> <@{USER}> ")
        assert payload["allowed_mentions"] == {"parse": [], "roles": [ROLE], "users": [USER]}

    def test_the_content_says_what_happened_for_a_phone_that_shows_only_it(self) -> None:
        payload = discord.render(critical(), mention=Mention(role_id=ROLE))

        assert payload["content"] == f"<@&{ROLE}> ✕ Deploy failed · shop.example.com"

    def test_only_the_role(self) -> None:
        payload = discord.render(critical(), mention=Mention(role_id=ROLE))

        assert payload["content"].startswith(f"<@&{ROLE}> ")
        assert payload["allowed_mentions"] == {"parse": [], "roles": [ROLE]}

    def test_only_the_user(self) -> None:
        payload = discord.render(critical(), mention=Mention(user_id=USER))

        assert payload["content"].startswith(f"<@{USER}> ")
        assert payload["allowed_mentions"] == {"parse": [], "users": [USER]}

    @pytest.mark.parametrize("state", [s for s in State if s not in CRITICAL_STATES])
    def test_nothing_else_pings(self, state: State) -> None:
        payload = discord.render(notification(state=state), mention=Mention(ROLE, USER))

        assert "content" not in payload
        assert payload["allowed_mentions"] == {"parse": []}

    def test_an_empty_mention_is_none(self) -> None:
        payload = discord.render(critical(), mention=Mention())

        assert "content" not in payload
        assert payload["allowed_mentions"] == {"parse": []}

    def test_the_embed_is_the_same_with_or_without_a_mention(self) -> None:
        n = critical()

        assert discord.render(n, mention=Mention(ROLE))["embeds"] == discord.render(n)["embeds"]

    def test_the_embed_never_carries_the_ping(self) -> None:
        payload = discord.render(critical(), mention=Mention(ROLE, USER))

        assert f"<@&{ROLE}>" not in json.dumps(payload["embeds"])
        assert f"<@{USER}>" not in json.dumps(payload["embeds"])

    def test_text_someone_else_chose_still_cannot_ping(self) -> None:
        """The content carries the headline; the headline carries a branch name."""
        n = hostile("en")["hostile.deploy_failed"]
        assert n.critical

        payload = discord.render(n, mention=Mention(ROLE))
        content = payload["content"]

        assert "@everyone" not in content and "@here" not in content
        assert content.count("<@") == 1  # the configured one; any other was defused
        assert payload["allowed_mentions"] == {"parse": [], "roles": [ROLE]}
        assert len(content) <= CONTENT_LIMIT

    def test_a_long_subject_does_not_push_the_content_over_its_limit(self) -> None:
        n = critical(subject="x" * 5000)

        assert len(discord.render(n, mention=Mention(ROLE, USER))["content"]) <= CONTENT_LIMIT


class TestTheEmbedForACriticalEvent:
    """What an operator sees: the checklist's embed, asked of a real deploy failure."""

    def test_title_description_colour_fields_footer_time_and_link(self) -> None:
        n = catalog("en")["deploy.failed"]

        embed = discord.render(n)["embeds"][0]

        assert embed["title"].startswith("✕ ")
        assert n.subject in embed["title"]
        assert embed["description"]
        assert embed["color"] == int("cf3a30", 16)
        names = [field["name"] for field in embed["fields"]]
        assert names[0] == "Server"
        assert len(names) >= 2
        assert embed["footer"]["text"] == f"Noust · {n.server}"
        assert embed["timestamp"] == n.ts.isoformat()
        assert embed["url"] == n.console_link.url if n.console_link else "url" not in embed

    def test_the_colour_is_the_severity(self) -> None:
        colours = {
            state: discord.render(notification(state=state))["embeds"][0]["color"]
            for state in State
        }

        assert len(set(colours.values())) == len(State) - 1  # warning and progress share amber
        assert colours[State.FAILED] != colours[State.WARNING]
        assert colours[State.OK] != colours[State.FAILED]

    def test_without_a_console_url_the_title_is_not_a_link(self) -> None:
        embed = discord.render(critical(links=()))["embeds"][0]

        assert "url" not in embed


class TestThePlainTextFallback:
    def test_it_is_text_and_a_ping_and_nothing_else(self) -> None:
        payload = discord.render_plain(critical(), mention=Mention(ROLE))

        assert set(payload) == {"content", "allowed_mentions"}
        assert payload["allowed_mentions"] == {"parse": [], "roles": [ROLE]}
        assert payload["content"].startswith(f"<@&{ROLE}> ")

    def test_it_has_the_headline_the_link_and_the_facts(self) -> None:
        n = critical(facts=(Fact("server", "Server", "web-1"), Fact("branch", "Branch", "main")))

        content = discord.render_plain(n)["content"]

        assert "Deploy failed" in content and "shop.example.com" in content
        assert "<https://c.example.com/apps/x>" in content
        assert "**Server:** web-1" in content
        assert "**Branch:** main" in content
        assert "It stopped at an error." in content

    def test_a_quiet_event_makes_no_notification_and_a_failure_does(self) -> None:
        assert discord.render_plain(notification(state=State.OK)).get("flags") == 4096
        assert "flags" not in discord.render_plain(critical())

    def test_nothing_but_a_critical_event_pings(self) -> None:
        payload = discord.render_plain(notification(state=State.WARNING), mention=Mention(ROLE))

        assert payload["allowed_mentions"] == {"parse": []}
        assert not payload["content"].startswith("<@")

    def test_a_long_log_is_cut_from_the_front_and_keeps_its_end_and_the_link(self) -> None:
        n = critical(excerpt=Excerpt("Output", LOG, omitted=0, pinned=0))

        content = discord.render_plain(n, mention=Mention(ROLE, USER))["content"]

        assert len(content) <= CONTENT_LIMIT
        assert "<https://c.example.com/apps/x>" in content
        assert LOG[-1] in content
        assert LOG[0] not in content
        assert "omitted" in content
        assert content.count("```") == 2  # the fence is closed

    def test_a_log_that_fits_is_shown_whole(self) -> None:
        n = critical(excerpt=Excerpt("Output", LOG[:3], omitted=0, pinned=0))

        content = discord.render_plain(n)["content"]

        assert all(line in content for line in LOG[:3])

    def test_hostile_text_cannot_ping_or_close_the_fence(self) -> None:
        payload = discord.render_plain(
            hostile("en")["hostile.deploy_failed"], mention=Mention(ROLE)
        )
        content = payload["content"]

        assert "@everyone" not in content and "@here" not in content
        assert content.count("<@") == 1
        assert content.count("```") % 2 == 0
        assert len(content) <= CONTENT_LIMIT

    @pytest.mark.parametrize("name", sorted(catalog("en")))
    def test_every_event_fits(self, name: str) -> None:
        for locale in ("en", "es"):
            n = catalog(locale)[name]  # type: ignore[arg-type]
            assert len(discord.render_plain(n, mention=Mention(ROLE, USER))["content"]) <= 2000

    def test_an_enormous_value_cannot_push_the_link_out(self) -> None:
        n = critical(
            summary="s" * 5000,
            facts=tuple(Fact(f"k{i}", f"Label {i}", "v" * 3000) for i in range(20)),
            excerpt=Excerpt("Output", tuple("l" * 2000 for _ in range(30)), omitted=0, pinned=0),
        )

        content = discord.render_plain(n)["content"]

        assert len(content) <= CONTENT_LIMIT
        assert "<https://c.example.com/apps/x>" in content


# ---------------------------------------------------------------------------
# Through the notifier: configuration in, HTTP out
# ---------------------------------------------------------------------------


def configured(config: Config, **keys: str) -> Config:
    """
    Turn notifications on with a Discord webhook and the given mention keys.

    Args:
        config: The test configuration.
        **keys: ``mention_role_id`` and/or ``mention_user_id``.

    Returns:
        The same configuration.
    """
    config.set("notifications.enabled", True)
    config.set("notifications.channels.discord.webhook_url", DISCORD_URL)
    for key, value in keys.items():
        config.set(f"notifications.channels.discord.{key}", value)
    return config


def sent(opener: CapturingOpener) -> list[dict[str, Any]]:
    """The JSON bodies the opener received."""
    return [json.loads(request.data) for request in opener.requests]


class TestFromTheConfiguration:
    def test_a_failed_deploy_pings_the_configured_role_and_user(self, config: Config) -> None:
        configured(config, mention_role_id=ROLE, mention_user_id=USER)
        opener = CapturingOpener()

        Notifier(config, opener=opener).notify(catalog("en")["deploy.failed"])

        (payload,) = sent(opener)
        assert payload["content"].startswith(f"<@&{ROLE}> <@{USER}> ")
        assert payload["allowed_mentions"] == {"parse": [], "roles": [ROLE], "users": [USER]}
        assert payload["embeds"][0]["title"].startswith("✕")

    def test_a_successful_deploy_pings_nobody(self, config: Config) -> None:
        configured(config, mention_role_id=ROLE)
        opener = CapturingOpener()

        Notifier(config, opener=opener).notify(make_event())

        (payload,) = sent(opener)
        assert "content" not in payload
        assert payload["allowed_mentions"] == {"parse": []}

    def test_a_warning_pings_nobody(self, config: Config) -> None:
        configured(config, mention_role_id=ROLE)
        opener = CapturingOpener()

        Notifier(config, opener=opener).notify(catalog("en")["deploy.rolled_back"])

        assert "content" not in sent(opener)[0]

    def test_with_nothing_configured_a_failure_pings_nobody(self, config: Config) -> None:
        configured(config)
        opener = CapturingOpener()

        Notifier(config, opener=opener).notify(catalog("en")["deploy.failed"])

        assert "content" not in sent(opener)[0]

    def test_the_test_button_never_pings(self, config: Config) -> None:
        configured(config, mention_role_id=ROLE, mention_user_id=USER)
        opener = CapturingOpener()

        assert Notifier(config, opener=opener).test_channel("discord") is None

        assert "content" not in sent(opener)[0]

    def test_a_mention_written_by_hand_that_is_not_an_id_is_ignored_and_the_alert_still_goes(
        self, config: Config, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A hand-edited file may say anything; the alert matters more than the ping."""
        configured(config)
        config._config["notifications"]["channels"]["discord"]["mention_role_id"] = "@everyone"
        opener = CapturingOpener()

        with caplog.at_level(logging.WARNING, logger="noust.core.notifier"):
            Notifier(config, opener=opener).notify(catalog("en")["deploy.failed"])

        (payload,) = sent(opener)
        assert "content" not in payload
        assert payload["allowed_mentions"] == {"parse": []}
        assert "mention_role_id" in caplog.text
        assert "@everyone" not in caplog.text


class Refusing:
    """
    An opener whose first request is refused the way Discord refuses a bad embed.

    Attributes:
        requests: Every request, refused or not, in order.
        status: The status of the refusal.
        refusals: How many times to refuse before accepting.
    """

    def __init__(self, status: int = 400, refusals: int = 1) -> None:
        self.requests: list[Request] = []
        self.status = status
        self.refusals = refusals

    def __call__(self, request: Request, timeout: float | None = None) -> io.BytesIO:
        self.requests.append(request)
        if len(self.requests) <= self.refusals:
            raise HTTPError(
                request.full_url,
                self.status,
                "Bad Request",
                {},  # type: ignore[arg-type]
                io.BytesIO(b'{"embeds": ["Invalid Form Body"]}'),
            )
        return io.BytesIO(b"ok")


class TestWhenDiscordRefusesTheEmbed:
    def test_the_alert_is_sent_again_as_plain_text(self, config: Config) -> None:
        configured(config, mention_role_id=ROLE)
        opener = Refusing()

        Notifier(config, opener=opener).notify(catalog("en")["deploy.failed"])

        first, second = (json.loads(request.data) for request in opener.requests)
        assert "embeds" in first
        assert "embeds" not in second
        assert second["content"].startswith(f"<@&{ROLE}> ")
        assert "<https://" in second["content"]

    def test_the_retry_goes_to_the_same_webhook(self, config: Config) -> None:
        configured(config)
        opener = Refusing()

        Notifier(config, opener=opener).notify(catalog("en")["deploy.failed"])

        assert [request.full_url for request in opener.requests] == [DISCORD_URL, DISCORD_URL]

    def test_a_long_log_in_the_retry_still_fits(self, config: Config) -> None:
        configured(config)
        opener = Refusing()
        long = replace(
            catalog("en")["deploy.failed"], excerpt=Excerpt("Output", LOG, omitted=0, pinned=0)
        )

        Notifier(config, opener=opener).notify(long)

        assert len(json.loads(opener.requests[1].data)["content"]) <= CONTENT_LIMIT

    def test_only_a_400_is_retried(self, config: Config) -> None:
        configured(config)
        opener = Refusing(status=500)

        Notifier(config, opener=opener).notify(catalog("en")["deploy.failed"])

        assert len(opener.requests) == 1

    def test_a_refusal_of_the_retry_is_logged_and_not_retried_again(
        self, config: Config, caplog: pytest.LogCaptureFixture
    ) -> None:
        configured(config)
        opener = Refusing(refusals=5)

        with caplog.at_level(logging.WARNING, logger="noust.core.notifier"):
            Notifier(config, opener=opener).notify(catalog("en")["deploy.failed"])

        assert len(opener.requests) == 2
        assert "discord" in caplog.text
        assert "discord-secret" not in caplog.text


# ---------------------------------------------------------------------------
# The configuration keys
# ---------------------------------------------------------------------------


class TestTheConfiguration:
    def test_the_defaults_name_the_keys_and_leave_them_off(self) -> None:
        discord_defaults = DEFAULT_CONFIG["notifications"]["channels"]["discord"]

        assert discord_defaults["mention_role_id"] == ""
        assert discord_defaults["mention_user_id"] == ""

    @pytest.mark.parametrize("key", ["mention_role_id", "mention_user_id"])
    def test_an_id_is_accepted_and_trimmed(self, config: Config, key: str) -> None:
        config.set(f"notifications.channels.discord.{key}", f"  {ROLE}  ")

        assert config.get(f"notifications.channels.discord.{key}") == ROLE

    @pytest.mark.parametrize("key", ["mention_role_id", "mention_user_id"])
    def test_empty_turns_it_off(self, config: Config, key: str) -> None:
        config.set(f"notifications.channels.discord.{key}", ROLE)
        config.set(f"notifications.channels.discord.{key}", "")

        assert config.get(f"notifications.channels.discord.{key}") == ""

    @pytest.mark.parametrize("key", ["mention_role_id", "mention_user_id"])
    @pytest.mark.parametrize("value", ["everyone", "@here", "<@&123456789012345678>", "12345"])
    def test_anything_that_is_not_an_id_is_refused_with_how_to_find_one(
        self, config: Config, key: str, value: str
    ) -> None:
        with pytest.raises(ConfigError) as caught:
            config.set(f"notifications.channels.discord.{key}", value)

        assert key in str(caught.value)
        assert "Developer Mode" in (caught.value.details or "")

    def test_the_ids_are_not_secrets(self) -> None:
        shown = redact_secrets(DEFAULT_CONFIG["notifications"]["channels"]["discord"])

        assert shown["mention_role_id"] == ""
        assert "mention_user_id" in shown
        config = {"discord": {"mention_role_id": ROLE, "webhook_url": DISCORD_URL}}
        assert redact_secrets(config)["discord"]["mention_role_id"] == ROLE
        assert redact_secrets(config)["discord"]["webhook_url"] == "***"
