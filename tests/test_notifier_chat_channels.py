# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Mattermost, Matrix, Lark and PagerDuty through the notifier.

Checklist K-07 (Mattermost), K-10 (Matrix and Lark) and K-29 (PagerDuty: an
incident that opens when something breaks and closes when it is fine again).

The suite never opens a socket: the opener is injected and DNS is replaced.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import logging
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from urllib.request import Request

import pytest

import noust.core.notifier as notifier_module
from noust.core.config import Config
from noust.core.notification_ledger import DeliveryLedger
from noust.core.notifications.instances import validate_channels
from noust.core.notifications.model import Fact, Notification, State
from noust.core.notifications.render import pagerduty
from noust.core.notifier import Notifier
from noust.core.notifier_requests import (
    CheckedRequest,
    lark_request,
    matrix_request,
    pagerduty_request,
)
from noust.core.store import NoustStore

START = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
MATTERMOST = "https://chat.example.test/hooks/xyz789secret"
HOMESERVER = "https://matrix.example.test"
ROOM = "!AbCdEf:example.test"
LARK = "https://open.larksuite.test/open-apis/bot/v2/hook/larksecret123"
ROUTING_KEY = "k" * 32


class Opener:
    """Stands in for ``urlopen``: records what would be sent, answers as told."""

    def __init__(self) -> None:
        self.requests: list[Request] = []
        self.errors: dict[str, Exception] = {}
        self.reply = b"ok"

    def __call__(self, request: Request, timeout: float | None = None) -> io.BytesIO:
        for prefix, error in self.errors.items():
            if request.full_url.startswith(prefix):
                raise error
        self.requests.append(request)
        return io.BytesIO(self.reply)

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.data) for r in self.requests]  # type: ignore[arg-type]


def http_error(url: str, code: int, body: bytes = b"") -> HTTPError:
    return HTTPError(url, code, "Reason", {}, io.BytesIO(body))  # type: ignore[arg-type]


def note(
    kind: str = "deploy_failed",
    state: State = State.FAILED,
    domain: str | None = "shop.example.com",
) -> Notification:
    return Notification(
        kind=kind,
        code=f"{kind}.x",
        state=state,
        locale="en",
        title="Deploy failed" if state is State.FAILED else "Back to normal",
        subject=domain or "",
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
def ledger(tmp_path: Path) -> Iterator[DeliveryLedger]:
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    yield DeliveryLedger(store, clock=lambda: START.timestamp())
    NoustStore.reset_instance()


def notifier(config: Config, opener: Opener, ledger: DeliveryLedger) -> Notifier:
    return Notifier(
        config,
        opener=opener,
        ledger=ledger,
        clock=lambda: START,
        placement=lambda _domain: None,
    )


class TestMattermost:
    def test_the_message_is_an_attachment_a_mattermost_server_reads(
        self, config: Config, ledger: DeliveryLedger
    ) -> None:
        config.set("notifications.channels.mattermost", {"webhook_url": MATTERMOST})
        opener = Opener()

        notifier(config, opener, ledger).notify(note())

        (request,) = opener.requests
        assert request.full_url == MATTERMOST
        attachment = opener.bodies()[0]["attachments"][0]
        assert "Deploy failed" in attachment["title"]
        assert attachment["color"].startswith("#")
        assert "blocks" not in attachment

    def test_a_url_that_is_not_http_is_refused(
        self, config: Config, ledger: DeliveryLedger
    ) -> None:
        config._config["notifications"]["channels"]["mattermost"] = {
            "webhook_url": "file:///etc/passwd"
        }
        opener = Opener()

        notifier(config, opener, ledger).notify(note())

        assert opener.requests == []


class TestMatrix:
    def configure(self, config: Config, name: str = "matrix", **extra: Any) -> None:
        block = {
            "homeserver": HOMESERVER,
            "room_id": ROOM,
            "access_token": "syt_secret_token",
            **extra,
        }
        config.set(f"notifications.channels.{name}", block)

    def test_the_event_is_put_in_the_room_with_the_token_in_a_header(
        self, config: Config, ledger: DeliveryLedger
    ) -> None:
        self.configure(config)
        opener = Opener()

        notifier(config, opener, ledger).notify(note())

        (request,) = opener.requests
        assert request.get_method() == "PUT"
        assert request.get_header("Authorization") == "Bearer syt_secret_token"
        assert request.full_url.startswith(
            f"{HOMESERVER}/_matrix/client/v3/rooms/%21AbCdEf%3Aexample.test/send/m.room.message/"
        )
        assert "syt_secret_token" not in request.full_url
        body = opener.bodies()[0]
        assert body["msgtype"] == "m.text" and "Deploy failed" in body["body"]

    def test_a_retry_is_the_same_transaction_and_two_channels_are_two(self, config: Config) -> None:
        n = note()

        def transaction(channel: str, notification: Notification = n) -> str:
            request = matrix_request(HOMESERVER, ROOM, "t", notification, channel)
            return unquote(request.full_url.rsplit("/", 1)[1])

        assert transaction("matrix") == transaction("matrix")
        assert transaction("matrix") != transaction("matrix-ops")
        assert transaction("matrix") != transaction("matrix", note())

    @pytest.mark.parametrize(
        "room", ["#alias:example.test", "AbCdEf:example.test", "!no-server", ""]
    )
    def test_a_room_that_is_not_an_id_is_refused(self, room: str) -> None:
        with pytest.raises(ValueError, match="room_id"):
            matrix_request(HOMESERVER, room, "t", note(), "matrix")

    def test_a_homeserver_that_is_not_http_is_refused(self) -> None:
        with pytest.raises(ValueError, match="homeserver"):
            matrix_request("ftp://matrix.example.test", ROOM, "t", note(), "matrix")

    def test_a_room_id_is_checked_when_it_is_written(self) -> None:
        with pytest.raises(ValueError, match="room_id"):
            validate_channels({"matrix": {"homeserver": HOMESERVER, "room_id": "#alias:x.test"}})

    def test_a_channel_lacking_a_part_sends_nothing(
        self, config: Config, ledger: DeliveryLedger
    ) -> None:
        config.set("notifications.channels.matrix", {"homeserver": HOMESERVER, "room_id": ROOM})
        opener = Opener()

        notifier(config, opener, ledger).notify(note())

        assert opener.requests == []

    def test_the_token_never_reaches_a_log_or_the_record(
        self, config: Config, ledger: DeliveryLedger, caplog: pytest.LogCaptureFixture
    ) -> None:
        self.configure(config)
        opener = Opener()
        opener.errors[HOMESERVER] = URLError("failed Bearer syt_secret_token")

        with caplog.at_level(logging.DEBUG):
            notifier(config, opener, ledger).notify(note())

        assert "syt_secret_token" not in caplog.text
        assert "syt_secret_token" not in (ledger.recent()[0].last_error or "")


class TestLark:
    def test_an_unsigned_bot_gets_the_card_and_nothing_else(
        self, config: Config, ledger: DeliveryLedger
    ) -> None:
        config.set("notifications.channels.lark", {"webhook_url": LARK})
        opener = Opener()
        opener.reply = b'{"code":0,"msg":"success"}'

        notifier(config, opener, ledger).notify(note())

        body = opener.bodies()[0]
        assert body["msg_type"] == "interactive" and "sign" not in body
        assert opener.requests[0].full_url == LARK

    def test_a_signed_bot_gets_the_signature_lark_computes(self) -> None:
        request = lark_request(LARK, note(), "bot-secret", now=1_700_000_000)

        body = json.loads(request.data)  # type: ignore[arg-type]
        expected = base64.b64encode(
            hmac.new(b"1700000000\nbot-secret", digestmod=hashlib.sha256).digest()
        ).decode()
        assert body["timestamp"] == "1700000000"
        assert body["sign"] == expected

    def test_a_refusal_that_comes_with_http_200_is_a_failed_delivery_not_a_sent_one(
        self, config: Config, ledger: DeliveryLedger
    ) -> None:
        config.set(
            "notifications.channels.lark", {"webhook_url": LARK, "secret": "bot-signing-secret"}
        )
        opener = Opener()
        opener.reply = b'{"code":19021,"msg":"sign match fail or timestamp is not within one hour"}'

        notifier(config, opener, ledger).notify(note())

        (delivery,) = ledger.recent()
        assert delivery.state == "failed"
        assert "19021" in (delivery.last_error or "")
        assert "sign match fail" in (delivery.last_error or "")

    def test_an_answer_that_is_not_json_is_taken_as_sent(
        self, config: Config, ledger: DeliveryLedger
    ) -> None:
        config.set("notifications.channels.lark", {"webhook_url": LARK})
        opener = Opener()
        opener.reply = b"<html>proxy</html>"

        notifier(config, opener, ledger).notify(note())

        assert ledger.recent()[0].state == "sent"

    def test_the_request_knows_it_has_an_answer_to_judge(self) -> None:
        assert isinstance(lark_request(LARK, note()), CheckedRequest)


class TestPagerDuty:
    def configure(self, config: Config, **extra: Any) -> None:
        config.set(
            "notifications.channels.pagerduty",
            {"routing_key": ROUTING_KEY, "region": "us", **extra},
        )

    def test_a_failure_opens_an_incident(self, config: Config, ledger: DeliveryLedger) -> None:
        self.configure(config)
        opener = Opener()

        notifier(config, opener, ledger).notify(note())

        (request,) = opener.requests
        assert request.full_url == "https://events.pagerduty.com/v2/enqueue"
        body = opener.bodies()[0]
        assert body["event_action"] == "trigger"
        assert body["routing_key"] == ROUTING_KEY
        assert body["payload"]["severity"] == "critical"

    def test_the_eu_service_region_has_its_own_endpoint(
        self, config: Config, ledger: DeliveryLedger
    ) -> None:
        self.configure(config, region="eu")
        opener = Opener()

        notifier(config, opener, ledger).notify(note())

        assert opener.requests[0].full_url == "https://events.eu.pagerduty.com/v2/enqueue"

    def test_the_recovery_closes_the_incident_the_failure_opened(
        self, config: Config, ledger: DeliveryLedger
    ) -> None:
        self.configure(config)
        opener = Opener()
        sender = notifier(config, opener, ledger)

        sender.notify(note("app_unreachable", State.FAILED))
        sender.notify(note("app_recovered", State.OK))

        opened, closed = opener.bodies()
        assert (opened["event_action"], closed["event_action"]) == ("trigger", "resolve")
        assert opened["dedup_key"] == closed["dedup_key"]

    def acknowledged(self) -> Notification:
        return replace(
            note("alert_acknowledged", State.INFO),
            facts=(Fact("rule", "Rule", "cpu-high"),),
            domain=None,
            subject="cpu-high",
        )

    def firing(self) -> Notification:
        return replace(
            note("alert_firing", State.FAILED),
            facts=(Fact("rule", "Rule", "cpu-high"),),
            domain=None,
            subject="cpu-high",
        )

    def test_an_alert_somebody_took_acknowledges_the_incident_its_firing_opened(
        self, config: Config, ledger: DeliveryLedger
    ) -> None:
        self.configure(config)
        opener = Opener()
        sender = notifier(config, opener, ledger)

        sender.notify(self.firing())
        sender.notify(self.acknowledged())

        opened, taken = opener.bodies()
        assert (opened["event_action"], taken["event_action"]) == ("trigger", "acknowledge")
        assert opened["dedup_key"] == taken["dedup_key"]
        assert set(taken) == {"routing_key", "event_action", "dedup_key"}

    def test_a_severity_floor_and_quiet_hours_do_not_keep_the_page_from_being_acknowledged(
        self, config: Config, ledger: DeliveryLedger
    ) -> None:
        self.configure(
            config,
            route={"min_severity": "failed"},
            quiet={"start": "11:00", "end": "13:00", "timezone": "UTC", "allow": "failed"},
        )
        opener = Opener()
        sender = notifier(config, opener, ledger)

        sender.notify(self.firing())
        sender.notify(self.acknowledged())

        assert [b["event_action"] for b in opener.bodies()] == ["trigger", "acknowledge"]
        assert "held" not in [d.state for d in ledger.recent()]

    def test_a_channel_that_does_not_hear_the_alert_does_not_hear_it_was_taken(
        self, config: Config, ledger: DeliveryLedger
    ) -> None:
        self.configure(config, events={"alert_firing": False})
        opener = Opener()

        notifier(config, opener, ledger).notify(self.acknowledged())

        assert opener.requests == []

    def test_the_plan_names_the_acknowledgement_and_the_incident(self) -> None:
        planned = pagerduty.plan(self.acknowledged())

        assert planned is not None and planned.action == "acknowledge"
        assert planned.dedup_key == pagerduty.dedup_key(self.firing())
        assert pagerduty.render(self.acknowledged(), ROUTING_KEY, planned) == pagerduty.acknowledge(
            ROUTING_KEY, planned.dedup_key
        )

    def test_what_is_not_a_problem_is_not_sent_and_leaves_no_record(
        self, config: Config, ledger: DeliveryLedger
    ) -> None:
        self.configure(config)
        opener = Opener()

        notifier(config, opener, ledger).notify(note("deploy_started", State.PROGRESS))
        notifier(config, opener, ledger).notify(note("cert_expiring", State.OK))

        assert opener.requests == []
        assert ledger.recent() == []

    def test_a_severity_floor_does_not_keep_a_recovery_from_closing_the_incident(
        self, config: Config, ledger: DeliveryLedger
    ) -> None:
        self.configure(config, route={"min_severity": "failed"})
        opener = Opener()
        sender = notifier(config, opener, ledger)

        sender.notify(note("app_unreachable", State.FAILED))
        sender.notify(note("disk_threshold", State.WARNING, domain=None))
        sender.notify(note("app_recovered", State.OK))

        assert [b["event_action"] for b in opener.bodies()] == ["trigger", "resolve"]

    def test_quiet_hours_do_not_hold_a_recovery(
        self, config: Config, ledger: DeliveryLedger
    ) -> None:
        self.configure(
            config, quiet={"start": "11:00", "end": "13:00", "timezone": "UTC", "allow": "failed"}
        )
        opener = Opener()
        sender = notifier(config, opener, ledger)

        sender.notify(note("app_unreachable", State.FAILED))
        sender.notify(note("disk_threshold", State.WARNING, domain=None))
        sender.notify(note("app_recovered", State.OK))

        assert [b["event_action"] for b in opener.bodies()] == ["trigger", "resolve"]
        assert [d.state for d in ledger.recent()].count("held") == 1

    def test_a_recovery_follows_the_switch_of_the_problem_it_closes(
        self, config: Config, ledger: DeliveryLedger
    ) -> None:
        # backup_success ships off, backup_failed on: the channel hears the
        # failure, so it must hear the backup that fixed it.
        self.configure(config)
        opener = Opener()
        sender = notifier(config, opener, ledger)

        sender.notify(note("backup_failed", State.FAILED))
        sender.notify(note("backup_success", State.OK))

        assert [b["event_action"] for b in opener.bodies()] == ["trigger", "resolve"]

    def test_a_channel_that_does_not_hear_the_problem_does_not_hear_the_recovery(
        self, config: Config, ledger: DeliveryLedger
    ) -> None:
        self.configure(config, events={"backup_failed": False})
        opener = Opener()

        notifier(config, opener, ledger).notify(note("backup_success", State.OK))

        assert opener.requests == []

    def test_a_recovery_of_something_switched_on_by_default_is_not_heard_without_its_problem(
        self, config: Config, ledger: DeliveryLedger
    ) -> None:
        # app_recovered ships on, but nothing opened an incident: app_unreachable is off here.
        self.configure(config, events={"app_unreachable": False})
        opener = Opener()

        notifier(config, opener, ledger).notify(note("app_recovered", State.OK))

        assert opener.requests == []

    def test_the_test_button_sends_an_info_event(
        self, config: Config, ledger: DeliveryLedger
    ) -> None:
        self.configure(config)
        opener = Opener()

        error = notifier(config, opener, ledger).test_channel("pagerduty")

        assert error is None
        assert opener.bodies()[0]["payload"]["severity"] == "info"

    @pytest.mark.parametrize("key", ["short", "k" * 33, "k" * 31 + "!"])
    def test_a_key_that_is_not_an_integration_key_is_refused(self, key: str) -> None:
        with pytest.raises(ValueError, match="routing_key"):
            pagerduty_request(key, note())

    def test_a_region_that_is_not_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="region"):
            pagerduty_request(ROUTING_KEY, note(), "asia")
        with pytest.raises(ValueError, match="region"):
            validate_channels({"pagerduty": {"routing_key": ROUTING_KEY, "region": "asia"}})

    def test_a_refused_key_is_not_tried_again_and_a_busy_service_is(
        self, config: Config, ledger: DeliveryLedger
    ) -> None:
        self.configure(config)
        opener = Opener()
        opener.errors["https://events.pagerduty.com"] = http_error(
            "https://events.pagerduty.com/v2/enqueue", 400, b'{"message":"Invalid routing key"}'
        )
        notifier(config, opener, ledger).notify(note())
        opener.errors["https://events.pagerduty.com"] = http_error(
            "https://events.pagerduty.com/v2/enqueue", 503
        )
        notifier(config, opener, ledger).notify(note("app_unreachable", State.FAILED))

        states = sorted(d.state for d in ledger.recent())
        assert states == ["failed", "retrying"]

    def test_the_key_never_reaches_a_log(
        self, config: Config, ledger: DeliveryLedger, caplog: pytest.LogCaptureFixture
    ) -> None:
        self.configure(config)
        opener = Opener()
        opener.errors["https://events.pagerduty.com"] = URLError(f"failed for {ROUTING_KEY}")

        with caplog.at_level(logging.DEBUG):
            notifier(config, opener, ledger).notify(note())

        assert ROUTING_KEY not in caplog.text
        assert ROUTING_KEY not in (ledger.recent()[0].last_error or "")


class TestTheNewTypesAreChannelsLikeTheOthers:
    def test_a_second_instance_of_a_new_type_can_be_named(
        self, config: Config, ledger: DeliveryLedger
    ) -> None:
        config.set(
            "notifications.channels.oncall",
            {"type": "pagerduty", "routing_key": ROUTING_KEY},
        )
        opener = Opener()

        notifier(config, opener, ledger).notify(note())

        assert [d.channel for d in ledger.recent()] == ["oncall"]
        assert [d.channel_type for d in ledger.recent()] == ["pagerduty"]
