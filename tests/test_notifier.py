# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for :mod:`noust.core.notifier`.

The suite never opens a socket: every test injects an opener with urlopen's
calling convention and asserts on the :class:`urllib.request.Request` objects
the notifier built. What is being defended:

- the exact payload each channel receives, because the receiving side is not
  ours to fix,
- delivery isolation: one dead channel must not cost the other channels the
  event, nor the caller its deploy,
- secrecy: the Telegram bot token is part of the request URL and must never
  reach a log,
- the test-button contract: the failure comes back in the server's own words,
- the agreement between ``DEFAULT_CONFIG["notifications"]`` and the constants
  here, which no import can enforce because config.py cannot import the
  notifier.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import ipaddress
import json
import logging
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

import noust.core.notifier as notifier_module
from noust.core.config import DEFAULT_CONFIG, Config
from noust.core.notifications.model import Notification, State
from noust.core.notifier import (
    CHANNELS,
    EVENT_KINDS,
    NOTIFY_TIMEOUT,
    USER_AGENT,
    NotificationEvent,
    Notifier,
    validate_telegram_bot_token,
    validate_telegram_chat_id,
)

#: The documented shape of a Bot API token: ``<bot id>:<secret>``.
BOT_TOKEN = "110201543:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"

WEBHOOK_URL = "https://hooks.example.test/wasm"
SLACK_URL = "https://hooks.slack.test/services/T0/B0/slack-secret"
DISCORD_URL = "https://discord.test/api/webhooks/1/discord-secret"


@pytest.fixture
def config(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    """
    Point the global config file at the sandbox and reset the singleton.

    Args:
        sandbox: Isolated filesystem root.
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        A fresh configuration carrying only the defaults.
    """
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", sandbox / "etc" / "wasm" / "config.yaml"
    )
    Config.reset_instance()
    try:
        yield Config()
    finally:
        Config.reset_instance()


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Resolve every hostname to a public address instead of a real one.

    The SSRF guard in noust.core.notifier resolves every destination before
    dispatch; the suite never opens a real socket, so every test that
    dispatches a notification needs a deterministic stand-in for DNS. Tests
    of the guard itself, in TestSSRFGuard below, override this per test to
    point a host at a forbidden address instead.
    """
    monkeypatch.setattr(notifier_module, "_resolve_host", lambda host: ("93.184.216.34",))


class CapturingOpener:
    """
    Stands in for ``urllib.request.urlopen``.

    Attributes:
        requests: Every request that would have been sent, in order.
        timeouts: The timeout passed with each call, failed calls included.
        errors: URL prefix mapped to the exception raised instead of sending.
    """

    def __init__(self) -> None:
        self.requests: list[Request] = []
        self.timeouts: list[float | None] = []
        self.errors: dict[str, Exception] = {}

    def __call__(self, request: Request, timeout: float | None = None) -> io.BytesIO:
        """
        Record a request, or fail the way urlopen would.

        Args:
            request: The request the notifier built.
            timeout: The deadline the notifier asked for.

        Returns:
            A closeable stand-in for the HTTP response.
        """
        self.timeouts.append(timeout)
        for prefix, error in self.errors.items():
            if request.full_url.startswith(prefix):
                raise error
        self.requests.append(request)
        return io.BytesIO(b"ok")


def make_event(**overrides: object) -> NotificationEvent:
    """
    Build a valid event, overriding only what a test cares about.

    Args:
        overrides: Field values that replace the defaults.

    Returns:
        The event.
    """
    fields: dict = {
        "kind": "deploy_success",
        "title": "Deployed example.com",
        "body": "wasm-example.com is running",
        "domain": "example.com",
    }
    fields.update(overrides)
    return NotificationEvent(**fields)


class TestWebhookChannel:
    """The generic webhook carries the documented JSON payload."""

    def test_sends_the_documented_payload(self, config: Config) -> None:
        """URL, method, headers, timeout and payload are all part of the API."""
        config.set("notifications.enabled", True)
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        opener = CapturingOpener()

        Notifier(config, opener=opener).notify(make_event())

        assert len(opener.requests) == 1
        request = opener.requests[0]
        assert request.full_url == WEBHOOK_URL
        assert request.get_method() == "POST"
        assert request.get_header("Content-type") == "application/json"
        assert request.get_header("User-agent") == USER_AGENT
        assert USER_AGENT.startswith("noust-notifier/")
        assert opener.timeouts == [NOTIFY_TIMEOUT]

        assert request.get_header("X-noust-event") == "deploy_success"
        payload = json.loads(request.data)
        assert request.get_header("X-noust-delivery") == payload["id"]
        assert request.get_header("X-noust-signature") is None
        # Version 1 keeps the keys the webhook always had, with their meaning.
        assert payload["version"] == 1
        assert payload["event"] == "deploy_success"
        assert payload["title"] == "Deployed example.com"
        assert payload["domain"] == "example.com"
        assert "wasm-example.com is running" in payload["body"]
        assert payload["ts"].endswith("Z")  # ISO 8601, present even when nobody set it
        assert payload["server"]

    def test_a_secret_signs_the_exact_body(self, config: Config) -> None:
        """The signature is the receiver's proof; it must cover what was sent."""
        config.set("notifications.enabled", True)
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        config.set("notifications.channels.webhook.secret", "s3cret-signing-value")
        opener = CapturingOpener()

        Notifier(config, opener=opener).notify(make_event())

        request = opener.requests[0]
        expected = hmac.new(b"s3cret-signing-value", request.data, hashlib.sha256).hexdigest()
        assert request.get_header("X-noust-signature") == f"sha256={expected}"

    def test_the_secret_never_reaches_a_log(
        self, config: Config, caplog: pytest.LogCaptureFixture
    ) -> None:
        config.set("notifications.enabled", True)
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        config.set("notifications.channels.webhook.secret", "s3cret-signing-value")
        opener = CapturingOpener()
        opener.errors[WEBHOOK_URL] = URLError("failed near s3cret-signing-value")

        with caplog.at_level(logging.WARNING, logger="noust.core.notifier"):
            Notifier(config, opener=opener).notify(make_event())

        assert "s3cret-signing-value" not in caplog.text

    def test_the_master_switch_gates_everything(self, config: Config) -> None:
        """A configured channel must stay silent while notifications are off."""
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        opener = CapturingOpener()

        Notifier(config, opener=opener).notify(make_event())

        assert opener.requests == []

    def test_an_unconfigured_channel_sends_nothing(self, config: Config) -> None:
        """Empty URLs are the off position; no channel may guess a default."""
        config.set("notifications.enabled", True)
        opener = CapturingOpener()

        Notifier(config, opener=opener).notify(make_event())

        assert opener.requests == []

    def test_a_non_http_url_is_refused_and_logged(
        self, config: Config, caplog: pytest.LogCaptureFixture
    ) -> None:
        """urlopen would happily fetch file:// and ftp://; the notifier must not."""
        config.set("notifications.enabled", True)
        config.set("notifications.channels.webhook.webhook_url", "ftp://files.example.test/hook")
        opener = CapturingOpener()

        with caplog.at_level(logging.WARNING, logger="noust.core.notifier"):
            Notifier(config, opener=opener).notify(make_event())

        assert opener.requests == []
        assert "http" in caplog.text


class TestEventFilter:
    """``notifications.events.*`` switches one kind off, not the feature."""

    def test_a_disabled_kind_is_filtered(self, config: Config) -> None:
        """The kind the operator switched off must not be delivered."""
        config.set("notifications.enabled", True)
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        config.set("notifications.events.deploy_success", False)
        opener = CapturingOpener()

        Notifier(config, opener=opener).notify(make_event(kind="deploy_success"))

        assert opener.requests == []

    def test_other_kinds_still_deliver(self, config: Config) -> None:
        """Filtering one kind must leave the others alone."""
        config.set("notifications.enabled", True)
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        config.set("notifications.events.deploy_success", False)
        opener = CapturingOpener()

        Notifier(config, opener=opener).notify(
            make_event(kind="deploy_failed", title="Deploy failed: example.com")
        )

        assert [json.loads(r.data)["event"] for r in opener.requests] == ["deploy_failed"]


class TestDeliveryIsolation:
    """One dead channel must not cost the others the event."""

    def test_a_failing_channel_does_not_block_the_rest(
        self, config: Config, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The failure is logged; Slack and Discord still get the event."""
        config.set("notifications.enabled", True)
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        config.set("notifications.channels.slack.webhook_url", SLACK_URL)
        config.set("notifications.channels.discord.webhook_url", DISCORD_URL)
        opener = CapturingOpener()
        opener.errors["https://hooks.example.test"] = URLError("connection refused")

        with caplog.at_level(logging.WARNING, logger="noust.core.notifier"):
            Notifier(config, opener=opener).notify(make_event())

        assert [r.full_url for r in opener.requests] == [SLACK_URL, DISCORD_URL]
        assert "webhook" in caplog.text
        assert "connection refused" in caplog.text

    def test_notify_never_raises_for_a_delivery_problem(self, config: Config) -> None:
        """The caller is a deploy; its work matters more than the announcement."""
        config.set("notifications.enabled", True)
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        opener = CapturingOpener()
        opener.errors["https://"] = URLError("total outage")

        Notifier(config, opener=opener).notify(make_event())


class TestChatChannels:
    """Slack gets Block Kit in an attachment, Discord one embed."""

    def test_slack_payload_is_a_coloured_attachment(self, config: Config) -> None:
        """The strip is the state's colour; the fallback is what a phone reads."""
        config.set("notifications.enabled", True)
        config.set("notifications.channels.slack.webhook_url", SLACK_URL)
        opener = CapturingOpener()

        Notifier(config, opener=opener).notify(make_event())

        assert [r.full_url for r in opener.requests] == [SLACK_URL]
        payload = json.loads(opener.requests[0].data)
        assert "text" not in payload
        attachment = payload["attachments"][0]
        assert attachment["fallback"] == "\u25cf Deployed example.com"
        assert "wasm-example.com is running" in json.dumps(attachment["blocks"])

    def test_discord_payload_is_one_embed_that_pings_nobody(self, config: Config) -> None:
        config.set("notifications.enabled", True)
        config.set("notifications.channels.discord.webhook_url", DISCORD_URL)
        opener = CapturingOpener()

        Notifier(config, opener=opener).notify(make_event())

        assert [r.full_url for r in opener.requests] == [DISCORD_URL]
        payload = json.loads(opener.requests[0].data)
        assert payload["allowed_mentions"] == {"parse": []}
        embed = payload["embeds"][0]
        assert embed["title"] == "\u25cf Deployed example.com"
        assert "wasm-example.com is running" in embed["description"]

    def test_slack_stays_inside_its_limits_for_an_oversized_body(self, config: Config) -> None:
        """Slack rejects a message over its own limit; this must never happen."""
        config.set("notifications.enabled", True)
        config.set("notifications.channels.slack.webhook_url", SLACK_URL)
        opener = CapturingOpener()

        Notifier(config, opener=opener).notify(make_event(body="x" * 50000))

        blocks = json.loads(opener.requests[0].data)["attachments"][0]["blocks"]
        assert all(len(b.get("text", {}).get("text", "")) <= 3000 for b in blocks)

    def test_discord_stays_inside_its_limits_for_an_oversized_body(self, config: Config) -> None:
        """Discord's limits are far below a health gate's evidence."""
        config.set("notifications.enabled", True)
        config.set("notifications.channels.discord.webhook_url", DISCORD_URL)
        opener = CapturingOpener()

        Notifier(config, opener=opener).notify(make_event(body="npm ERR!\n" * 500))

        embed = json.loads(opener.requests[0].data)["embeds"][0]
        assert len(embed["description"]) <= 4096

    def test_a_short_body_is_sent_whole(self, config: Config) -> None:
        """The common case: nothing is cut when there is nothing to cut."""
        config.set("notifications.enabled", True)
        config.set("notifications.channels.discord.webhook_url", DISCORD_URL)
        opener = CapturingOpener()

        Notifier(config, opener=opener).notify(make_event())

        description = json.loads(opener.requests[0].data)["embeds"][0]["description"]
        assert "wasm-example.com is running" in description
        assert "omitted" not in description


class TestTelegramChannel:
    """The bot token rides in the URL, which is why it must never be logged."""

    def _configure(self, config: Config) -> None:
        config.set("notifications.enabled", True)
        config.set("notifications.channels.telegram.bot_token", BOT_TOKEN)
        config.set("notifications.channels.telegram.chat_id", "-1002003004005")

    def test_builds_the_bot_api_url(self, config: Config) -> None:
        """sendMessage with chat_id and text, addressed with the token."""
        self._configure(config)
        opener = CapturingOpener()

        Notifier(config, opener=opener).notify(make_event())

        assert len(opener.requests) == 1
        request = opener.requests[0]
        assert request.full_url == f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = json.loads(request.data)
        assert payload["chat_id"] == "-1002003004005"
        assert payload["parse_mode"] == "HTML"
        assert payload["link_preview_options"] == {"is_disabled": True}
        assert payload["text"].startswith("\u25cf <b>Deployed example.com</b>")
        assert "wasm-example.com is running" in payload["text"]

    def test_stays_inside_the_bot_apis_own_limit(self, config: Config) -> None:
        """The Bot API rejects a message over 4096 characters outright."""
        self._configure(config)
        opener = CapturingOpener()

        Notifier(config, opener=opener).notify(make_event(body="journalctl output\n" * 400))

        text = json.loads(opener.requests[0].data)["text"]
        assert len(text) <= 4096

    def test_refused_markup_is_retried_once_as_plain_text(self, config: Config) -> None:
        """An alert is never lost to a formatting quirk."""
        self._configure(config)
        sent: list[dict] = []

        def opener(request: Request, timeout: float | None = None) -> io.BytesIO:
            sent.append(json.loads(request.data))
            if len(sent) == 1:
                raise HTTPError(
                    request.full_url,
                    400,
                    "Bad Request",
                    {},
                    io.BytesIO(
                        b'{"ok": false, "description": "Bad Request: can\'t parse entities"}'
                    ),
                )
            return io.BytesIO(b"ok")

        Notifier(config, opener=opener).notify(make_event())

        assert len(sent) == 2
        assert sent[0]["parse_mode"] == "HTML"
        assert "parse_mode" not in sent[1]
        assert "<b>" not in sent[1]["text"]

    def test_another_400_is_not_retried(self, config: Config) -> None:
        self._configure(config)
        sent: list[dict] = []

        def opener(request: Request, timeout: float | None = None) -> io.BytesIO:
            sent.append(json.loads(request.data))
            raise HTTPError(
                request.full_url,
                400,
                "Bad Request",
                {},
                io.BytesIO(b'{"ok": false, "description": "Bad Request: chat not found"}'),
            )

        Notifier(config, opener=opener).notify(make_event())

        assert len(sent) == 1

    def test_half_a_configuration_sends_nothing(self, config: Config) -> None:
        """A token without a chat_id has nowhere to deliver to."""
        config.set("notifications.enabled", True)
        config.set("notifications.channels.telegram.bot_token", BOT_TOKEN)
        opener = CapturingOpener()

        Notifier(config, opener=opener).notify(make_event())

        assert opener.requests == []

    def test_a_failure_never_logs_the_token(
        self, config: Config, caplog: pytest.LogCaptureFixture
    ) -> None:
        """urllib quotes the full URL in some errors; the log must not."""
        self._configure(config)
        opener = CapturingOpener()
        opener.errors["https://api.telegram.org"] = ValueError(
            f"unknown url type: 'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'"
        )

        with caplog.at_level(logging.WARNING, logger="noust.core.notifier"):
            Notifier(config, opener=opener).notify(make_event())

        assert "telegram" in caplog.text
        assert BOT_TOKEN not in caplog.text
        assert "***" in caplog.text

    def test_a_malformed_token_is_refused_before_it_becomes_a_url(
        self, config: Config, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The token is part of the request path; junk must not reshape the URL."""
        config.set("notifications.enabled", True)
        config.set("notifications.channels.telegram.bot_token", "junk/../../evil")
        config.set("notifications.channels.telegram.chat_id", "42")
        opener = CapturingOpener()

        with caplog.at_level(logging.WARNING, logger="noust.core.notifier"):
            Notifier(config, opener=opener).notify(make_event())

        assert opener.requests == []
        assert "telegram" in caplog.text

    @pytest.mark.parametrize("suffix", ["\n", "\r\n", "\n/../evil"])
    def test_a_token_with_a_trailing_line_break_is_refused(
        self, config: Config, caplog: pytest.LogCaptureFixture, suffix: str
    ) -> None:
        """
        ``$`` also matches just before a final newline, so ``match`` against
        ``^...$`` accepted a token followed by a line break: the whole value
        has to be the Bot API's shape, not just everything before its end.
        """
        config.set("notifications.enabled", True)
        config._config["notifications"]["channels"]["telegram"]["bot_token"] = BOT_TOKEN + suffix
        config.set("notifications.channels.telegram.chat_id", "42")
        opener = CapturingOpener()

        with caplog.at_level(logging.WARNING, logger="noust.core.notifier"):
            Notifier(config, opener=opener).notify(make_event())

        assert opener.requests == []
        assert "telegram" in caplog.text

    def test_a_malformed_chat_id_is_refused_before_it_reaches_telegram(
        self, config: Config, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A chat id that is not the Bot API's shape never becomes a request."""
        config.set("notifications.enabled", True)
        config.set("notifications.channels.telegram.bot_token", BOT_TOKEN)
        # Written by an older version, before the config refused it on save.
        config._config["notifications"]["channels"]["telegram"]["chat_id"] = "not-a-chat-id"
        opener = CapturingOpener()

        with caplog.at_level(logging.WARNING, logger="noust.core.notifier"):
            Notifier(config, opener=opener).notify(make_event())

        assert opener.requests == []
        assert "telegram" in caplog.text


class TestTelegramChatIdValidation:
    """
    The regression: a supergroup id copied without its leading minus fails
    with Telegram's own "chat not found" and nothing points at the missing
    character.
    """

    @pytest.mark.parametrize("value", ["42", "-1002003004005", "0", "@some_channel"])
    def test_accepts_the_documented_shapes(self, value: str) -> None:
        assert validate_telegram_chat_id(value) == value

    def test_refuses_an_arbitrary_string(self) -> None:
        with pytest.raises(ValueError, match="not a Telegram chat id"):
            validate_telegram_chat_id("not-a-chat-id")

    def test_a_channel_username_must_start_with_at(self) -> None:
        with pytest.raises(ValueError):
            validate_telegram_chat_id("some_channel")

    def test_suggests_the_missing_minus_sign(self) -> None:
        """A positive, 13+ digit id starting with 100 is a channel id missing its sign."""
        with pytest.raises(ValueError) as raised:
            validate_telegram_chat_id("1002003004005")

        assert "-1002003004005" in str(raised.value)

    def test_a_short_positive_id_is_not_flagged_as_missing_a_sign(self) -> None:
        """A private chat id is a plain positive integer; it must not be second-guessed."""
        assert validate_telegram_chat_id("100200") == "100200"


class TestTelegramBotTokenValidation:
    """The one rule for a bot token's shape, used on save and before every request."""

    def test_accepts_the_bot_api_shape(self) -> None:
        assert validate_telegram_bot_token(BOT_TOKEN) == BOT_TOKEN

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "junk",
            "110201543:",
            ":AAHtoken",
            "110201543:AAH token",
            "110201543:AAH/../evil",
            BOT_TOKEN + "\n",
            BOT_TOKEN + "\r\n",
            "\n" + BOT_TOKEN,
        ],
    )
    def test_refuses_anything_else(self, value: str) -> None:
        with pytest.raises(ValueError, match="does not look like a Telegram bot token"):
            validate_telegram_bot_token(value)

    def test_the_refusal_never_quotes_the_value(self) -> None:
        with pytest.raises(ValueError) as raised:
            validate_telegram_bot_token("110201543:secret part\n")

        assert "secret part" not in str(raised.value)


class TestTestChannel:
    """The settings-page button: try one channel, get the truth back."""

    def test_returns_none_on_success(self, config: Config) -> None:
        """A delivered test message is a None, so the UI can say 'sent'."""
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        opener = CapturingOpener()

        result = Notifier(config, opener=opener).test_channel("webhook")

        assert result is None
        assert [r.full_url for r in opener.requests] == [WEBHOOK_URL]

    def test_message_defaults_to_english(self, config: Config) -> None:
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        opener = CapturingOpener()

        Notifier(config, opener=opener).test_channel("webhook")

        payload = json.loads(opener.requests[0].data)
        assert payload["event"] == "test"
        assert payload["title"] == "Test notification: Webhook"
        assert (
            payload["summary"]
            == "If you can read this, the Webhook channel is configured correctly."
        )

    def test_message_is_rendered_in_the_configured_language(self, config: Config) -> None:
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        config.set("notifications.language", "es")
        opener = CapturingOpener()

        Notifier(config, opener=opener).test_channel("webhook")

        payload = json.loads(opener.requests[0].data)
        assert payload["title"] == "Notificación de prueba: Webhook"
        assert payload["summary"] == "Si ves esto, el canal Webhook está bien configurado."

    def test_message_names_the_server(self, config: Config) -> None:
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        config.set("server.name", "edge-3")
        opener = CapturingOpener()

        Notifier(config, opener=opener).test_channel("webhook")

        assert json.loads(opener.requests[0].data)["server"] == "edge-3"

    def test_works_while_notifications_are_disabled(self, config: Config) -> None:
        """The button exists to try a channel before switching the feature on."""
        assert config.get("notifications.enabled") is False
        config.set("notifications.channels.slack.webhook_url", SLACK_URL)
        opener = CapturingOpener()

        assert Notifier(config, opener=opener).test_channel("slack") is None
        assert len(opener.requests) == 1

    def test_returns_the_error_verbatim(self, config: Config) -> None:
        """A system error is never paraphrased; the UI shows these words."""
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        opener = CapturingOpener()
        opener.errors["https://hooks.example.test"] = URLError("connection refused by the endpoint")

        result = Notifier(config, opener=opener).test_channel("webhook")

        assert result == "connection refused by the endpoint"

    def test_reports_an_unconfigured_channel(self, config: Config) -> None:
        """The message names the setting the operator has to fill in."""
        result = Notifier(config, opener=CapturingOpener()).test_channel("slack")

        assert result is not None
        assert "notifications.channels.slack.webhook_url" in result

    def test_rejects_an_unknown_channel_name(self, config: Config) -> None:
        """A typo in the caller must come back as words, not a crash."""
        result = Notifier(config, opener=CapturingOpener()).test_channel("pigeon")

        assert result is not None
        assert "pigeon" in result

    def test_a_telegram_400_includes_its_own_description(self, config: Config) -> None:
        """
        The regression: a test send that answered 400 only ever showed "HTTP
        400 Bad Request". ``api.telegram.org`` is fixed and known, unlike an
        operator's own webhook URL, so its own explanation is safe to show.
        """
        config.set("notifications.channels.telegram.bot_token", BOT_TOKEN)
        config.set("notifications.channels.telegram.chat_id", "-1002003004005")
        opener = CapturingOpener()
        body = json.dumps(
            {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}
        ).encode()
        opener.errors["https://api.telegram.org"] = HTTPError(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            400,
            "Bad Request",
            {},
            io.BytesIO(body),
        )

        result = Notifier(config, opener=opener).test_channel("telegram")

        assert result == "HTTP 400 Bad Request: Bad Request: chat not found"
        assert BOT_TOKEN not in result

    def test_a_telegram_error_without_json_falls_back_to_the_bare_status(
        self, config: Config
    ) -> None:
        """A response that is not the Bot API's JSON shape must not crash the lookup."""
        config.set("notifications.channels.telegram.bot_token", BOT_TOKEN)
        config.set("notifications.channels.telegram.chat_id", "-1002003004005")
        opener = CapturingOpener()
        opener.errors["https://api.telegram.org"] = HTTPError(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            502,
            "Bad Gateway",
            {},
            io.BytesIO(b"<html>not json</html>"),
        )

        result = Notifier(config, opener=opener).test_channel("telegram")

        assert result == "HTTP 502 Bad Gateway"

    def test_a_non_telegram_400_never_echoes_the_body(self, config: Config) -> None:
        """The SSRF rule still holds: an operator's own webhook body is never reflected."""
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        opener = CapturingOpener()
        opener.errors["https://hooks.example.test"] = HTTPError(
            WEBHOOK_URL,
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"description": "secret internal detail"}'),
        )

        result = Notifier(config, opener=opener).test_channel("webhook")

        assert result == "HTTP 400 Bad Request"
        assert result is not None
        assert "secret internal detail" not in result


class FakeEmailNotifier:
    """
    Records what would have been emailed, instead of opening SMTP.

    Attributes:
        sent: Every message handed to the transport.
        is_configured: What the notifier answers when asked.
    """

    def __init__(self, configured: bool = True) -> None:
        self.sent: list = []
        self.is_configured = configured

    def send_notification(self, notification: Notification) -> bool:
        self.sent.append(notification)
        return True


class TestEmailChannel:
    """Email reuses the monitor's SMTP implementation, never a second one."""

    def test_delegates_to_the_email_notifier(self, config: Config) -> None:
        """The event becomes one message through the injected transport."""
        config.set("notifications.enabled", True)
        config.set("notifications.channels.email.enabled", True)
        opener = CapturingOpener()
        email = FakeEmailNotifier()

        Notifier(config, opener=opener, email_notifier=email).notify(make_event())

        assert len(email.sent) == 1
        assert email.sent[0].title == "Deployed example.com"
        assert email.sent[0].excerpt is not None
        assert "wasm-example.com is running" in email.sent[0].excerpt.lines
        assert opener.requests == []

    def test_a_disabled_email_channel_is_skipped(self, config: Config) -> None:
        """channels.email.enabled is the switch, not the SMTP settings."""
        config.set("notifications.enabled", True)
        email = FakeEmailNotifier()

        Notifier(config, opener=CapturingOpener(), email_notifier=email).notify(make_event())

        assert email.sent == []

    def test_unconfigured_smtp_is_skipped_not_raised(self, config: Config) -> None:
        """A host without SMTP settings must not turn every deploy into noise."""
        config.set("notifications.enabled", True)
        config.set("notifications.channels.email.enabled", True)
        email = FakeEmailNotifier(configured=False)

        Notifier(config, opener=CapturingOpener(), email_notifier=email).notify(make_event())

        assert email.sent == []


class TestAgreementWithDefaults:
    """
    config.py spells out the same names the notifier owns.

    It cannot import them - the notifier reads its settings from config.py -
    so the agreement is pinned here, the same way the web security defaults
    are pinned in tests/test_cli_web.py.
    """

    def test_default_event_switches_match_event_kinds(self) -> None:
        """Every kind is switchable, and no orphan switch survives a rename."""
        assert set(DEFAULT_CONFIG["notifications"]["events"]) == set(EVENT_KINDS)

    def test_default_channel_blocks_match_channel_names(self) -> None:
        """Every channel is configurable, and no orphan block survives."""
        assert set(DEFAULT_CONFIG["notifications"]["channels"]) == set(CHANNELS)

    def test_notifications_ship_disabled(self) -> None:
        """Nothing may phone anywhere until the operator says so."""
        assert DEFAULT_CONFIG["notifications"]["enabled"] is False

    def test_an_unknown_kind_is_rejected_at_the_publisher(self) -> None:
        """A typo'd kind would silently bypass the operator's filters."""
        with pytest.raises(ValueError, match="deploy_sucess"):
            NotificationEvent(kind="deploy_sucess", title="t", body="b")


class TestSSRFGuard:
    """
    IPv6 forms that reach a forbidden IPv4 through tunnelling or mapping.

    ``_FORBIDDEN_NETWORKS`` lists IPv4 and native IPv6 ranges directly, but
    IPv4-mapped (``::ffff:0:0/96``), NAT64 (``64:ff9b::/96``, RFC 6052) and
    6to4 (``2002::/16``) addresses carry an IPv4 address inside an IPv6
    wrapper that none of those entries would catch on its own. The
    unspecified address (``::``) reaches the local host on Linux the same
    way ``0.0.0.0`` does. This class exercises both the address-level guard
    (``_is_forbidden``) and the URL-level one (``_require_public_destination``),
    the second overriding ``public_dns`` so a literal address in the URL is
    resolved by the real, unpatched ``_resolve_host``.
    """

    @pytest.fixture(autouse=True)
    def public_dns(self) -> None:
        """
        Shadow the module-level fake-DNS fixture for this class.

        These tests assert on real address literals and on ``_resolve_host``
        results they set up themselves; the module's autouse fixture would
        otherwise force every host, literal or not, to a fixed public
        address and make every case in this class pass for the wrong reason.
        """
        return None

    @pytest.mark.parametrize(
        "address",
        [
            "::ffff:127.0.0.1",  # IPv4-mapped IPv6, loopback
            "::ffff:169.254.169.254",  # IPv4-mapped IPv6, cloud metadata
            "::ffff:10.0.0.1",  # IPv4-mapped IPv6, RFC 1918
            "64:ff9b::7f00:1",  # NAT64, loopback
            "64:ff9b::a9fe:a9fe",  # NAT64, cloud metadata
            "2002:7f00:0001::1",  # 6to4, loopback
            "2002:a9fe:a9fe::1",  # 6to4, cloud metadata
            "::",  # unspecified address
        ],
    )
    def test_ipv6_forms_embedding_a_forbidden_ipv4_are_forbidden(self, address: str) -> None:
        """Each tunnelling/mapping form that reaches a forbidden IPv4 is caught."""
        assert notifier_module._is_forbidden(ipaddress.ip_address(address)) is True

    @pytest.mark.parametrize(
        "address",
        [
            "::ffff:8.8.8.8",
            "64:ff9b::808:808",
            "2002:0808:0808::1",
        ],
    )
    def test_ipv6_forms_embedding_a_public_ipv4_are_allowed(self, address: str) -> None:
        """A public address must still pass, even wrapped in one of these forms."""
        assert notifier_module._is_forbidden(ipaddress.ip_address(address)) is False

    @pytest.mark.parametrize(
        "address",
        [
            "::ffff:127.0.0.1",
            "64:ff9b::7f00:1",
            "2002:7f00:0001::1",
            "::",
        ],
    )
    def test_url_literal_embedding_a_forbidden_ipv4_is_refused(
        self, config: Config, address: str
    ) -> None:
        """A literal in the URL bypasses DNS entirely; the guard must still catch it."""
        with pytest.raises(ValueError, match="resolves to"):
            notifier_module._require_public_destination(f"http://[{address}]/hook", config)

    def test_resolved_name_embedding_a_forbidden_ipv4_is_refused(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A name resolving to one of these forms is refused exactly like a literal."""
        monkeypatch.setattr(
            notifier_module, "_resolve_host", lambda host: ("::ffff:169.254.169.254",)
        )
        with pytest.raises(ValueError, match="resolves to"):
            notifier_module._require_public_destination("http://metadata.internal/hook", config)

    def test_resolved_name_embedding_a_public_ipv4_is_allowed(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A resolved embedded address that is genuinely public must still pass."""
        monkeypatch.setattr(notifier_module, "_resolve_host", lambda host: ("::ffff:8.8.8.8",))
        notifier_module._require_public_destination("http://dns.example.test/hook", config)


class _JsonOpener:
    """A minimal opener answering every request with the same fixed body."""

    def __init__(self, body: bytes) -> None:
        """
        Args:
            body: What every call returns as the response body.
        """
        self.body = body
        self.requests: list[Request] = []

    def __call__(self, request: Request, timeout: float | None = None) -> io.BytesIO:
        """
        Args:
            request: The request the notifier built.
            timeout: Ignored; recorded by the caller's signature only.

        Returns:
            The scripted body.
        """
        self.requests.append(request)
        return io.BytesIO(self.body)


class TestTelegramChats:
    """
    Finding a chat id today means opening the Bot API's getUpdates by hand.
    :meth:`Notifier.list_telegram_chats` is that lookup, reusing the bot token
    already saved and the same HTTP path - and SSRF guard - as sending one.
    """

    def _configure(self, config: Config) -> None:
        config.set("notifications.channels.telegram.bot_token", BOT_TOKEN)

    def test_lists_every_distinct_chat_id_first_seen(self, config: Config) -> None:
        self._configure(config)
        payload = {
            "ok": True,
            "result": [
                {
                    "update_id": 1,
                    "message": {
                        "chat": {
                            "id": 123,
                            "type": "private",
                            "username": "ops",
                            "first_name": "Ops",
                        }
                    },
                },
                {
                    "update_id": 2,
                    "channel_post": {
                        "chat": {"id": -1001234567890, "type": "channel", "title": "Ops Room"}
                    },
                },
                # Same chat again, from a later update: reported once.
                {
                    "update_id": 3,
                    "message": {"chat": {"id": 123, "type": "private", "username": "ops"}},
                },
            ],
        }
        opener = _JsonOpener(json.dumps(payload).encode())

        chats = Notifier(config, opener=opener).list_telegram_chats()

        assert [chat.id for chat in chats] == [123, -1001234567890]
        assert chats[0].type == "private"
        assert chats[0].username == "ops"
        assert chats[1].title == "Ops Room"
        assert opener.requests[0].full_url == f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"

    def test_refuses_when_no_token_is_configured(self, config: Config) -> None:
        with pytest.raises(ValueError, match="bot_token"):
            Notifier(config, opener=_JsonOpener(b"{}")).list_telegram_chats()

    def test_refuses_a_malformed_token_before_any_request(self, config: Config) -> None:
        config.set("notifications.channels.telegram.bot_token", "junk/../../evil")
        opener = _JsonOpener(b"{}")

        with pytest.raises(ValueError, match="does not look like"):
            Notifier(config, opener=opener).list_telegram_chats()

        assert opener.requests == []

    def test_refuses_a_token_with_a_trailing_newline(self, config: Config) -> None:
        config._config["notifications"]["channels"]["telegram"]["bot_token"] = BOT_TOKEN + "\n"
        opener = _JsonOpener(b"{}")

        with pytest.raises(ValueError, match="does not look like"):
            Notifier(config, opener=opener).list_telegram_chats()

        assert opener.requests == []

    def test_a_telegram_failure_is_reported_in_its_own_words(self, config: Config) -> None:
        self._configure(config)
        opener = _JsonOpener(json.dumps({"ok": False, "description": "Unauthorized"}).encode())

        with pytest.raises(ValueError, match="Unauthorized"):
            Notifier(config, opener=opener).list_telegram_chats()

    def test_updates_with_no_chat_are_skipped_not_crashed_on(self, config: Config) -> None:
        self._configure(config)
        payload = {
            "ok": True,
            "result": [{"update_id": 1, "my_chat_member": {"new_chat_member": {}}}],
        }
        opener = _JsonOpener(json.dumps(payload).encode())

        assert Notifier(config, opener=opener).list_telegram_chats() == []

    def test_no_updates_yet_is_an_empty_list_not_an_error(self, config: Config) -> None:
        self._configure(config)
        opener = _JsonOpener(json.dumps({"ok": True, "result": []}).encode())

        assert Notifier(config, opener=opener).list_telegram_chats() == []


class _BareConfig:
    """
    A configuration with an events block that names nothing.

    An operator's file written before a kind existed has no key for it; what
    the notifier does then is decided by the model, not by whatever the
    default happens to be in config.py.
    """

    def __init__(self, webhook_url: str) -> None:
        self._values = {
            "enabled": True,
            "events": {},
            "channels": {"webhook": {"webhook_url": webhook_url}},
        }

    def get(self, key: str, default: object = None) -> object:
        return self._values if key == "notifications" else default


class TestKindsAndDefaults:
    """Which kinds ship on, and what a missing switch means."""

    def _notification(self, kind: str) -> Notification:
        return Notification(
            kind=kind,
            code=kind,
            state=State.OK,
            locale="en",
            title="t",
            subject="s",
            summary="x",
            server="web-1",
        )

    @pytest.mark.parametrize(
        ("kind", "sent"),
        [
            ("deploy_success", True),
            ("restore_failed", True),
            ("node_unreachable", True),
            ("deploy_started", False),
            ("backup_success", False),
        ],
    )
    def test_a_kind_the_file_does_not_mention_follows_its_default(
        self, kind: str, sent: bool
    ) -> None:
        opener = CapturingOpener()
        notifier = Notifier(_BareConfig(WEBHOOK_URL), opener=opener)  # type: ignore[arg-type]

        notifier.notify(self._notification(kind))

        assert bool(opener.requests) is sent

    def test_every_kind_the_model_knows_is_a_switch_in_the_defaults(self) -> None:
        assert set(DEFAULT_CONFIG["notifications"]["events"]) == set(EVENT_KINDS)
        assert {"restore_success", "restore_failed", "backup_success"} <= set(EVENT_KINDS)
        assert {"node_unreachable", "node_recovered", "node_host_key_changed"} <= set(EVENT_KINDS)

    def test_the_event_kinds_the_notifier_exports_are_the_models(self) -> None:
        from noust.core.notifications.model import EVENT_KINDS as MODEL_KINDS

        assert EVENT_KINDS is MODEL_KINDS

    def test_the_webhook_secret_ships_empty_and_is_redacted_on_the_way_out(self) -> None:
        from noust.core.config import REDACTED, redact_secrets

        assert DEFAULT_CONFIG["notifications"]["channels"]["webhook"]["secret"] == ""
        shown = redact_secrets({"webhook": {"webhook_url": "x", "secret": "s3cret-value"}})
        assert shown["webhook"]["secret"] == REDACTED

    def test_server_name_ships_empty_so_the_hostname_is_used(self) -> None:
        assert DEFAULT_CONFIG["server"]["name"] == ""


class TestLegacyEvent:
    """A title and a body still work: nothing builds one, but the class is public."""

    def test_it_becomes_a_notification_with_the_body_as_its_excerpt(self, config: Config) -> None:
        config.set("server.name", "web-1")
        opener = CapturingOpener()
        config.set("notifications.enabled", True)
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)

        Notifier(config, opener=opener).notify(
            make_event(kind="deploy_failed", body="line one\nline two")
        )

        payload = json.loads(opener.requests[0].data)
        assert payload["state"] == "failed"
        assert payload["excerpt"]["lines"] == ["line one", "line two"]
        assert payload["server"] == "web-1"
        assert payload["code"] == "legacy.deploy_failed"

    @pytest.mark.parametrize(
        ("kind", "state"),
        [
            ("deploy_started", "progress"),
            ("deploy_success", "ok"),
            ("deploy_rolled_back", "warning"),
            ("backup_failed", "failed"),
            ("test", "info"),
        ],
    )
    def test_the_state_follows_the_kind(self, config: Config, kind: str, state: str) -> None:
        config.set("notifications.enabled", True)
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        config.set("notifications.events.deploy_started", True)
        opener = CapturingOpener()

        Notifier(config, opener=opener).notify(make_event(kind=kind))

        assert json.loads(opener.requests[0].data)["state"] == state
