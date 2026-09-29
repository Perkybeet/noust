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
- **The title and body match the documented shapes**: trigger, commit and
  branch, the health gate's evidence verbatim for a failure or a rollback,
  a preview's parent and pull request number when the domain is one, and a
  console link when ``web.public_url`` is configured.
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
from noust.core.notifier import NOTIFICATION_QUEUE, NotificationEvent, Notifier
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
    )


class FakeNotifier:
    """Stands in for :class:`Notifier`: records what it was asked to send."""

    #: Shared across every instance created in a test, so on_deploy_event's
    #: own Notifier(config) construction still lands in the one place the
    #: test can see it.
    calls: list[NotificationEvent] = []
    delay: float = 0.0

    def __init__(self, config: Config) -> None:
        del config

    def notify(self, event: NotificationEvent) -> None:
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


class TestTitle:
    """The one-line headline, exactly as specified."""

    def test_started(self) -> None:
        title = deploy_notifications._title(make_event(DeployEventKind.STARTED))
        assert title == "Deploying shop.example.com"

    def test_succeeded_carries_the_commit_and_branch(self) -> None:
        title = deploy_notifications._title(make_event(DeployEventKind.SUCCEEDED))
        assert title == "shop.example.com deployed abc1234 (main)"

    def test_succeeded_with_no_commit_known(self) -> None:
        title = deploy_notifications._title(
            make_event(DeployEventKind.SUCCEEDED, commit=None, branch=None)
        )
        assert title == "shop.example.com deployed"

    def test_failed(self) -> None:
        title = deploy_notifications._title(make_event(DeployEventKind.FAILED))
        assert title == "shop.example.com failed to deploy"

    def test_rolled_back(self) -> None:
        title = deploy_notifications._title(make_event(DeployEventKind.ROLLED_BACK))
        assert title == "shop.example.com rolled back"


class TestTitleInSpanish:
    """notifications.language: es renders WASM's own words, commit and branch left alone."""

    def test_started(self) -> None:
        title = deploy_notifications._title(make_event(DeployEventKind.STARTED), "es")
        assert title == "Desplegando shop.example.com"

    def test_succeeded_carries_the_commit_and_branch_untranslated(self) -> None:
        title = deploy_notifications._title(make_event(DeployEventKind.SUCCEEDED), "es")
        assert title == "shop.example.com desplegado abc1234 (main)"

    def test_failed(self) -> None:
        title = deploy_notifications._title(make_event(DeployEventKind.FAILED), "es")
        assert title == "No se ha podido desplegar shop.example.com"

    def test_rolled_back(self) -> None:
        title = deploy_notifications._title(make_event(DeployEventKind.ROLLED_BACK), "es")
        assert title == "Se ha vuelto a la versión anterior de shop.example.com"


class TestBody:
    """Trigger, commit, evidence and a console link, apart in their own paragraph."""

    def test_carries_the_trigger_and_commit(self, config: Config) -> None:
        body = deploy_notifications._body(make_event(), config)
        assert "Trigger: webhook" in body
        assert "Commit: abc1234 (main)" in body

    def test_a_failures_evidence_is_verbatim_never_paraphrased(self, config: Config) -> None:
        evidence = "Probe / -> 502\nnginx: [emerg] duplicate listen\njournalctl: ..."
        body = deploy_notifications._body(
            make_event(DeployEventKind.FAILED, error=evidence), config
        )
        assert evidence in body

    def test_a_rollbacks_evidence_says_what_is_active_again(self, config: Config) -> None:
        evidence = "Release 2 did not pass; release 1 is active again"
        body = deploy_notifications._body(
            make_event(DeployEventKind.ROLLED_BACK, error=evidence), config
        )
        assert evidence in body

    def test_no_console_link_when_no_public_url_is_configured(self, config: Config) -> None:
        body = deploy_notifications._body(make_event(), config)
        assert "http" not in body

    def test_a_console_link_is_built_from_the_public_url(self, config: Config) -> None:
        config.set("web.public_url", "https://console.example.test")
        body = deploy_notifications._body(make_event(deployment_id=7), config)
        assert "https://console.example.test/apps/shop.example.com/deployments/7" in body

    def test_no_console_link_without_a_deployment_id(self, config: Config) -> None:
        config.set("web.public_url", "https://console.example.test")
        body = deploy_notifications._body(make_event(deployment_id=None), config)
        assert "console.example.test" not in body

    def test_names_the_preview_and_its_pull_request_number(
        self, config: Config, store: NoustStore
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

        body = deploy_notifications._body(make_event(domain="pr-42.example.com"), config)

        assert "Preview of shop.example.com #42." in body

    def test_a_preview_with_no_recorded_number_still_names_its_parent(
        self, config: Config, store: NoustStore
    ) -> None:
        store.create_app(
            App(
                domain="pr-9.example.com",
                app_path="/var/www/apps/pr-9-example-com",
                preview_parent="shop.example.com",
            )
        )

        body = deploy_notifications._body(make_event(domain="pr-9.example.com"), config)

        assert "Preview of shop.example.com." in body

    def test_an_ordinary_application_has_no_preview_line(
        self, config: Config, store: NoustStore
    ) -> None:
        store.create_app(App(domain="shop.example.com", app_path="/var/www/apps/shop-example-com"))

        body = deploy_notifications._body(make_event(), config)

        assert "Preview of" not in body

    def test_an_application_the_store_has_never_heard_of_has_no_preview_line(
        self, config: Config, store: NoustStore
    ) -> None:
        """The domain is real (it is deploying), just not yet recorded."""
        body = deploy_notifications._body(make_event(domain="new.example.com"), config)
        assert "Preview of" not in body


class TestBodyInSpanish:
    """notifications.language: es translates WASM's sentences; evidence stays verbatim."""

    def test_carries_the_trigger_and_commit(self, config: Config) -> None:
        config.set("notifications.language", "es")
        body = deploy_notifications._body(make_event(), config)
        assert "Origen: webhook" in body
        assert "Commit: abc1234 (main)" in body

    def test_a_failures_evidence_is_still_verbatim_never_translated(self, config: Config) -> None:
        config.set("notifications.language", "es")
        evidence = "Probe / -> 502\nnginx: [emerg] duplicate listen\njournalctl: ..."
        body = deploy_notifications._body(
            make_event(DeployEventKind.FAILED, error=evidence), config
        )
        assert evidence in body

    def test_names_the_preview_and_its_pull_request_number(
        self, config: Config, store: NoustStore
    ) -> None:
        config.set("notifications.language", "es")
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

        body = deploy_notifications._body(make_event(domain="pr-42.example.com"), config)

        assert "Vista previa de shop.example.com n.º 42." in body


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
        notification = NotificationEvent(
            kind="deploy_started",
            title=deploy_notifications._title(make_event(DeployEventKind.STARTED)),
            body=deploy_notifications._body(make_event(DeployEventKind.STARTED), config),
            domain="shop.example.com",
        )

        Notifier(config, opener=opener).notify(notification)

        assert opener.requests == []

    def test_an_operator_can_turn_deploy_started_on(self, config: Config) -> None:
        config.set("notifications.enabled", True)
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        config.set("notifications.events.deploy_started", True)
        opener = CapturingOpener()
        notification = NotificationEvent(
            kind="deploy_started", title="Deploying shop.example.com", body="", domain=None
        )

        Notifier(config, opener=opener).notify(notification)

        assert len(opener.requests) == 1

    def test_a_rolled_back_notification_is_sent_by_default(self, config: Config) -> None:
        config.set("notifications.enabled", True)
        config.set("notifications.channels.webhook.webhook_url", WEBHOOK_URL)
        opener = CapturingOpener()
        notification = NotificationEvent(
            kind="deploy_rolled_back",
            title="shop.example.com rolled back",
            body="Release 1 is active again",
            domain="shop.example.com",
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

            def notify(self, event: NotificationEvent) -> None:
                if event.kind == "deploy_started":
                    time.sleep(0.1)
                delivered.append(event.kind)

        monkeypatch.setattr(deploy_notifications, "Notifier", SlowStart)
        deploy_notifications.on_deploy_event(make_event(DeployEventKind.STARTED))
        deploy_notifications.on_deploy_event(make_event(DeployEventKind.FAILED, error="boom"))
        NOTIFICATION_QUEUE.drain(timeout=5.0)

        assert delivered == ["deploy_started", "deploy_failed"]
