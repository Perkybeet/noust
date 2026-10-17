# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The notifier with channel instances, the push services, routing and the ledger.

Checklist K-04 (Telegram topics), K-08 (Pushover), K-09 (ntfy), K-10 (Gotify and
Teams), K-25 (events per channel), K-26 (routing), K-27 (several channels of one
type), K-28 (quiet hours and the digest) and K-30 (retries, a record of every
delivery, and a failure that never reaches the caller).

The suite never opens a socket: the opener is injected and DNS is replaced.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs
from urllib.request import Request

import pytest

import noust.core.notifier as notifier_module
from noust.core.config import Config
from noust.core.notification_ledger import RETRY_DELAYS, DeliveryLedger
from noust.core.notifications.model import Notification, State
from noust.core.notifications.routing import Placement
from noust.core.notifier import Notifier, SweepReport, is_transient
from noust.core.store import NoustStore

BOT_TOKEN = "110201543:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
SLACK = "https://hooks.slack.test/services/T0/B0/prod-secret"
SLACK_STAGING = "https://hooks.slack.test/services/T0/B1/staging-secret"
PUSHOVER_USER = "u" * 30
PUSHOVER_APP = "a" * 30
START = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


class Clock:
    """One moment, read as a datetime by the notifier and as epoch seconds by the ledger."""

    def __init__(self, moment: datetime = START) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def epoch(self) -> float:
        return self.moment.timestamp()

    def advance(self, **delta: float) -> None:
        self.moment += timedelta(**delta)


class Opener:
    """Stands in for ``urlopen``: records what would be sent, fails on request."""

    def __init__(self) -> None:
        self.requests: list[Request] = []
        self.errors: dict[str, Exception] = {}

    def __call__(self, request: Request, timeout: float | None = None) -> io.BytesIO:
        for prefix, error in self.errors.items():
            if request.full_url.startswith(prefix):
                raise error
        self.requests.append(request)
        return io.BytesIO(b"ok")

    def to(self, prefix: str) -> list[Request]:
        return [r for r in self.requests if r.full_url.startswith(prefix)]


def http_error(url: str, code: int, body: bytes = b"") -> HTTPError:
    return HTTPError(url, code, "Reason", {}, io.BytesIO(body))  # type: ignore[arg-type]


def note(
    kind: str = "deploy_failed",
    state: State = State.FAILED,
    domain: str | None = "shop.example.com",
    subject: str | None = None,
) -> Notification:
    return Notification(
        kind=kind,
        code=f"{kind}.x",
        state=state,
        locale="en",
        title="Deploy failed" if state is State.FAILED else "Heads up",
        subject=subject if subject is not None else (domain or ""),
        summary="It stopped at an error.",
        server="web-1",
        domain=domain,
        ts=START,
    )


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "noust" / "config.yaml"
    )
    Config.reset_instance()
    try:
        cfg = Config()
        cfg.set("notifications.enabled", True)
        yield cfg
    finally:
        Config.reset_instance()


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notifier_module, "_resolve_host", lambda host: ("93.184.216.34",))


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def ledger(tmp_path: Path, clock: Clock) -> Iterator[DeliveryLedger]:
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    yield DeliveryLedger(store, clock=clock.epoch)
    NoustStore.reset_instance()


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        notifier_module.audit,
        "record",
        lambda event, **fields: events.append({"event": event, **fields}),
    )
    return events


def notifier(
    config: Config,
    opener: Opener,
    ledger: DeliveryLedger | None,
    clock: Clock,
    placement: Any = None,
) -> Notifier:
    return Notifier(
        config,
        opener=opener,
        ledger=ledger,
        record=ledger is not None,
        clock=clock,
        placement=placement or (lambda _domain: None),
    )


class TestPushServices:
    def test_pushover_sends_a_form_with_the_credentials_in_the_body(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set(
            "notifications.channels.pushover",
            {"user_key": PUSHOVER_USER, "app_token": PUSHOVER_APP, "device": "phone"},
        )
        opener = Opener()

        notifier(config, opener, ledger, clock).notify(note())

        (request,) = opener.requests
        assert request.full_url == "https://api.pushover.net/1/messages.json"
        assert request.get_header("Content-type") == "application/x-www-form-urlencoded"
        form = parse_qs(request.data.decode())  # type: ignore[union-attr]
        assert form["token"] == [PUSHOVER_APP] and form["user"] == [PUSHOVER_USER]
        assert form["device"] == ["phone"]
        assert form["priority"] == ["1"]
        assert "Deploy failed" in form["title"][0]
        assert PUSHOVER_APP not in request.full_url and PUSHOVER_USER not in request.full_url

    def test_pushover_refuses_a_key_that_is_not_one(
        self, config: Config, ledger: DeliveryLedger, clock: Clock, caplog: pytest.LogCaptureFixture
    ) -> None:
        config._config["notifications"]["channels"]["pushover"] = {
            "user_key": "not a key!",
            "app_token": PUSHOVER_APP,
        }
        opener = Opener()

        with caplog.at_level(logging.WARNING):
            notifier(config, opener, ledger, clock).notify(note())

        assert opener.requests == []
        assert "pushover" in caplog.text

    def test_ntfy_publishes_json_to_the_servers_root_with_the_topic_in_the_body(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set(
            "notifications.channels.ntfy",
            {"webhook_url": "https://ntfy.example.test/noust-alerts", "token": "tk_secret"},
        )
        opener = Opener()

        notifier(config, opener, ledger, clock).notify(note())

        (request,) = opener.requests
        assert request.full_url == "https://ntfy.example.test/"
        assert request.get_header("Authorization") == "Bearer tk_secret"
        body = json.loads(request.data)  # type: ignore[arg-type]
        assert body["topic"] == "noust-alerts"
        assert body["priority"] == 4
        assert "Deploy failed" in body["title"]

    def test_ntfy_under_a_sub_path_keeps_the_path(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set(
            "notifications.channels.ntfy", {"webhook_url": "https://example.test/ntfy/alerts"}
        )
        opener = Opener()

        notifier(config, opener, ledger, clock).notify(note())

        assert opener.requests[0].full_url == "https://example.test/ntfy/"
        assert opener.requests[0].get_header("Authorization") is None

    def test_ntfy_without_a_topic_is_refused(
        self, config: Config, ledger: DeliveryLedger, clock: Clock, caplog: pytest.LogCaptureFixture
    ) -> None:
        config.set("notifications.channels.ntfy", {"webhook_url": "https://ntfy.example.test/"})
        opener = Opener()

        with caplog.at_level(logging.WARNING):
            notifier(config, opener, ledger, clock).notify(note())

        assert opener.requests == []
        assert "topic" in caplog.text

    def test_gotify_posts_to_message_with_the_token_in_a_header(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set(
            "notifications.channels.gotify",
            {"webhook_url": "https://gotify.example.test", "token": "app-token-1"},
        )
        opener = Opener()

        notifier(config, opener, ledger, clock).notify(note())

        (request,) = opener.requests
        assert request.full_url == "https://gotify.example.test/message"
        assert request.get_header("X-gotify-key") == "app-token-1"
        assert "app-token-1" not in request.full_url
        assert json.loads(request.data)["priority"] == 8  # type: ignore[arg-type]

    def test_gotify_without_a_token_sends_nothing(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set("notifications.channels.gotify", {"webhook_url": "https://gotify.example.test"})
        opener = Opener()

        notifier(config, opener, ledger, clock).notify(note())

        assert opener.requests == []

    def test_teams_gets_an_adaptive_card(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set(
            "notifications.channels.teams",
            {"webhook_url": "https://prod.workflows.example.test/workflows/abc"},
        )
        opener = Opener()

        notifier(config, opener, ledger, clock).notify(note())

        (request,) = opener.requests
        card = json.loads(request.data)["attachments"][0]  # type: ignore[arg-type]
        assert card["contentType"] == "application/vnd.microsoft.card.adaptive"
        assert card["content"]["type"] == "AdaptiveCard"

    def test_the_credentials_of_the_new_channels_never_reach_a_log(
        self, config: Config, ledger: DeliveryLedger, clock: Clock, caplog: pytest.LogCaptureFixture
    ) -> None:
        config.set(
            "notifications.channels.gotify",
            {"webhook_url": "https://gotify.example.test", "token": "app-token-1"},
        )
        opener = Opener()
        opener.errors["https://gotify.example.test"] = URLError(
            "failed to reach https://gotify.example.test/message?token=app-token-1"
        )

        with caplog.at_level(logging.DEBUG):
            notifier(config, opener, ledger, clock).notify(note())

        assert "app-token-1" not in caplog.text
        assert "app-token-1" not in (ledger.recent()[0].last_error or "")


class TestTelegramTopics:
    def configure(self, config: Config, **extra: Any) -> None:
        config.set(
            "notifications.channels.telegram",
            {"bot_token": BOT_TOKEN, "chat_id": "-1001234567890", **extra},
        )

    def test_a_topic_is_where_the_message_is_posted(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        self.configure(config, thread_id=7)
        opener = Opener()

        notifier(config, opener, ledger, clock).notify(note())

        assert json.loads(opener.requests[0].data)["message_thread_id"] == 7  # type: ignore[arg-type]

    def test_each_event_may_have_its_own_topic(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        self.configure(config, thread_id=7, threads={"backup_failed": 11})
        opener = Opener()
        sender = notifier(config, opener, ledger, clock)

        sender.notify(note())
        sender.notify(note(kind="backup_failed"))

        topics = [json.loads(r.data)["message_thread_id"] for r in opener.requests]  # type: ignore[arg-type]
        assert topics == [7, 11]

    def test_without_a_topic_the_message_goes_to_the_chat_itself(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        self.configure(config)
        opener = Opener()

        notifier(config, opener, ledger, clock).notify(note())

        assert "message_thread_id" not in json.loads(opener.requests[0].data)  # type: ignore[arg-type]

    def test_the_plain_text_retry_keeps_the_topic(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        self.configure(config, thread_id=7)
        opener = Opener()
        calls = {"n": 0}
        refusal = http_error(
            "https://api.telegram.org/x",
            400,
            b'{"ok":false,"description":"Bad Request: can\'t parse entities: x"}',
        )

        def flaky(request: Request, timeout: float | None = None) -> io.BytesIO:
            calls["n"] += 1
            if calls["n"] == 1:
                raise refusal
            return opener(request, timeout)

        sender = Notifier(
            config, opener=flaky, ledger=ledger, clock=clock, placement=lambda _d: None
        )
        sender.notify(note())

        (retry,) = opener.requests
        body = json.loads(retry.data)  # type: ignore[arg-type]
        assert "parse_mode" not in body and body["message_thread_id"] == 7


class TestSeveralChannelsOfOneType:
    def test_two_slacks_both_hear_it(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set("notifications.channels.slack", {"webhook_url": SLACK})
        config.set(
            "notifications.channels.slack-staging", {"type": "slack", "webhook_url": SLACK_STAGING}
        )
        opener = Opener()

        notifier(config, opener, ledger, clock).notify(note())

        assert [r.full_url for r in opener.requests] == [SLACK, SLACK_STAGING]

    def test_a_disabled_instance_is_silent(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set("notifications.channels.slack", {"webhook_url": SLACK})
        config.set(
            "notifications.channels.slack-staging",
            {"type": "slack", "webhook_url": SLACK_STAGING, "enabled": False},
        )
        opener = Opener()

        notifier(config, opener, ledger, clock).notify(note())

        assert [r.full_url for r in opener.requests] == [SLACK]

    def test_one_dead_instance_does_not_cost_the_others_the_event(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set("notifications.channels.slack", {"webhook_url": SLACK})
        config.set(
            "notifications.channels.slack-staging", {"type": "slack", "webhook_url": SLACK_STAGING}
        )
        opener = Opener()
        opener.errors[SLACK] = URLError("connection refused")

        notifier(config, opener, ledger, clock).notify(note())

        assert [r.full_url for r in opener.requests] == [SLACK_STAGING]

    def test_a_test_names_the_instance_it_was_sent_through(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set(
            "notifications.channels.slack-staging", {"type": "slack", "webhook_url": SLACK_STAGING}
        )
        opener = Opener()

        error = notifier(config, opener, ledger, clock).test_channel("slack-staging")

        assert error is None
        assert "slack-staging" in opener.requests[0].data.decode()  # type: ignore[union-attr]
        assert ledger.recent() == []

    def test_an_unknown_instance_lists_the_known_ones(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        error = notifier(config, Opener(), ledger, clock).test_channel("nope")

        assert error is not None and "nope" in error and "slack" in error

    def test_the_hint_for_an_unconfigured_instance_names_its_own_keys(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set("notifications.channels.oncall", {"type": "pushover"})

        error = notifier(config, Opener(), ledger, clock).test_channel("oncall")

        assert error == (
            "Channel oncall is not configured; set notifications.channels.oncall.user_key "
            "and app_token first."
        )


class TestPerChannelEvents:
    def test_a_channel_may_switch_on_what_the_server_has_off(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set("notifications.channels.slack", {"webhook_url": SLACK})
        config.set(
            "notifications.channels.slack-staging",
            {
                "type": "slack",
                "webhook_url": SLACK_STAGING,
                "events": {"deploy_started": True},
            },
        )
        opener = Opener()

        notifier(config, opener, ledger, clock).notify(
            note(kind="deploy_started", state=State.PROGRESS)
        )

        assert [r.full_url for r in opener.requests] == [SLACK_STAGING]

    def test_a_channel_may_switch_off_what_the_server_has_on(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set("notifications.channels.slack", {"webhook_url": SLACK})
        config.set(
            "notifications.channels.slack-staging",
            {
                "type": "slack",
                "webhook_url": SLACK_STAGING,
                "events": {"deploy_failed": False},
            },
        )
        opener = Opener()

        notifier(config, opener, ledger, clock).notify(note())

        assert [r.full_url for r in opener.requests] == [SLACK]

    def test_the_server_wide_switch_is_the_default_for_every_channel(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set("notifications.channels.slack", {"webhook_url": SLACK})
        config.set("notifications.events.deploy_failed", False)
        opener = Opener()

        notifier(config, opener, ledger, clock).notify(note())

        assert opener.requests == []

    def test_the_new_events_are_known_to_the_switches(self) -> None:
        events = Config().get("notifications.events")

        for kind in (
            "alert_firing",
            "alert_resolved",
            "backup_missing",
            "cron_failed",
            "cron_success",
            "os_patches",
            "token_expiring",
        ):
            assert kind in events
        assert events["cron_success"] is False


class TestRouting:
    def test_a_channel_hears_only_its_applications(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set("notifications.channels.slack", {"webhook_url": SLACK})
        config.set(
            "notifications.channels.slack-blog",
            {"type": "slack", "webhook_url": SLACK_STAGING, "route": {"apps": ["blog.*"]}},
        )
        opener = Opener()
        sender = notifier(config, opener, ledger, clock)

        sender.notify(note(domain="shop.example.com"))
        sender.notify(note(domain="blog.example.com"))

        assert [r.full_url for r in opener.requests] == [SLACK, SLACK, SLACK_STAGING]

    def test_a_project_route_asks_where_the_application_lives(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set(
            "notifications.channels.slack-shop",
            {"type": "slack", "webhook_url": SLACK, "route": {"projects": ["shop"]}},
        )
        places = {
            "shop.example.com": Placement(project="shop", environment="production"),
            "blog.example.com": Placement(project="blog", environment="production"),
        }
        opener = Opener()
        sender = notifier(config, opener, ledger, clock, placement=places.get)

        sender.notify(note(domain="shop.example.com"))
        sender.notify(note(domain="blog.example.com"))

        assert len(opener.requests) == 1

    def test_a_tag_route_matches_the_applications_tags(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set(
            "notifications.channels.slack-critical",
            {"type": "slack", "webhook_url": SLACK, "route": {"tags": ["critical"]}},
        )
        places = {
            "shop.example.com": Placement(tags=frozenset({"critical"})),
            "blog.example.com": Placement(tags=frozenset({"low"})),
        }
        opener = Opener()
        sender = notifier(config, opener, ledger, clock, placement=places.get)

        sender.notify(note(domain="shop.example.com"))
        sender.notify(note(domain="blog.example.com"))

        assert len(opener.requests) == 1

    def test_the_placement_is_read_once_per_application_per_event(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        for name in ("a", "b", "c"):
            config.set(
                f"notifications.channels.slack-{name}",
                {"type": "slack", "webhook_url": SLACK + name, "route": {"projects": ["shop"]}},
            )
        asked: list[str] = []

        def placement(domain: str) -> Placement:
            asked.append(domain)
            return Placement(project="shop")

        notifier(config, Opener(), ledger, clock, placement=placement).notify(note())

        assert asked == ["shop.example.com"]

    def test_a_minimum_severity_keeps_the_warnings_out(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set(
            "notifications.channels.oncall",
            {
                "type": "pushover",
                "user_key": PUSHOVER_USER,
                "app_token": PUSHOVER_APP,
                "route": {"min_severity": "failed"},
            },
        )
        opener = Opener()
        sender = notifier(config, opener, ledger, clock)

        sender.notify(note(kind="disk_threshold", state=State.WARNING, domain=None))
        sender.notify(note())

        assert len(opener.requests) == 1


class TestTheLedger:
    def test_a_delivery_that_went_out_is_on_record(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set("notifications.channels.slack", {"webhook_url": SLACK})

        notifier(config, Opener(), ledger, clock).notify(note())

        (delivery,) = ledger.recent()
        assert (delivery.state, delivery.channel, delivery.kind) == (
            "sent",
            "slack",
            "deploy_failed",
        )

    def test_a_channel_that_is_down_is_tried_again_later(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set("notifications.channels.slack", {"webhook_url": SLACK})
        opener = Opener()
        opener.errors[SLACK] = http_error(SLACK, 503)
        sender = notifier(config, opener, ledger, clock)

        sender.notify(note())
        (delivery,) = ledger.recent()
        assert delivery.state == "retrying" and delivery.attempts == 1
        assert delivery.next_attempt_at == clock.epoch() + RETRY_DELAYS[0]
        assert "503" in (delivery.last_error or "")

        assert sender.deliver_due() == SweepReport()
        clock.advance(seconds=RETRY_DELAYS[0])
        del opener.errors[SLACK]
        assert sender.deliver_due() == SweepReport(delivered=1)
        assert ledger.recent()[0].state == "sent"
        assert len(opener.to(SLACK)) == 1

    def test_the_wait_grows_and_then_the_delivery_is_given_up(
        self,
        config: Config,
        ledger: DeliveryLedger,
        clock: Clock,
        audited: list[dict[str, Any]],
    ) -> None:
        config.set("notifications.channels.slack", {"webhook_url": SLACK})
        opener = Opener()
        opener.errors[SLACK] = URLError("connection refused")
        sender = notifier(config, opener, ledger, clock)
        sender.notify(note())

        waits = []
        for _ in RETRY_DELAYS:
            delivery = ledger.recent()[0]
            assert delivery.next_attempt_at is not None
            waits.append(delivery.next_attempt_at - clock.epoch())
            clock.advance(seconds=waits[-1])
            sender.deliver_due()

        assert waits == list(RETRY_DELAYS)
        final = ledger.recent()[0]
        assert (final.state, final.attempts) == ("failed", len(RETRY_DELAYS) + 1)
        assert audited[-1]["event"] == "notify.delivery_failed"
        assert audited[-1]["target"] == "channel:slack"
        assert audited[-1]["details"]["attempts"] == len(RETRY_DELAYS) + 1

    def test_an_answer_that_will_not_change_is_not_retried(
        self,
        config: Config,
        ledger: DeliveryLedger,
        clock: Clock,
        audited: list[dict[str, Any]],
    ) -> None:
        config.set("notifications.channels.slack", {"webhook_url": SLACK})
        opener = Opener()
        opener.errors[SLACK] = http_error(SLACK, 404, b"no_service")

        notifier(config, opener, ledger, clock).notify(note())

        (delivery,) = ledger.recent()
        assert delivery.state == "failed" and delivery.next_attempt_at is None
        assert "404" in (delivery.last_error or "")
        assert [e["event"] for e in audited] == ["notify.delivery_failed"]

    def test_a_failure_that_may_pass_is_not_an_audit_event_yet(
        self, config: Config, ledger: DeliveryLedger, clock: Clock, audited: list[dict[str, Any]]
    ) -> None:
        config.set("notifications.channels.slack", {"webhook_url": SLACK})
        opener = Opener()
        opener.errors[SLACK] = http_error(SLACK, 429)

        notifier(config, opener, ledger, clock).notify(note())

        assert audited == []

    def test_a_channel_removed_while_a_delivery_waited_ends_it_with_that_said(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set("notifications.channels.slack", {"webhook_url": SLACK})
        opener = Opener()
        opener.errors[SLACK] = http_error(SLACK, 502)
        sender = notifier(config, opener, ledger, clock)
        sender.notify(note())
        config.set("notifications.channels.slack", {"webhook_url": ""})
        clock.advance(hours=1)

        report = sender.deliver_due()

        assert report.failed == 1
        assert "no longer configured" in (ledger.recent()[0].last_error or "")

    def test_a_ledger_that_cannot_be_written_never_costs_the_delivery(
        self, config: Config, clock: Clock, caplog: pytest.LogCaptureFixture
    ) -> None:
        config.set("notifications.channels.slack", {"webhook_url": SLACK})

        class Broken(DeliveryLedger):
            def record_sent(self, *args: Any, **kwargs: Any) -> int:
                raise OSError("disk full")

        opener = Opener()
        broken = Broken(None)

        with caplog.at_level(logging.WARNING):
            notifier(config, opener, broken, clock).notify(note())

        assert len(opener.requests) == 1
        assert "disk full" in caplog.text

    def test_a_bug_is_not_swallowed_as_if_it_were_a_delivery_problem(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        """Only what delivery can raise is caught (rule 2): anything else is a defect."""
        config.set("notifications.channels.slack", {"webhook_url": SLACK})
        opener = Opener()
        opener.errors[SLACK] = RuntimeError("a bug")

        with pytest.raises(RuntimeError):
            notifier(config, opener, ledger, clock).notify(note())

    def test_without_a_ledger_it_still_delivers(self, config: Config, clock: Clock) -> None:
        config.set("notifications.channels.slack", {"webhook_url": SLACK})
        opener = Opener()

        notifier(config, opener, None, clock).notify(note())

        assert len(opener.requests) == 1

    def test_a_sweep_without_a_ledger_does_nothing(self, config: Config, clock: Clock) -> None:
        assert notifier(config, Opener(), None, clock).deliver_due() == SweepReport()


class TestQuietHours:
    def quiet_slack(self, config: Config, **extra: Any) -> None:
        config.set(
            "notifications.channels.slack",
            {
                "webhook_url": SLACK,
                "quiet": {"start": "22:00", "end": "07:00", **extra},
            },
        )

    def test_a_warning_waits_for_the_morning_and_a_failure_does_not(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        self.quiet_slack(config)
        clock.moment = datetime(2026, 10, 13, 23, 30, tzinfo=timezone.utc)
        opener = Opener()
        sender = notifier(config, opener, ledger, clock)

        sender.notify(note(kind="disk_threshold", state=State.WARNING, domain=None))
        sender.notify(note())

        assert len(opener.requests) == 1
        held = ledger.recent(state="held")
        assert len(held) == 1
        assert (
            held[0].next_attempt_at == datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc).timestamp()
        )

    def test_what_was_held_goes_out_when_the_quiet_hours_end(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        self.quiet_slack(config)
        clock.moment = datetime(2026, 10, 13, 23, 30, tzinfo=timezone.utc)
        opener = Opener()
        sender = notifier(config, opener, ledger, clock)
        sender.notify(note(kind="disk_threshold", state=State.WARNING, domain=None))
        assert sender.deliver_due() == SweepReport()

        clock.moment = datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)
        report = sender.deliver_due()

        assert report.delivered == 1
        assert len(opener.requests) == 1
        assert ledger.recent()[0].state == "sent"

    def test_a_pile_becomes_one_digest(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        self.quiet_slack(config)
        clock.moment = datetime(2026, 10, 13, 23, 0, tzinfo=timezone.utc)
        opener = Opener()
        sender = notifier(config, opener, ledger, clock)
        for index in range(5):
            sender.notify(
                note(
                    kind="disk_threshold",
                    state=State.WARNING,
                    domain=None,
                    subject=f"/mnt/disk{index}",
                )
            )

        clock.moment = datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)
        report = sender.deliver_due()

        assert report.digests == 1 and report.delivered == 5
        (request,) = opener.requests
        text = request.data.decode()  # type: ignore[union-attr]
        assert "Held notifications" in text and "/mnt/disk4" in text
        assert all(d.state == "sent" for d in ledger.recent())

    def test_a_digest_is_not_wanted_when_the_channel_says_so(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        self.quiet_slack(config, digest=False)
        clock.moment = datetime(2026, 10, 13, 23, 0, tzinfo=timezone.utc)
        opener = Opener()
        sender = notifier(config, opener, ledger, clock)
        for _ in range(5):
            sender.notify(note(kind="disk_threshold", state=State.WARNING, domain=None))

        clock.moment = datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)
        report = sender.deliver_due()

        assert report.digests == 0 and len(opener.requests) == 5

    def test_quiet_hours_without_a_ledger_deliver_rather_than_lose(
        self, config: Config, clock: Clock
    ) -> None:
        self.quiet_slack(config)
        clock.moment = datetime(2026, 10, 13, 23, 30, tzinfo=timezone.utc)
        opener = Opener()

        notifier(config, opener, None, clock).notify(
            note(kind="disk_threshold", state=State.WARNING, domain=None)
        )

        assert len(opener.requests) == 1

    def test_quiet_hours_that_came_back_hold_what_was_due(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        self.quiet_slack(config)
        clock.moment = datetime(2026, 10, 13, 23, 0, tzinfo=timezone.utc)
        opener = Opener()
        sender = notifier(config, opener, ledger, clock)
        sender.notify(note(kind="disk_threshold", state=State.WARNING, domain=None))
        # The window is widened while the message waits: 07:00 is quiet now.
        self.quiet_slack(config, **{"end": "09:00"})
        clock.moment = datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)

        report = sender.deliver_due()

        assert report == SweepReport()
        assert opener.requests == []
        (held,) = ledger.recent(state="held")
        assert held.next_attempt_at == datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc).timestamp()


class TestFlood:
    def capped_slack(self, config: Config, **flood: Any) -> None:
        config.set(
            "notifications.channels.slack",
            {"webhook_url": SLACK, "flood": {"max": 3, "minutes": 10, **flood}},
        )

    def fail(self, sender: Notifier, count: int) -> None:
        for index in range(count):
            sender.notify(note(subject=f"app{index}.example.com"))

    def test_past_its_cap_a_channel_holds_the_rest(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        self.capped_slack(config)
        opener = Opener()

        self.fail(notifier(config, opener, ledger, clock), 8)

        assert len(opener.requests) == 3
        held = ledger.recent(state="held")
        assert len(held) == 5
        assert {item.held_for for item in held} == {"flood"}
        assert {item.next_attempt_at for item in held} == {
            (START + timedelta(minutes=10)).timestamp()
        }
        assert ledger.counts()["sent"] == 3

    def test_what_a_flood_held_arrives_as_one_message_once_it_is_over(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        self.capped_slack(config)
        opener = Opener()
        sender = notifier(config, opener, ledger, clock)
        self.fail(sender, 8)
        clock.advance(minutes=9)
        assert sender.deliver_due() == SweepReport()

        clock.advance(minutes=1)
        report = sender.deliver_due()

        assert (report.digests, report.delivered) == (1, 5)
        assert len(opener.requests) == 4
        text = opener.requests[-1].data.decode()  # type: ignore[union-attr]
        assert "Notifications held back" in text and "app4.example.com" in text
        assert ledger.counts()["held"] == 0

    def test_a_few_are_sent_as_they_are(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        self.capped_slack(config, max=1)
        opener = Opener()
        sender = notifier(config, opener, ledger, clock)
        self.fail(sender, 3)
        clock.advance(minutes=10)

        report = sender.deliver_due()

        assert (report.digests, report.delivered) == (0, 2)
        assert len(opener.requests) == 3

    def test_a_cap_that_still_applies_keeps_them_for_another_window(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        self.capped_slack(config)
        opener = Opener()
        sender = notifier(config, opener, ledger, clock)
        self.fail(sender, 5)
        # The window is widened while they wait: three went out within the last hour.
        self.capped_slack(config, minutes=60)
        clock.advance(minutes=10)

        report = sender.deliver_due()

        assert report == SweepReport()
        assert len(opener.requests) == 3
        held = ledger.recent(state="held")
        assert {item.next_attempt_at for item in held} == {
            (clock.moment + timedelta(minutes=60)).timestamp()
        }

    def test_the_cap_is_per_channel(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        self.capped_slack(config)
        config.set(
            "notifications.channels.slack-b", {"type": "slack", "webhook_url": SLACK_STAGING}
        )
        opener = Opener()

        self.fail(notifier(config, opener, ledger, clock), 5)

        assert len(opener.to(SLACK)) == 3
        assert len(opener.to(SLACK_STAGING)) == 5

    def test_a_cap_that_cannot_be_counted_never_silences_a_channel(
        self, config: Config, clock: Clock
    ) -> None:
        self.capped_slack(config)
        opener = Opener()

        self.fail(notifier(config, opener, None, clock), 6)

        assert len(opener.requests) == 6

    def test_a_pager_is_neither_capped_nor_summarised(self) -> None:
        from noust.core.notifications.instances import resolve_instances

        (pager,) = resolve_instances(
            {
                "pagerduty": {
                    "routing_key": "k" * 32,
                    "flood": {"max": 1, "minutes": 10},
                    "summary": {"at": "08:00"},
                }
            }
        )
        assert pager.routing.flood is not None and pager.routing.summary is not None

        for notification in (note(), note(kind="deploy_success", state=State.OK)):
            routing, _judged = Notifier._judged(pager, notification, {})
            assert routing.flood is None and routing.summary is None


class TestSummary:
    def summarised_slack(self, config: Config, **summary: Any) -> None:
        config.set(
            "notifications.channels.slack",
            {"webhook_url": SLACK, "summary": {"at": "08:00", **summary}},
        )

    def succeed(self, sender: Notifier, count: int) -> None:
        for index in range(count):
            sender.notify(
                note(kind="deploy_success", state=State.OK, subject=f"app{index}.example.com")
            )

    def test_what_is_not_a_problem_waits_and_a_failure_does_not(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        self.summarised_slack(config)
        opener = Opener()
        sender = notifier(config, opener, ledger, clock)

        self.succeed(sender, 3)
        sender.notify(note())

        assert len(opener.requests) == 1
        held = ledger.recent(state="held")
        assert {item.held_for for item in held} == {"summary"}
        assert {item.next_attempt_at for item in held} == {
            datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc).timestamp()
        }

    def test_at_its_hour_it_goes_out_as_one_message(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        self.summarised_slack(config)
        opener = Opener()
        sender = notifier(config, opener, ledger, clock)
        self.succeed(sender, 3)
        clock.moment = datetime(2026, 10, 14, 7, 59, tzinfo=timezone.utc)
        assert sender.deliver_due() == SweepReport()

        clock.moment = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)
        report = sender.deliver_due()

        assert (report.digests, report.delivered) == (1, 3)
        (request,) = opener.requests
        text = request.data.decode()  # type: ignore[union-attr]
        assert "Scheduled summary" in text and "app2.example.com" in text

    def test_a_single_item_is_not_dressed_up_as_a_summary(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        self.summarised_slack(config)
        opener = Opener()
        sender = notifier(config, opener, ledger, clock)
        self.succeed(sender, 1)
        clock.moment = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)

        report = sender.deliver_due()

        assert (report.digests, report.delivered) == (0, 1)

    def test_a_summary_that_goes_below_warnings_too(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        self.summarised_slack(config, below="failed")
        opener = Opener()
        sender = notifier(config, opener, ledger, clock)

        sender.notify(note(kind="disk_threshold", state=State.WARNING, domain=None))
        sender.notify(note())

        assert len(opener.requests) == 1
        assert len(ledger.recent(state="held")) == 1

    def test_the_quiet_hours_still_keep_what_they_hold_after_the_summary_hour(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set(
            "notifications.channels.slack",
            {
                "webhook_url": SLACK,
                "summary": {"at": "08:00"},
                "quiet": {"start": "07:00", "end": "09:00", "allow": "none"},
            },
        )
        opener = Opener()
        sender = notifier(config, opener, ledger, clock)
        self.succeed(sender, 3)
        clock.moment = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)

        report = sender.deliver_due()

        assert report == SweepReport() and opener.requests == []
        held = ledger.recent(state="held")
        assert {item.next_attempt_at for item in held} == {
            datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc).timestamp()
        }


class TestHoldsFromBefore:
    def test_a_delivery_held_before_the_cause_was_recorded_is_a_quiet_hours_one(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        config.set(
            "notifications.channels.slack",
            {"webhook_url": SLACK, "quiet": {"start": "22:00", "end": "07:00"}},
        )
        clock.moment = datetime(2026, 10, 13, 23, 0, tzinfo=timezone.utc)
        opener = Opener()
        sender = notifier(config, opener, ledger, clock)
        sender.notify(note(kind="disk_threshold", state=State.WARNING, domain=None))
        with ledger.store._transaction() as cursor:
            cursor.execute("UPDATE notification_deliveries SET held_for = NULL")
        assert ledger.recent(state="held")[0].held_for is None

        clock.moment = datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)
        report = sender.deliver_due()

        assert report.delivered == 1 and len(opener.requests) == 1


class TestTransientFailures:
    @pytest.mark.parametrize(
        ("error", "transient"),
        [
            (http_error("https://x", 500), True),
            (http_error("https://x", 503), True),
            (http_error("https://x", 429), True),
            (http_error("https://x", 408), True),
            (http_error("https://x", 400), False),
            (http_error("https://x", 401), False),
            (http_error("https://x", 404), False),
            (URLError("refused"), True),
            (TimeoutError("slow"), True),
            (ValueError("bad URL"), False),
            (notifier_module._Unresolvable("no such host"), True),
        ],
    )
    def test_what_may_pass_and_what_will_not(self, error: BaseException, transient: bool) -> None:
        assert is_transient(error) is transient

    def test_an_email_transport_failure_may_pass(self) -> None:
        from noust.core.exceptions import NoustError

        assert is_transient(NoustError("smtp down"))
