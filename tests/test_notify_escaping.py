# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
User-controlled text cannot ping a channel, and events are delivered in order.

A branch is named by whoever can push to the repository, and it reaches the
notification verbatim: a branch called ``<!channel>`` pinged a whole Slack
channel, and ``@everyone`` a whole Discord server. Slack's control sequences
are neutralised by escaping ``&``, ``<`` and ``>`` (what Slack itself asks
for), Discord's by breaking the mention and by telling Discord to parse none.

:func:`~noust.core.notifier.notify_in_background` puts every notification on
one worker per process, first in first out, reading the configuration file
afresh without reloading the instance every other thread shares.
"""

# ruff: noqa: F811

from __future__ import annotations

import json
import threading
import time
from typing import Any

import pytest

from noust.core import notifier as notifier_module
from noust.core.config import Config
from noust.core.notifier import NotificationEvent, Notifier, notify_in_background
from tests.test_notifier import CapturingOpener, config, public_dns  # noqa: F401

SLACK_URL = "https://hooks.slack.com/services/T000/B000/XXXX"
DISCORD_URL = "https://discord.com/api/webhooks/1/abc"


def _sent(config: Config, channel: str, url: str, event: NotificationEvent) -> dict[str, Any]:
    config.set("notifications.enabled", True)
    config.set(f"notifications.channels.{channel}.webhook_url", url)
    opener = CapturingOpener()
    Notifier(config, opener=opener).notify(event)
    assert len(opener.requests) == 1
    return dict(json.loads(opener.requests[0].data))


def _event(title: str, body: str = "") -> NotificationEvent:
    return NotificationEvent(kind="deploy_success", title=title, body=body, domain="a.example.com")


def test_slack_control_sequences_are_escaped(config: Config) -> None:
    payload = _sent(
        config,
        "slack",
        SLACK_URL,
        _event("a.example.com deployed abc (<!channel>)", "Commit: x (<@U123> & <!here>)"),
    )

    text = payload["text"]
    assert "<" not in text and ">" not in text
    assert "&lt;!channel&gt;" in text
    assert "&lt;@U123&gt; &amp; &lt;!here&gt;" in text


def test_discord_mass_mentions_are_neutralised(config: Config) -> None:
    payload = _sent(
        config,
        "discord",
        DISCORD_URL,
        _event("a.example.com deployed abc (@everyone)", "Commit: x (@here)"),
    )

    content = payload["content"]
    assert "@everyone" not in content and "@here" not in content
    assert "everyone" in content and "here" in content
    assert payload["allowed_mentions"] == {"parse": []}


def test_the_generic_webhook_keeps_the_text_verbatim(config: Config) -> None:
    """The operator's own endpoint gets the facts, not a chat's escaping."""
    payload = _sent(
        config,
        "webhook",
        "https://hooks.example.test/wasm",
        _event("deployed (<!channel>) @everyone"),
    )
    assert payload["title"] == "deployed (<!channel>) @everyone"


class TestNotifyInBackground:
    """One FIFO worker, a fresh read of the file, the shared instance untouched."""

    def test_events_arrive_in_the_order_they_were_published(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        delivered: list[str] = []

        class SlowFirst:
            def __init__(self, config: Config) -> None:
                del config

            def notify(self, event: NotificationEvent) -> None:
                if event.title == "first":
                    time.sleep(0.1)
                delivered.append(event.title)

        monkeypatch.setattr(notifier_module, "Notifier", SlowFirst)
        notify_in_background(_event("first"))
        notify_in_background(_event("second"))
        assert notifier_module.NOTIFICATION_QUEUE.drain(timeout=5)

        assert delivered == ["first", "second"]

    def test_the_shared_configuration_is_never_reloaded(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reloads: list[str] = []
        used: list[Config] = []
        monkeypatch.setattr(
            Config, "reload", lambda self: reloads.append(threading.current_thread().name)
        )

        class Capture:
            def __init__(self, config: Config) -> None:
                used.append(config)

            def notify(self, event: NotificationEvent) -> None:
                del event

        monkeypatch.setattr(notifier_module, "Notifier", Capture)
        notify_in_background(_event("x"))
        assert notifier_module.NOTIFICATION_QUEUE.drain(timeout=5)

        assert reloads == []
        assert len(used) == 1 and used[0] is not Config()
