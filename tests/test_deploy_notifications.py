# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for :mod:`wasm.core.deploy_notifications`.

The default subscriber of every deployment in every process - CLI, console
jobs and the webhook alike, per :mod:`wasm.deployers.deploy_events`. What is
defended:

- **Every deploy event kind maps to its own notification kind**, and an
  event kind this module does not recognise is logged and dropped, not
  raised.
- **The title and body match the documented shapes**: trigger, commit and
  branch, the health gate's evidence verbatim for a failure or a rollback,
  a preview's parent and pull request number when the domain is one, and a
  console link when ``web.public_url`` is configured.
- **``deploy_started`` ships off by default**; the others ship on. Asserted
  through the real :class:`~wasm.core.notifier.Notifier`, the same way
  tests/test_web_notifications_wiring.py asserts the job-based wiring's
  switches, not by re-reading the default in isolation.
- **Delivery never blocks the caller**, even when a channel is slow, and a
  notification already in flight is actually sent before
  :func:`~wasm.core.deploy_notifications._join_pending` returns - the
  behaviour the module registers with :mod:`atexit` for, so a CLI process
  that exits moments after a deploy still sends its notification instead of
  losing it with every other daemon thread.
"""

# The notifier's config fixture is imported rather than replicated, so there
# stays one definition of "a sandboxed configuration".
# ruff: noqa: F811

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.test_notifier import CapturingOpener, config, public_dns  # noqa: F401
from wasm.core import deploy_notifications
from wasm.core.config import DEFAULT_CONFIG, Config
from wasm.core.notifier import NotificationEvent, Notifier
from wasm.core.store import App, PreviewRecord, WASMStore
from wasm.deployers.deploy_events import DeployEvent, DeployEventKind

WEBHOOK_URL = "https://hooks.example.test/wasm"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[WASMStore]:
    """A sandboxed store, reset around the test like test_deploy_events.py's own."""
    WASMStore.reset_instance()
    instance = WASMStore(tmp_path / "state" / "wasm.db")
    yield instance
    WASMStore.reset_instance()


@pytest.fixture(autouse=True)
def _reset_pending_threads() -> Iterator[None]:
    """
    Keep one test's in-flight notification threads out of the next test's.

    ``_pending`` and ``_atexit_registered`` are process-wide: a thread a
    previous test started (a deliberately slow one, for the threading tests
    below) must not be joined - and its budget spent - by a later test that
    never started it.
    """
    deploy_notifications._pending.clear()
    deploy_notifications._atexit_registered = False
    yield
    deploy_notifications._pending.clear()
    deploy_notifications._atexit_registered = False


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
        deploy_notifications._join_pending(timeout=5.0)

        assert [call.kind for call in fake_notifier.calls] == [notification_kind]

    def test_an_unrecognised_kind_is_logged_and_dropped_not_raised(
        self, fake_notifier: type[FakeNotifier], caplog: pytest.LogCaptureFixture
    ) -> None:
        """DeployEventKind is closed; this defends the day it is not."""

        class _FutureEvent:
            kind = "not-a-real-kind"
            domain = "shop.example.com"

        deploy_notifications.on_deploy_event(_FutureEvent())  # type: ignore[arg-type]
        deploy_notifications._join_pending(timeout=1.0)

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
        self, config: Config, store: WASMStore
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
        self, config: Config, store: WASMStore
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
        self, config: Config, store: WASMStore
    ) -> None:
        store.create_app(App(domain="shop.example.com", app_path="/var/www/apps/shop-example-com"))

        body = deploy_notifications._body(make_event(), config)

        assert "Preview of" not in body

    def test_an_application_the_store_has_never_heard_of_has_no_preview_line(
        self, config: Config, store: WASMStore
    ) -> None:
        """The domain is real (it is deploying), just not yet recorded."""
        body = deploy_notifications._body(make_event(domain="new.example.com"), config)
        assert "Preview of" not in body


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

        deploy_notifications._join_pending(timeout=5.0)  # let the thread finish before teardown

    def test_join_pending_waits_for_a_slow_notification_to_actually_send(
        self, config: Config, fake_notifier: type[FakeNotifier]
    ) -> None:
        """What the atexit hook relies on: the CLI process exits only once this returns."""
        fake_notifier.delay = 0.15

        deploy_notifications.on_deploy_event(make_event())
        deploy_notifications._join_pending(timeout=5.0)

        assert len(fake_notifier.calls) == 1

    def test_join_pending_gives_up_at_its_cap_rather_than_hang(
        self, config: Config, fake_notifier: type[FakeNotifier]
    ) -> None:
        fake_notifier.delay = 2.0

        deploy_notifications.on_deploy_event(make_event())
        started = time.perf_counter()
        deploy_notifications._join_pending(timeout=0.05)
        elapsed = time.perf_counter() - started

        assert elapsed < 1.0
        assert fake_notifier.calls == []  # the cap won, not the channel

    def test_a_thread_forgets_itself_once_it_has_sent(
        self, config: Config, fake_notifier: type[FakeNotifier]
    ) -> None:
        """The tracking list must not grow forever in a long-running process."""
        deploy_notifications.on_deploy_event(make_event())
        deploy_notifications._join_pending(timeout=5.0)

        assert deploy_notifications._pending == []


class TestAtexitRegistration:
    """The join is actually wired to process exit, once per process."""

    def test_the_first_notification_registers_the_joiner(
        self, monkeypatch: pytest.MonkeyPatch, config: Config, fake_notifier: type[FakeNotifier]
    ) -> None:
        registered: list[Any] = []
        monkeypatch.setattr("atexit.register", registered.append)

        deploy_notifications.on_deploy_event(make_event())

        assert registered == [deploy_notifications._join_pending]

    def test_a_second_notification_does_not_register_again(
        self, monkeypatch: pytest.MonkeyPatch, config: Config, fake_notifier: type[FakeNotifier]
    ) -> None:
        registered: list[Any] = []
        monkeypatch.setattr("atexit.register", registered.append)

        deploy_notifications.on_deploy_event(make_event())
        deploy_notifications._join_pending(timeout=5.0)
        deploy_notifications.on_deploy_event(make_event(DeployEventKind.STARTED))
        deploy_notifications._join_pending(timeout=5.0)

        assert len(registered) == 1
