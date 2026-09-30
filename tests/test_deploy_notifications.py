# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for :mod:`noust.core.deploy_notifications`.

The default subscriber of every deployment in every process - CLI, console
jobs and the webhook alike, per :mod:`noust.deployers.deploy_events`. What is
defended:

- **Every deploy event kind maps to its own notification kind**, and an
  event kind this module does not recognise is logged and dropped, not
  raised.
- **The notification is composed from the event and the configuration**: the
  language, the server's name, the console link under ``web.public_url`` and a
  preview's parent and pull request number when the domain is one. What each
  composition says is pinned in tests/test_notification_composers.py; here only
  the wiring from the configuration and the store is.
- **``deploy_started`` ships off by default**; the others ship on. Asserted
  through the real :class:`~noust.core.notifier.Notifier`, the same way
  tests/test_web_notifications_wiring.py asserts the job-based wiring's
  switches, not by re-reading the default in isolation.
- **Delivery never blocks the caller**, even when a channel is slow, and
  goes through the notifier's one FIFO worker, whose exit drain
  (tests/test_background_queue.py) is what lets a CLI process that exits
  moments after a deploy still send its notification.
"""

# The notifier's config fixture is imported rather than replicated, so there
# stays one definition of "a sandboxed configuration".
# ruff: noqa: F811

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import deploy_notifications
from noust.core.config import DEFAULT_CONFIG, Config
from noust.core.notifications.composers import compose_deploy
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import Notification
from noust.core.notifier import NOTIFICATION_QUEUE, Notifier
from noust.core.store import App, NoustStore, PreviewRecord
from noust.deployers.deploy_events import DeployEvent, DeployEventKind
from tests.test_notifier import CapturingOpener, config, public_dns  # noqa: F401

WEBHOOK_URL = "https://hooks.example.test/wasm"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A sandboxed store, reset around the test like test_deploy_events.py's own."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "wasm.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def _drained_queue() -> Iterator[None]:
    """
    Keep one test's in-flight notifications out of the next test's.

    The notification worker is process-wide: a deliberately slow notification
    a test left behind must have finished before the next test counts calls.
    """
    NOTIFICATION_QUEUE.drain(timeout=10.0)
    yield
    NOTIFICATION_QUEUE.drain(timeout=10.0)


def make_event(
    kind: DeployEventKind = DeployEventKind.SUCCEEDED,
    *,
    domain: str = "shop.example.com",
    deployment_id: int | None = 42,
    trigger: str | None = "webhook",
    commit: str | None = "abc1234",
    branch: str | None = "main",
    error: str | None = None,
    error_output: str | None = None,
    operation: str = "deploy",
) -> DeployEvent:
    """Build a DeployEvent, overriding only what a test cares about."""
    return DeployEvent(
        kind=kind,
        domain=domain,
        deployment_id=deployment_id,
        trigger=trigger,
        commit=commit,
        branch=branch,
        error=error,
        error_output=error_output,
        operation=operation,
    )


class FakeNotifier:
    """Stands in for :class:`Notifier`: records what it was asked to send."""

    #: Shared across every instance created in a test, so on_deploy_event's
    #: own Notifier(config) construction still lands in the one place the
    #: test can see it.
    calls: list[Notification] = []
    delay: float = 0.0

    def __init__(self, config: Config) -> None:
        del config

    def notify(self, event: Notification) -> None:
        if self.delay:
            time.sleep(self.delay)
        FakeNotifier.calls.append(event)


@pytest.fixture
def fake_notifier(monkeypatch: pytest.MonkeyPatch) -> type[FakeNotifier]:
    """Install FakeNotifier in place of the real Notifier, cleared for this test."""
    FakeNotifier.calls = []
    FakeNotifier.delay = 0.0
    monkeypatch.setattr(deploy_notifications, "Notifier", FakeNotifier)
    return FakeNotifier


class TestKindMapping:
    """Every DeployEventKind becomes its own notification kind."""

    @pytest.mark.parametrize(
        ("event_kind", "notification_kind"),
        [
            (DeployEventKind.STARTED, "deploy_started"),
            (DeployEventKind.SUCCEEDED, "deploy_success"),
            (DeployEventKind.FAILED, "deploy_failed"),
            (DeployEventKind.ROLLED_BACK, "deploy_rolled_back"),
        ],
    )
    def test_maps_to_the_documented_kind(
        self,
        config: Config,
        fake_notifier: type[FakeNotifier],
        event_kind: DeployEventKind,
        notification_kind: str,
    ) -> None:
        deploy_notifications.on_deploy_event(make_event(event_kind))
        NOTIFICATION_QUEUE.drain(timeout=5.0)

        assert [call.kind for call in fake_notifier.calls] == [notification_kind]

    def test_an_unrecognised_kind_is_logged_and_dropped_not_raised(
        self, fake_notifier: type[FakeNotifier], caplog: pytest.LogCaptureFixture
    ) -> None:
        """DeployEventKind is closed; this defends the day it is not."""

        class _FutureEvent:
            kind = "not-a-real-kind"
            domain = "shop.example.com"

        deploy_notifications.on_deploy_event(_FutureEvent())  # type: ignore[arg-type]
        NOTIFICATION_QUEUE.drain(timeout=1.0)

        assert fake_notifier.calls == []
        assert "not-a-real-kind" in caplog.text


class TestComposedFromTheConfiguration:
    """What the subscriber reads from configuration and the store."""

    @pytest.fixture(autouse=True)
    def _fresh(self, config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
        # The subscriber reads the file afresh on the worker; the test stands in
        # for that disk read with the sandboxed configuration it just changed.
        monkeypatch.setattr(deploy_notifications, "fresh_config", lambda: config)

    def _sent(self, fake_notifier: type[FakeNotifier], event: DeployEvent) -> Notification:
        deploy_notifications.on_deploy_event(event)
        NOTIFICATION_QUEUE.drain(timeout=5.0)
        return fake_notifier.calls[0]

    def test_it_is_the_notification_the_composer_builds(
        self, config: Config, fake_notifier: type[FakeNotifier]
    ) -> None:
        config.set("server.name", "web-1")
        event = make_event()

        sent = self._sent(fake_notifier, event)

        expected = compose_deploy(event, NotificationContext.from_config(config))
        assert (sent.kind, sent.code, sent.title, sent.subject, sent.server) == (
            expected.kind,
            expected.code,
            expected.title,
            expected.subject,
            expected.server,
        )
        assert sent.server == "web-1"

    def test_a_failures_evidence_reaches_the_excerpt_verbatim(
        self, config: Config, fake_notifier: type[FakeNotifier]
    ) -> None:
        evidence = "Probe / -> 502\nnginx: [emerg] duplicate listen\njournalctl: ..."

        sent = self._sent(
            fake_notifier,
            make_event(DeployEventKind.FAILED, error=evidence, error_output=evidence),
        )

        assert sent.excerpt is not None
        assert sent.excerpt.lines == tuple(evidence.splitlines())

    def test_no_console_link_when_no_public_url_is_configured(
        self, config: Config, fake_notifier: type[FakeNotifier]
    ) -> None:
        assert self._sent(fake_notifier, make_event()).links == ()

    def test_a_console_link_is_built_from_the_public_url(
        self, config: Config, fake_notifier: type[FakeNotifier]
    ) -> None:
        config.set("web.public_url", "https://console.example.test")

        sent = self._sent(fake_notifier, make_event(deployment_id=7))

        assert [link.url for link in sent.links] == [
            "https://console.example.test/apps/shop.example.com/deployments/7"
        ]

    def test_a_node_reachable_only_through_the_central_links_under_it(
        self, config: Config, fake_notifier: type[FakeNotifier]
    ) -> None:
        config.set("web.public_url", "https://central.example.test/n/web-2")

        sent = self._sent(fake_notifier, make_event(deployment_id=7))

        assert sent.links[0].url == (
            "https://central.example.test/n/web-2/apps/shop.example.com/deployments/7"
        )

    def test_without_a_deployment_id_it_links_to_the_application(
        self, config: Config, fake_notifier: type[FakeNotifier]
    ) -> None:
        config.set("web.public_url", "https://console.example.test")

        sent = self._sent(fake_notifier, make_event(deployment_id=None))

        assert sent.links[0].url == "https://console.example.test/apps/shop.example.com"

    def test_the_language_is_the_configured_one(
        self, config: Config, fake_notifier: type[FakeNotifier]
    ) -> None:
        config.set("notifications.language", "es")

        sent = self._sent(fake_notifier, make_event())

        assert (sent.locale, sent.title) == ("es", "Desplegado")

    def test_names_the_preview_and_its_pull_request_number(
        self, config: Config, store: NoustStore, fake_notifier: type[FakeNotifier]
    ) -> None:
        store.create_app(
            App(
                domain="pr-42.example.com",
                app_path="/var/www/apps/pr-42-example-com",
                preview_parent="shop.example.com",
            )
        )
        store.save_preview(
            PreviewRecord(
                parent_domain="shop.example.com",
                domain="pr-42.example.com",
                number=42,
                branch="feature",
                provider="github",
                expires_at="2099-01-01T00:00:00Z",
            )
        )

        sent = self._sent(fake_notifier, make_event(domain="pr-42.example.com"))

        assert {fact.key: fact.value for fact in sent.facts}["preview"] == "shop.example.com #42"

    def test_a_preview_with_no_recorded_number_still_names_its_parent(
        self, config: Config, store: NoustStore, fake_notifier: type[FakeNotifier]
    ) -> None:
        store.create_app(
            App(
                domain="pr-9.example.com",
                app_path="/var/www/apps/pr-9-example-com",
                preview_parent="shop.example.com",
            )
        )

        sent = self._sent(fake_notifier, make_event(domain="pr-9.example.com"))

        assert {fact.key: fact.value for fact in sent.facts}["preview"] == "shop.example.com"

    def test_an_ordinary_application_has_no_preview_fact(
        self, config: Config, store: NoustStore, fake_notifier: type[FakeNotifier]
    ) -> None:
        store.create_app(App(domain="shop.example.com", app_path="/var/www/apps/shop-example-com"))

        sent = self._sent(fake_notifier, make_event())

        assert "preview" not in {fact.key for fact in sent.facts}

    def test_an_application_the_store_has_never_heard_of_has_no_preview_fact(
        self, config: Config, store: NoustStore, fake_notifier: type[FakeNotifier]
    ) -> None:
        """The domain is real (it is deploying), just not yet recorded."""
        sent = self._sent(fake_notifier, make_event(domain="new.example.com"))

        assert "preview" not in {fact.key for fact in sent.facts}

    def test_the_operation_names_the_event(
        self, config: Config, fake_notifier: type[FakeNotifier]
    ) -> None:
        sent = self._sent(fake_notifier, make_event(operation="update"))

        assert (sent.kind, sent.code, sent.title) == (
            "deploy_success",
            "update.succeeded",
            "Updated",
        )


class TestDefaultEnablement:
    """deploy_started ships off; the rest ship on - through the real Notifier."""

    def test_deploy_started_defaults_to_off(self) -> None:
        assert DEFAULT_CONFIG["notifications"]["events"]["deploy_started"] is False

    def test_deploy_rolled_back_defaults_to_on(self) -> None:
        assert DEFAULT_CONFIG["notifications"]["events"]["deploy_rolled_back"] is True

    def test_a_started_notification_is_filtered_by_default(self, config: Config) -> None:
        config.set("notifications.enabled", True)
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        opener = CapturingOpener()
        notification = compose_deploy(
            make_event(DeployEventKind.STARTED), NotificationContext.from_config(config)
        )

        Notifier(config, opener=opener).notify(notification)

        assert opener.requests == []

    def test_an_operator_can_turn_deploy_started_on(self, config: Config) -> None:
        config.set("notifications.enabled", True)
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        config.set("notifications.events.deploy_started", True)
        opener = CapturingOpener()
        notification = compose_deploy(
            make_event(DeployEventKind.STARTED), NotificationContext.from_config(config)
        )

        Notifier(config, opener=opener).notify(notification)

        assert len(opener.requests) == 1

    def test_a_rolled_back_notification_is_sent_by_default(self, config: Config) -> None:
        config.set("notifications.enabled", True)
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        opener = CapturingOpener()
        notification = compose_deploy(
            make_event(DeployEventKind.ROLLED_BACK, error_output="Release 1 is active again"),
            NotificationContext.from_config(config),
        )

        Notifier(config, opener=opener).notify(notification)

        assert len(opener.requests) == 1
        assert json.loads(opener.requests[0].data)["event"] == "deploy_rolled_back"


class TestDeliveryNeverBlocksTheDeployment:
    """A slow channel costs the notification, never the caller."""

    def test_on_deploy_event_returns_before_the_channel_answers(
        self, config: Config, fake_notifier: type[FakeNotifier]
    ) -> None:
        fake_notifier.delay = 0.2

        started = time.perf_counter()
        deploy_notifications.on_deploy_event(make_event())
        elapsed = time.perf_counter() - started

        assert elapsed < 0.05
        assert fake_notifier.calls == []  # still in flight

        NOTIFICATION_QUEUE.drain(timeout=5.0)  # let the thread finish before teardown

    def test_drain_waits_for_a_slow_notification_to_actually_send(
        self, config: Config, fake_notifier: type[FakeNotifier]
    ) -> None:
        """What the exit drain relies on: the CLI process exits only once this returns."""
        fake_notifier.delay = 0.15

        deploy_notifications.on_deploy_event(make_event())
        NOTIFICATION_QUEUE.drain(timeout=5.0)

        assert len(fake_notifier.calls) == 1

    def test_drain_gives_up_at_its_cap_rather_than_hang(
        self, config: Config, fake_notifier: type[FakeNotifier]
    ) -> None:
        fake_notifier.delay = 1.0

        deploy_notifications.on_deploy_event(make_event())
        started = time.perf_counter()
        NOTIFICATION_QUEUE.drain(timeout=0.05)
        elapsed = time.perf_counter() - started

        assert elapsed < 0.5
        assert fake_notifier.calls == []  # the cap won, not the channel


class TestOrdering:
    """A deployment's moments reach the channels in the order they happened."""

    def test_a_fast_failure_never_overtakes_a_slow_start(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        delivered: list[str] = []

        class SlowStart:
            def __init__(self, config: Config) -> None:
                del config

            def notify(self, event: Notification) -> None:
                if event.kind == "deploy_started":
                    time.sleep(0.1)
                delivered.append(event.kind)

        monkeypatch.setattr(deploy_notifications, "Notifier", SlowStart)
        deploy_notifications.on_deploy_event(make_event(DeployEventKind.STARTED))
        deploy_notifications.on_deploy_event(make_event(DeployEventKind.FAILED, error="boom"))
        NOTIFICATION_QUEUE.drain(timeout=5.0)

        assert delivered == ["deploy_started", "deploy_failed"]
