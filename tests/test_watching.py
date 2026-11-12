# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What Noust watches by itself and where what it finds goes (item 159).

"Tell me if it goes down" was already there, on, and nothing said so: the answer was split between
Alerts, Probes and the channels. :func:`noust.monitor.watching.report` is the one definition of
what is watched and of who hears it, so the console asks and repeats nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest

from noust.core.config import Config
from noust.core.notifications.model import ALL_KINDS, EVENT_KINDS
from noust.monitor import watching
from noust.monitor.watching import NOT_A_WATCH, WATCHES, report

SLACK = "https://hooks.slack.test/services/T0/B0/prod-secret"
NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "noust" / "config.yaml"
    )
    Config.reset_instance()
    try:
        yield Config()
    finally:
        Config.reset_instance()


def _watch(answer: dict, name: str) -> dict:
    [found] = [item for item in answer["watches"] if item["id"] == name]
    return found


def _reach(answer: dict, name: str) -> list[str]:
    return [channel["name"] for channel in _watch(answer, name)["reach"]]


def _slack(config: Config, **block: object) -> None:
    config.set("notifications.enabled", True)
    config.set("notifications.channels.slack", {"webhook_url": SLACK, **block})


class TestTheTable:
    def test_every_event_of_every_watch_is_one_the_notifier_sends(self) -> None:
        for watch in WATCHES:
            assert set(watch.events) <= set(EVENT_KINDS), watch.id

    def test_an_event_is_in_one_watch_at_most(self) -> None:
        seen: dict[str, str] = {}
        for watch in WATCHES:
            for kind in watch.events:
                assert kind not in seen, f"{kind} is in {seen[kind]} and in {watch.id}"
                seen[kind] = watch.id

    def test_every_kind_the_notifier_sends_is_a_watch_or_is_said_not_to_be_one_and_why(
        self,
    ) -> None:
        """
        A kind added to the notifier must be ruled on here, or the page that says what Noust
        warns of would go on saying less than Noust does.
        """
        watched = {kind for watch in WATCHES for kind in watch.events}
        outside = set(NOT_A_WATCH)

        assert watched.isdisjoint(outside)
        assert watched | outside == set(ALL_KINDS)
        assert all(reason.strip() for reason in NOT_A_WATCH.values())

    def test_the_ids_are_unique_and_the_groups_are_the_ones_the_console_words(self) -> None:
        assert len({watch.id for watch in WATCHES}) == len(WATCHES)
        assert {watch.group for watch in WATCHES} <= {
            "applications",
            "domains",
            "server",
            "backups",
            "noust",
        }

    def test_the_settings_a_watch_names_exist_in_the_default_configuration(
        self, config: Config
    ) -> None:
        for watch in WATCHES:
            for key in (watch.off_key, *watch.params.values()):
                if key is not None:
                    assert config.get(key) is not None, f"{watch.id}: {key}"


class TestWhatIsOnAndWhatIsOffered:
    def test_a_fresh_server_watches_everything_but_the_registration_of_domains(
        self, config: Config
    ) -> None:
        answer = report(config, now=NOW)

        states = {watch["id"]: watch["state"] for watch in answer["watches"]}
        assert states.pop("domain_expiry") == "off"
        assert set(states.values()) == {"on"}

    def test_the_registration_of_domains_is_offered_with_what_turning_it_on_writes_and_asks_outside(
        self, config: Config
    ) -> None:
        domains = _watch(report(config, now=NOW), "domain_expiry")

        assert domains["offer"] == {"key": "monitor.domain_expiry", "value": True}
        assert domains["asks_outside"] is True

    def test_nothing_else_is_offered_and_nothing_else_asks_outside(self, config: Config) -> None:
        for watch in report(config, now=NOW)["watches"]:
            if watch["id"] != "domain_expiry":
                assert watch["offer"] is None, watch["id"]
                assert watch["asks_outside"] is False, watch["id"]

    def test_turned_on_it_is_on_and_no_longer_offered(self, config: Config) -> None:
        config.set("monitor.domain_expiry", True)

        domains = _watch(report(config, now=NOW), "domain_expiry")

        assert domains["state"] == "on"
        assert domains["offer"] is None

    @pytest.mark.parametrize(
        ("watch", "key"),
        [
            ("backups_missing", "monitor.backup_missing_days"),
            ("updates", "monitor.os_patches_check_hours"),
            ("tokens", "monitor.token_warning_days"),
            ("releases", "monitor.security_check_hours"),
            ("recipes", "monitor.recipe_check_hours"),
        ],
    )
    def test_a_check_the_operator_turned_off_says_so(
        self, config: Config, watch: str, key: str
    ) -> None:
        config.set(key, 0)

        assert _watch(report(config, now=NOW), watch)["state"] == "off"

    def test_a_watch_whose_every_message_is_switched_off_is_muted_not_off(
        self, config: Config
    ) -> None:
        config.set("notifications.events.disk_threshold", False)

        disk = _watch(report(config, now=NOW), "disk")

        assert disk["state"] == "muted"
        assert [event["on"] for event in disk["events"]] == [False]

    def test_a_watch_with_some_messages_off_stays_on_and_says_which(self, config: Config) -> None:
        config.set("notifications.events.deploy_hook_failed", False)

        deploys = _watch(report(config, now=NOW), "deploys")

        assert deploys["state"] == "on"
        assert {event["kind"]: event["on"] for event in deploys["events"]} == {
            "deploy_failed": True,
            "deploy_rolled_back": True,
            "deploy_hook_failed": False,
        }

    def test_the_numbers_a_sentence_needs_come_from_the_configuration(self, config: Config) -> None:
        config.set("monitor.disk_threshold", 85)
        config.set("monitor.backup_missing_days", 3)

        answer = report(config, now=NOW)

        assert _watch(answer, "disk")["params"] == {"percent": 85}
        assert _watch(answer, "backups_missing")["params"] == {"days": 3}


class TestWhereItGoes:
    def test_with_notifications_off_nothing_reaches_anybody_and_it_says_so(
        self, config: Config
    ) -> None:
        config.set("notifications.channels.slack", {"webhook_url": SLACK})

        answer = report(config, now=NOW)

        assert answer["notifications"]["enabled"] is False
        assert all(watch["reach"] == [] for watch in answer["watches"])

    def test_with_no_channel_configured_nothing_reaches_anybody(self, config: Config) -> None:
        config.set("notifications.enabled", True)

        answer = report(config, now=NOW)

        assert answer["notifications"]["enabled"] is True
        assert answer["notifications"]["channels"] == []
        assert all(watch["reach"] == [] for watch in answer["watches"])

    def test_a_configured_channel_hears_what_is_watched(self, config: Config) -> None:
        _slack(config)

        answer = report(config, now=NOW)

        assert [channel["name"] for channel in answer["notifications"]["channels"]] == ["slack"]
        assert _reach(answer, "apps_down") == ["slack"]
        assert _reach(answer, "domain_expiry") == ["slack"]

    def test_a_channel_with_no_destination_hears_nothing(self, config: Config) -> None:
        config.set("notifications.enabled", True)
        config.set("notifications.channels.slack", {"enabled": True})

        assert report(config, now=NOW)["notifications"]["channels"] == []

    def test_a_channel_that_is_off_hears_nothing(self, config: Config) -> None:
        _slack(config, enabled=False)

        assert report(config, now=NOW)["notifications"]["channels"] == []

    def test_a_channel_that_switched_a_message_off_does_not_hear_that_watch(
        self, config: Config
    ) -> None:
        _slack(config, events={"disk_threshold": False})

        answer = report(config, now=NOW)

        assert _reach(answer, "disk") == []
        assert _reach(answer, "updates") == ["slack"]

    def test_a_channel_that_switched_on_what_the_server_has_off_hears_it(
        self, config: Config
    ) -> None:
        config.set("notifications.events.disk_threshold", False)
        _slack(config, events={"disk_threshold": True})

        assert _reach(report(config, now=NOW), "disk") == ["slack"]

    def test_who_would_hear_a_watch_does_not_depend_on_whether_it_is_on(
        self, config: Config
    ) -> None:
        """What an offer says: if you turn it on, it arrives by Slack."""
        _slack(config)

        assert _reach(report(config, now=NOW), "domain_expiry") == ["slack"]
        config.set("monitor.os_patches_check_hours", 0)
        assert _reach(report(config, now=NOW), "updates") == ["slack"]

    def test_a_channel_that_narrows_what_it_hears_is_marked_limited(self, config: Config) -> None:
        _slack(config, route={"min_severity": "failed"})

        [channel] = _watch(report(config, now=NOW), "disk")["reach"]

        assert channel["limited"] is True

    def test_a_channel_with_quiet_hours_is_marked_limited(self, config: Config) -> None:
        _slack(config, quiet={"start": "23:00", "end": "07:00", "timezone": "Europe/Madrid"})

        [channel] = _watch(report(config, now=NOW), "disk")["reach"]

        assert channel["limited"] is True

    def test_an_unlimited_channel_is_not(self, config: Config) -> None:
        _slack(config)

        [channel] = _watch(report(config, now=NOW), "disk")["reach"]

        assert channel["limited"] is False
        assert channel["type"] == "slack"

    def test_a_pause_is_said_with_when_it_ends(self, config: Config) -> None:
        _slack(config)
        config.set("notifications.muted_until", "2026-11-12T18:00:00+00:00")

        answer = report(config, now=NOW)

        assert answer["notifications"]["paused_until"] == "2026-11-12T18:00:00+00:00"
        # The channels are still the ones configured: the pause ends.
        assert _reach(answer, "disk") == ["slack"]

    def test_a_pause_that_ended_is_not_said(self, config: Config) -> None:
        config.set("notifications.muted_until", "2026-11-12T08:00:00+00:00")

        assert report(config, now=NOW)["notifications"]["paused_until"] is None

    def test_a_teams_own_channel_is_not_the_servers(self, config: Config) -> None:
        config.set("notifications.enabled", True)
        config.set("notifications.channels.slack", {"webhook_url": SLACK, "team": "payments"})

        assert report(config, now=NOW)["notifications"]["channels"] == []


class TestTheWatcher:
    @pytest.mark.parametrize(
        ("probe", "state"),
        [
            ({"installed": True, "enabled": True, "active": True}, "running"),
            ({"installed": True, "enabled": True, "active": False}, "not_running"),
            ({"installed": True, "enabled": False, "active": False}, "disabled"),
            ({"installed": False, "enabled": False, "active": False}, "not_installed"),
            (None, "unknown"),
        ],
    )
    def test_the_monitor_that_does_the_watching_is_said_to_run_or_not(
        self, config: Config, probe: dict | None, state: str
    ) -> None:
        assert report(config, watcher=lambda: probe, now=NOW)["watcher"]["state"] == state

    def test_when_it_does_not_run_the_command_that_starts_it_is_given(self, config: Config) -> None:
        answer = report(
            config, watcher=lambda: {"installed": True, "enabled": False, "active": False}, now=NOW
        )

        assert answer["watcher"]["fix"] == "noust monitor enable"

    def test_when_it_runs_there_is_nothing_to_fix(self, config: Config) -> None:
        answer = report(
            config, watcher=lambda: {"installed": True, "enabled": True, "active": True}, now=NOW
        )

        assert answer["watcher"]["fix"] is None


def test_the_module_keeps_one_table() -> None:
    """The console words each id; a second list would be a second definition (rule 3)."""
    assert watching.WATCHES is WATCHES
