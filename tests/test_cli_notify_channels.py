# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust notify`` and ``noust alert``: channels, events, deliveries and rules.

The commands are thin fronts over the classes the console uses, so what is
asserted here is the part that is theirs: that a credential is never an argument,
that what is not mentioned is kept, that a refusal comes in the configuration's
own words with a non-zero exit, and that the file is really written.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.alerts import AlertRules, AlertState
from noust.core.config import Config
from noust.core.notification_ledger import DeliveryLedger
from noust.core.notifications.model import Notification, State
from noust.core.store import NoustStore

SLACK = "https://hooks.slack.test/services/T0/B0/prod-secret"


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "etc" / "noust" / "config.yaml"
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", path)
    Config.reset_instance()
    try:
        yield path
    finally:
        Config.reset_instance()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        NoustStore.reset_instance()


@pytest.fixture
def run(config_path: Path, store: NoustStore):  # type: ignore[no-untyped-def]
    runner = CliRunner()

    def invoke(*args: str, input: str | None = None):  # type: ignore[no-untyped-def]
        Config.reset_instance()
        return runner.invoke(root_cli, list(args), input=input)

    return invoke


class TestChannelSet:
    def test_a_credential_asked_for_hidden_is_stored_and_never_printed(
        self, run, config_path: Path
    ) -> None:  # type: ignore[no-untyped-def]
        result = run(
            "notify",
            "channel",
            "set",
            "slack",
            "--secret",
            "webhook_url",
            input=f"{SLACK}\n{SLACK}\n",
        )

        assert result.exit_code == 0, result.output
        assert "configured" in result.output
        assert SLACK not in result.output
        assert SLACK in config_path.read_text()

    def test_a_credential_can_be_piped_in(self, run, config_path: Path) -> None:  # type: ignore[no-untyped-def]
        result = run(
            "notify",
            "channel",
            "set",
            "slack-staging",
            "--type",
            "slack",
            "--secret-stdin",
            "webhook_url",
            input=SLACK + "\n",
        )

        assert result.exit_code == 0, result.output
        assert Config().get("notifications.channels.slack-staging.webhook_url") == SLACK

    def test_a_credential_typed_as_an_argument_is_refused(self, run, config_path: Path) -> None:  # type: ignore[no-untyped-def]
        result = run("notify", "channel", "set", "slack", "--set", f"webhook_url={SLACK}")

        assert result.exit_code == 2
        assert "is a credential" in result.output and "--secret webhook_url" in result.output
        assert not config_path.exists()

    def test_a_setting_and_a_second_channel(self, run) -> None:  # type: ignore[no-untyped-def]
        result = run(
            "notify",
            "channel",
            "set",
            "alerts-tg",
            "--type",
            "telegram",
            "--set",
            "chat_id=-1001234567890",
            "--set",
            "thread_id=7",
            "--secret-stdin",
            "bot_token",
            input="110201543:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw\n",
        )

        assert result.exit_code == 0, result.output
        assert Config().get("notifications.channels.alerts-tg.chat_id") == "-1001234567890"
        assert Config().get("notifications.channels.alerts-tg.type") == "telegram"

    def test_events_route_and_quiet_hours(self, run) -> None:  # type: ignore[no-untyped-def]
        result = run(
            "notify",
            "channel",
            "set",
            "oncall",
            "--type",
            "pushover",
            "--set",
            "device=phone",
            "--event",
            "deploy_failed=on",
            "--event",
            "backup_success=off",
            "--route-project",
            "shop",
            "--route-project",
            "blog",
            "--min-severity",
            "failed",
            "--quiet",
            "22:00-07:00",
            "--quiet-timezone",
            "Europe/Madrid",
            "--no-digest",
        )

        assert result.exit_code == 0, result.output
        block = Config().get("notifications.channels.oncall")
        assert block["events"] == {"deploy_failed": True, "backup_success": False}
        assert block["route"] == {"projects": ["shop", "blog"], "min_severity": "failed"}
        assert block["quiet"] == {
            "start": "22:00",
            "end": "07:00",
            "timezone": "Europe/Madrid",
            "digest": False,
        }

    def test_a_cap_and_a_summary(self, run) -> None:  # type: ignore[no-untyped-def]
        result = run(
            "notify",
            "channel",
            "set",
            "oncall",
            "--type",
            "pushover",
            "--flood",
            "20/10",
            "--summary-at",
            "08:00",
            "--summary-timezone",
            "Europe/Madrid",
            "--summary-days",
            "mon, fri",
        )

        assert result.exit_code == 0, result.output
        block = Config().get("notifications.channels.oncall")
        assert block["flood"] == {"max": 20, "minutes": 10}
        assert block["summary"] == {
            "at": "08:00",
            "timezone": "Europe/Madrid",
            "days": ["mon", "fri"],
        }

        listed = {view["name"]: view for view in json.loads(run("notify", "list", "--json").output)}
        assert listed["oncall"]["flood"] == {"max": 20, "minutes": 10}
        assert listed["oncall"]["summary"]["timezone"] == "Europe/Madrid"

        result = run("notify", "channel", "set", "oncall", "--clear-flood", "--clear-summary")
        assert result.exit_code == 0, result.output
        assert not {"flood", "summary"} & set(Config().get("notifications.channels.oncall"))

    def test_changing_one_thing_keeps_the_rest(self, run) -> None:  # type: ignore[no-untyped-def]
        run(
            "notify",
            "channel",
            "set",
            "oncall",
            "--type",
            "pushover",
            "--event",
            "deploy_failed=on",
            "--route-project",
            "shop",
        )

        result = run(
            "notify", "channel", "set", "oncall", "--event", "cron_failed=off", "--disable"
        )

        assert result.exit_code == 0, result.output
        block = Config().get("notifications.channels.oncall")
        assert block["events"] == {"deploy_failed": True, "cron_failed": False}
        assert block["route"] == {"projects": ["shop"]}
        assert block["enabled"] is False

    def test_clearing(self, run) -> None:  # type: ignore[no-untyped-def]
        run(
            "notify",
            "channel",
            "set",
            "oncall",
            "--type",
            "pushover",
            "--event",
            "deploy_failed=on",
            "--route-project",
            "shop",
            "--quiet",
            "22:00-07:00",
        )

        result = run(
            "notify", "channel", "set", "oncall", "--clear-events", "--clear-route", "--clear-quiet"
        )

        assert result.exit_code == 0, result.output
        block = Config().get("notifications.channels.oncall")
        assert not {"events", "route", "quiet"} & set(block)

    @pytest.mark.parametrize(
        ("args", "needle"),
        [
            (["--event", "deploy_failed=maybe"], "must be on or off"),
            (["--event", "no-equals"], "KEY=VALUE"),
            (["--quiet", "late"], "START-END"),
            (["--flood", "lots"], "MAX/MINUTES"),
            (["--flood", "0/10"], "flood.max"),
            (["--summary-below", "warning"], "needs a time"),
            (["--summary-at", "soon"], "summary.at"),
            (["--quiet", "22:00-22:00"], "quiet.start and quiet.end must differ"),
            (["--route-app", "bad name"], "route.apps"),
            (["--set", "colour=red"], "no setting 'colour'"),
        ],
    )
    def test_a_refusal_is_in_the_configurations_words(
        self, run, args: list[str], needle: str
    ) -> None:  # type: ignore[no-untyped-def]
        result = run("notify", "channel", "set", "oncall", "--type", "pushover", *args)

        assert result.exit_code != 0
        assert needle in result.output

    def test_a_channel_without_a_type_is_refused(self, run) -> None:  # type: ignore[no-untyped-def]
        result = run("notify", "channel", "set", "mine")

        assert result.exit_code == 1 and "needs a type" in result.output

    def test_removing(self, run) -> None:  # type: ignore[no-untyped-def]
        run(
            "notify",
            "channel",
            "set",
            "mine",
            "--type",
            "slack",
            "--secret-stdin",
            "webhook_url",
            input=SLACK,
        )

        removed = run("notify", "channel", "remove", "mine")
        again = run("notify", "channel", "remove", "mine")

        assert removed.exit_code == 0 and "removed" in removed.output
        assert "nothing to remove" in again.output
        assert "mine" not in Config().get("notifications.channels")


class TestListingAndTesting:
    def test_the_list_says_what_each_channel_receives(self, run) -> None:  # type: ignore[no-untyped-def]
        run("notify", "channel", "set", "slack", "--secret-stdin", "webhook_url", input=SLACK)
        run(
            "notify",
            "channel",
            "set",
            "oncall",
            "--type",
            "ntfy",
            "--secret-stdin",
            "webhook_url",
            "--route-project",
            "shop",
            input="https://ntfy.example.test/x",
        )

        result = run("--json", "notify", "list")

        assert result.exit_code == 0, result.output
        views = {v["name"]: v for v in json.loads(result.output)}
        assert views["slack"]["configured"] and views["slack"]["settings"] == {"webhook_url": "***"}
        assert views["oncall"]["route"] == {"projects": ["shop"]}
        assert not views["teams"]["configured"]
        assert SLACK not in result.output

    def test_the_table_form(self, run) -> None:  # type: ignore[no-untyped-def]
        run("notify", "channel", "set", "slack", "--secret-stdin", "webhook_url", input=SLACK)

        result = run("notify", "list")

        assert (
            result.exit_code == 0 and "slack" in result.output and "not configured" in result.output
        )

    def test_the_events_are_listed(self, run) -> None:  # type: ignore[no-untyped-def]
        result = run("--json", "notify", "events")

        events = {e["kind"]: e for e in json.loads(result.output)}
        assert events["cron_success"]["default_on"] is False
        assert events["alert_firing"]["enabled"] is True

    def test_an_unknown_channel_is_a_usage_error_listing_the_known_ones(self, run) -> None:  # type: ignore[no-untyped-def]
        result = run("notify", "test", "carrier-pigeon")

        assert result.exit_code == 2
        assert "slack" in result.output


def note(subject: str = "shop.example.com") -> Notification:
    return Notification(
        kind="deploy_failed",
        code="deploy.failed",
        state=State.FAILED,
        locale="en",
        title="Deploy failed",
        subject=subject,
        summary="It stopped.",
        server="web-1",
    )


class TestDeliveries:
    def test_the_record_and_its_filters(self, run, store: NoustStore) -> None:  # type: ignore[no-untyped-def]
        ledger = DeliveryLedger(store)
        ledger.record_sent(note("a.example.com"), "slack", "slack")
        ledger.record_failure(
            note("b.example.com"), "teams", "teams", "HTTP 404 Not Found", retry_in=None
        )

        everything = json.loads(run("--json", "notify", "deliveries").output)
        failed = json.loads(run("--json", "notify", "deliveries", "--state", "failed").output)

        assert [i["subject"] for i in everything["items"]] == ["b.example.com", "a.example.com"]
        assert everything["counts"]["failed"] == 1
        assert [i["last_error"] for i in failed["items"]] == ["HTTP 404 Not Found"]

    def test_an_empty_record_says_so(self, run) -> None:  # type: ignore[no-untyped-def]
        assert "No deliveries" in run("notify", "deliveries").output

    def test_retrying_what_cannot_be_sent_again_fails(self, run, store: NoustStore) -> None:  # type: ignore[no-untyped-def]
        delivery_id = DeliveryLedger(store).record_sent(note(), "slack", "slack")

        result = run("notify", "retry", str(delivery_id))

        assert result.exit_code == 1 and "cannot be sent again" in result.output

    def test_a_sweep_reports_what_it_did(self, run) -> None:  # type: ignore[no-untyped-def]
        result = run("notify", "retry")

        assert result.exit_code == 0 and "0 delivered" in result.output


class TestAlertCommands:
    def test_a_rule_is_set_listed_and_removed(self, run) -> None:  # type: ignore[no-untyped-def]
        result = run(
            "alert",
            "set",
            "cpu-high",
            "--metric",
            "cpu",
            "--above",
            "90%",
            "--for",
            "10m",
            "--hysteresis",
            "5",
            "--cooldown",
            "2h",
            "--severity",
            "failed",
            "--remind",
        )

        assert result.exit_code == 0, result.output
        assert "cpu > 90% for 10m" in result.output
        rule = json.loads(run("--json", "alert", "list").output)[0]
        assert (rule["threshold"], rule["duration_s"], rule["cooldown_s"], rule["hysteresis"]) == (
            90.0,
            600,
            7200,
            5.0,
        )
        assert rule["severity"] == "failed" and rule["remind"] is True

        assert "removed" in run("alert", "remove", "cpu-high").output
        assert run("alert", "remove", "cpu-high").exit_code == 1

    def test_units_are_read_with_the_metric(self, run) -> None:  # type: ignore[no-untyped-def]
        run(
            "alert",
            "set",
            "mem",
            "--metric",
            "app_memory",
            "--app",
            "shop.example.com",
            "--below",
            "256MB",
        )
        run("alert", "set", "net", "--metric", "app_net_tx", "--above", "2MB/s", "--or-equal")

        rules = {r["name"]: r for r in json.loads(run("--json", "alert", "list").output)}

        assert rules["mem"]["threshold"] == 256 * 1024**2 and rules["mem"]["comparator"] == "<"
        assert rules["net"]["threshold"] == 2 * 1024**2 and rules["net"]["comparator"] == ">="

    @pytest.mark.parametrize(
        ("args", "needle"),
        [
            (["--metric", "cpu"], "exactly one of --above or --below"),
            (["--metric", "cpu", "--above", "1", "--below", "2"], "exactly one"),
            (["--metric", "cpu", "--above", "fast"], "Not a limit"),
            (["--metric", "cpu", "--above", "90", "--for", "30s"], "duration"),
            (["--metric", "cpu", "--above", "90", "--for", "soon"], "length of time"),
            (["--metric", "cpu", "--above", "90", "--app", "shop.example.com"], "server metric"),
        ],
    )
    def test_what_cannot_be_a_rule_is_refused(self, run, args: list[str], needle: str) -> None:  # type: ignore[no-untyped-def]
        result = run("alert", "set", "r", *args)

        assert result.exit_code != 0 and needle in result.output
        assert json.loads(run("--json", "alert", "list").output) == []

    def test_switching_and_the_status(self, run, store: NoustStore) -> None:  # type: ignore[no-untyped-def]
        run("alert", "set", "cpu-high", "--metric", "cpu", "--above", "90")
        rules = AlertRules(store)
        saved = rules.get("cpu-high")
        assert saved is not None and saved.id is not None
        rules.put_state(AlertState(saved.id, "", "firing", 1.0, 95.0, announced=True))

        status = json.loads(run("--json", "alert", "status").output)
        assert [(s["rule"], s["last_value"]) for s in status] == [("cpu-high", 95.0)]
        assert "FIRING" in run("alert", "list").output

        assert "off" in run("alert", "disable", "cpu-high").output
        assert json.loads(run("--json", "alert", "status").output) == []
        assert "on" in run("alert", "enable", "cpu-high").output
        assert run("alert", "enable", "nope").exit_code == 1

    def test_the_metrics_are_listed_with_their_units(self, run) -> None:  # type: ignore[no-untyped-def]
        metrics = {m["id"]: m for m in json.loads(run("--json", "alert", "metrics").output)}

        assert metrics["cpu"] == {"id": "cpu", "scope": "server", "unit": "percent"}
        assert metrics["app_net_rx"]["unit"] == "bytes_s"

    def test_nothing_firing_says_so(self, run) -> None:  # type: ignore[no-untyped-def]
        assert "Nothing is firing" in run("alert", "status").output


def test_the_listing_says_what_narrows_a_channel_including_its_cap_and_summary() -> None:
    from noust.cli.commands.notify import _describe
    from noust.core.notification_channels import ChannelView

    view = ChannelView(
        name="oncall",
        type="slack",
        enabled=True,
        configured=True,
        default=False,
        flood={"max": 20, "minutes": 10},
        summary={"at": "08:00", "timezone": "UTC", "below": "warning"},
    )

    described = _describe(view)

    assert "at most 20 per 10 min" in described
    assert "below warning in a summary at 08:00 UTC" in described
