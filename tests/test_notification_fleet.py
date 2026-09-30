# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The functions the fleet code calls to tell the operator about a node.

Composition is tested in tests/test_notification_composers.py; here is the
wiring: the notification is composed on the notification worker from the
configuration as it stands, delivered through the real notifier under the
operator's ``node_*`` switches, and never on the caller's thread.
"""

# ruff: noqa: F811

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

import noust.core.notifier as notifier_module
from noust.core.config import Config
from noust.core.notifications.fleet import (
    notify_node_host_key_changed,
    notify_node_recovered,
    notify_node_unreachable,
)
from noust.core.notifier import NOTIFICATION_QUEUE, Notifier
from tests.test_notifier import CapturingOpener, config, public_dns  # noqa: F401

WEBHOOK_URL = "https://hooks.example.test/fleet"


@pytest.fixture
def delivered(config: Config, monkeypatch: pytest.MonkeyPatch) -> CapturingOpener:
    """
    Route the worker's notifier to a capturing opener over the sandbox config.

    Returns:
        The opener every delivery goes through.
    """
    config.set("notifications.enabled", True)
    config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
    config.set("server.name", "central")
    opener = CapturingOpener()
    monkeypatch.setattr(notifier_module, "fresh_config", lambda: config)
    real = Notifier
    monkeypatch.setattr(notifier_module, "Notifier", lambda cfg: real(cfg, opener=opener))
    return opener


def sent(opener: CapturingOpener) -> list[dict]:
    assert NOTIFICATION_QUEUE.drain(timeout=5)
    return [json.loads(request.data) for request in opener.requests]


def test_an_unreachable_node_is_announced(delivered: CapturingOpener) -> None:
    notify_node_unreachable(
        "web-2",
        reason="ssh: connect to host 10.0.0.2 port 22: Connection timed out",
        address="10.0.0.2",
        since=datetime(2026, 9, 29, 10, 30, tzinfo=timezone.utc),
    )

    (payload,) = sent(delivered)
    assert (payload["event"], payload["code"], payload["state"]) == (
        "node_unreachable",
        "node.unreachable",
        "failed",
    )
    assert payload["server"] == "central"
    assert payload["title"] == "Server unreachable: web-2"
    assert payload["excerpt"]["lines"] == [
        "ssh: connect to host 10.0.0.2 port 22: Connection timed out"
    ]


def test_a_recovered_node_closes_the_alert(delivered: CapturingOpener) -> None:
    notify_node_recovered("web-2", down_for_s=754)

    (payload,) = sent(delivered)
    assert (payload["event"], payload["state"]) == ("node_recovered", "ok")
    assert {fact["key"]: fact["value"] for fact in payload["facts"]}["downtime"] == "12 min 34 s"


def test_a_changed_host_key_is_announced_with_both_fingerprints(
    delivered: CapturingOpener,
) -> None:
    notify_node_host_key_changed(
        "web-2", pinned="SHA256:aaaa", presented="SHA256:bbbb", command="noust node trust web-2"
    )

    (payload,) = sent(delivered)
    facts = {fact["key"]: fact["value"] for fact in payload["facts"]}
    assert payload["event"] == "node_host_key_changed"
    assert (facts["pinned"], facts["presented"]) == ("SHA256:aaaa", "SHA256:bbbb")
    assert facts["verify"] == "noust node trust web-2"


def test_the_operators_switch_holds(delivered: CapturingOpener, config: Config) -> None:
    config.set("notifications.events.node_recovered", False)

    notify_node_unreachable("web-2")
    notify_node_recovered("web-2")

    assert [payload["event"] for payload in sent(delivered)] == ["node_unreachable"]


def test_the_caller_does_not_wait_for_delivery(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    release = threading.Event()
    started = threading.Event()

    def slow(build):  # type: ignore[no-untyped-def]
        started.set()
        release.wait(5)

    monkeypatch.setattr(notifier_module, "_compose_and_notify", slow)

    notify_node_unreachable("web-2")  # returns while the worker is still blocked

    assert started.wait(5)
    release.set()
    assert NOTIFICATION_QUEUE.drain(timeout=5)
