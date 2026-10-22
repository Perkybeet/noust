# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Noust updating itself when nobody asks (checklist N-28): the settings, the timer that follows
them, and the run the timer starts, with the update check, the clock and the install faked.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from noust.core import audit, paths
from noust.core.config import Config
from noust.core.exceptions import ConfigError, NoustError, ValidationError
from noust.core.update_checker import UpdateCheckInProgress, VersionCheck, _location
from noust.managers import auto_update
from noust.managers.auto_update import AutoUpdate, AutoUpdateSettings, decide, next_window
from noust.managers.self_update import SelfUpdateRecord, SelfUpdateRefused

RUNNING = "3.4.0"


def check(
    *,
    installable: str | None = None,
    published: str | None = None,
    security: str | None = None,
    method: str = "pip",
) -> VersionCheck:
    return VersionCheck(
        current=RUNNING,
        location=_location(),
        method=method,
        installable=installable,
        published=published or installable,
        checked_at=0.0,
        security=security,
    )


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "etc" / "noust" / "config.yaml"
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", path)
    Config.reset_instance()
    try:
        yield path
    finally:
        Config.reset_instance()


@dataclass
class FakeScheduler:
    installed: list[dict[str, Any]] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    broken: bool = False

    def install_unit_pair(
        self,
        unit: str,
        prefix: str,
        what: str,
        timer: str,
        service: str,
        calendar: str,
        **values: str,
    ) -> None:
        if self.broken:
            raise NoustError("Failed to enable timer")
        self.installed.append(
            {
                "unit": unit,
                "prefix": prefix,
                "timer": timer,
                "service": service,
                "calendar": calendar,
                **values,
            }
        )

    def remove_unit_pair(self, unit: str, prefix: str) -> bool:
        self.removed.append(unit)
        return True

    def unit_state(self, unit: str) -> dict[str, Any]:
        return {"installed": bool(self.installed), "next_run": None, "last_run": None}


class FakeUpdater:
    """Stands for SelfUpdate: it records what would have been installed."""

    version = RUNNING

    def __init__(
        self,
        *,
        refusal: SelfUpdateRefused | None = None,
        start_refusal: SelfUpdateRefused | None = None,
        ends: str = "installed",
    ) -> None:
        self._refusal = refusal
        self._start_refusal = start_refusal
        self.ends = ends
        self.started: list[dict[str, Any]] = []

    def refusal(self) -> SelfUpdateRefused | None:
        return self._refusal

    def start(
        self,
        *,
        target_version: str | None = None,
        job_id: str | None = None,
        actor: str | None = None,
        on_line: Any = None,
    ) -> SelfUpdateRecord:
        if self._start_refusal is not None:
            raise self._start_refusal
        self.started.append({"target": target_version, "actor": actor})
        return SelfUpdateRecord(
            id="abc",
            method="pip",
            from_version=RUNNING,
            argv=["pip"],
            target_version=target_version,
            unit="noust-self-update-abc",
        )

    def follow(self, record: SelfUpdateRecord, on_line: Any) -> SelfUpdateRecord:
        record.status = self.ends
        if self.ends == "failed":
            record.error = "pip failed"
            record.tail = ["ERROR: no matching distribution"]
        else:
            record.to_version = record.target_version
        return record


def window_at(text: str) -> datetime:
    """2026-10-14 is a Wednesday; the default window is 03:00-05:00."""
    return datetime.fromisoformat(f"2026-10-{text}")


class Harness:
    def __init__(
        self,
        tmp_path: Path,
        *,
        now: str = "14T03:30",
        found: VersionCheck | None = None,
        busy: bool = False,
        check_enabled: bool = True,
        updater: FakeUpdater | None = None,
    ) -> None:
        self.scheduler = FakeScheduler()
        self.updater = updater or FakeUpdater()
        self.checks = 0
        self.found = found
        self.now = window_at(now)
        self.busy = busy
        self.check_enabled = check_enabled
        self.manager = AutoUpdate(
            scheduler=self.scheduler,  # type: ignore[arg-type]
            self_update=lambda: self.updater,  # type: ignore[arg-type,return-value]
            check=self._check,
            check_enabled=lambda: self.check_enabled,
            busy=lambda: self.busy,
            clock=lambda: self.now,
            state_path=tmp_path / "auto-update.json",
        )

    def _check(self) -> VersionCheck:
        self.checks += 1
        if self.found is None:
            raise UpdateCheckInProgress("reading")
        return self.found

    def turn_on(self, **changes: Any) -> AutoUpdateSettings:
        return self.manager.set_settings(enabled=True, **changes)


@pytest.fixture
def harness(tmp_path: Path, config_path: Path) -> Harness:
    return Harness(tmp_path, found=check(installable="3.4.1"))


class TestSettings:
    def test_is_off_until_an_operator_turns_it_on(self, harness: Harness) -> None:
        assert harness.manager.settings() == AutoUpdateSettings()
        assert not harness.manager.settings().enabled

    def test_turning_it_on_writes_the_timer_and_the_settings(
        self, harness: Harness, config_path: Path
    ) -> None:
        settings = harness.turn_on(channel="stable", window="02:00-04:00", days="mon,thu")

        assert settings.to_dict() == {
            "enabled": True,
            "channel": "stable",
            "window": "02:00-04:00",
            "days": "mon,thu",
        }
        (timer,) = harness.scheduler.installed
        assert timer["unit"] == paths.AUTO_UPDATE_UNIT
        assert (timer["timer"], timer["service"]) == (
            "auto-update-timer.j2",
            "auto-update-service.j2",
        )
        assert timer["calendar"] == "Mon,Thu *-*-* 02,03:00/10:00"
        assert harness.manager.settings() == settings
        assert "stable" in config_path.read_text()

    def test_a_window_across_midnight_installs_both_halves(self, harness: Harness) -> None:
        harness.turn_on(window="23:00-01:00", days="sun")
        (timer,) = harness.scheduler.installed
        assert timer["calendar"] == "Sun *-*-* 23:00/10:00"
        assert timer["more"] == "Mon *-*-* 00:00/10:00"

    def test_turning_it_off_removes_the_timer_and_keeps_the_choices(self, harness: Harness) -> None:
        harness.turn_on(channel="security")
        settings = harness.manager.set_settings(enabled=False)
        assert harness.scheduler.removed == [paths.AUTO_UPDATE_UNIT]
        assert not settings.enabled
        assert settings.channel == "security"

    def test_changing_one_thing_leaves_the_rest(self, harness: Harness) -> None:
        harness.turn_on(channel="security", window="01:00-02:00", days="wed")
        settings = harness.manager.set_settings(window="04:00-05:00")
        assert settings.to_dict() == {
            "enabled": True,
            "channel": "security",
            "window": "04:00-05:00",
            "days": "wed",
        }

    @pytest.mark.parametrize(
        "change",
        [
            {"channel": "next"},
            {"window": "25:00-26:00"},
            {"window": "03:00-03:00"},
            {"days": "funday"},
        ],
    )
    def test_refuses_what_the_rules_refuse_and_changes_nothing(
        self, harness: Harness, change: dict[str, str]
    ) -> None:
        before = harness.manager.settings()
        with pytest.raises(ValidationError):
            harness.manager.set_settings(enabled=True, **change)
        assert harness.manager.settings() == before
        assert harness.scheduler.installed == []

    def test_cannot_be_turned_on_where_this_installation_cannot_update_itself(
        self, tmp_path: Path, config_path: Path
    ) -> None:
        refusal = SelfUpdateRefused(
            "container_image",
            "This Noust runs from a container image",
            "On the Docker host: docker compose pull",
        )
        manager = Harness(tmp_path, updater=FakeUpdater(refusal=refusal))
        with pytest.raises(NoustError) as error:
            manager.turn_on()
        assert "container image" in str(error.value)
        assert manager.scheduler.installed == []
        assert not manager.manager.settings().enabled

    def test_a_timer_that_cannot_be_written_leaves_the_old_settings_in_force(
        self, harness: Harness
    ) -> None:
        harness.scheduler.broken = True
        with pytest.raises(NoustError):
            harness.turn_on(channel="stable")
        assert harness.manager.settings() == AutoUpdateSettings()

    def test_the_configuration_file_enforces_the_same_rules(self, config_path: Path) -> None:
        config = Config()
        with pytest.raises(ConfigError):
            config.set("updates.auto.window", "soon")
        with pytest.raises(ConfigError):
            config.set("updates.auto.channel", "nightly")
        config.set("updates.auto.days", "Thu,mon")
        assert config.get("updates.auto.days") == "mon,thu"

    def test_a_hand_edit_the_rules_refuse_turns_it_off_rather_than_run_half_a_policy(
        self, config_path: Path
    ) -> None:
        config = Config()
        config.set("updates.auto.enabled", True)
        config._config["updates"]["auto"]["window"] = (
            "whenever"  # as a hand edit would have left it
        )
        assert not AutoUpdateSettings.from_config(config).enabled


class TestDeciding:
    def test_nothing_to_do_when_up_to_date(self) -> None:
        assert decide(check(), AutoUpdateSettings()).action == "none"

    def test_waits_for_a_release_whose_package_is_still_being_built(self) -> None:
        decision = decide(check(installable=None, published="3.4.1"), AutoUpdateSettings())
        assert (decision.action, decision.reason) == ("wait", "on_the_way")

    def test_updates_to_a_patch_release_on_the_default_channel(self) -> None:
        decision = decide(check(installable="3.4.1"), AutoUpdateSettings())
        assert (decision.action, decision.target) == ("update", "3.4.1")

    def test_leaves_a_new_minor_to_a_person_on_the_default_channel_and_says_why(self) -> None:
        decision = decide(check(installable="3.5.0"), AutoUpdateSettings())
        assert (decision.action, decision.reason) == ("none", "outside_channel")
        assert "upgrade guide" in decision.detail

    def test_takes_a_new_minor_on_the_stable_channel(self) -> None:
        assert (
            decide(check(installable="3.5.0"), AutoUpdateSettings(channel="stable")).action
            == "update"
        )

    def test_the_security_channel_takes_only_a_release_that_carries_the_fix(self) -> None:
        settings = AutoUpdateSettings(channel="security")
        assert decide(check(installable="3.4.1"), settings).action == "none"
        assert decide(check(installable="3.4.1", security="3.4.1"), settings).action == "update"
        # The fix exists but this server's package source has not got it yet: not a security update.
        assert (
            decide(check(installable="3.4.1", security="3.4.2", published="3.4.2"), settings).action
            == "none"
        )

    def test_never_takes_a_new_major(self) -> None:
        for channel in ("security", "patch", "stable"):
            assert (
                decide(
                    check(installable="4.0.0", security="4.0.0"),
                    AutoUpdateSettings(channel=channel),
                ).action
                == "none"
            )


class TestNextWindow:
    def test_is_now_while_it_is_open(self) -> None:
        assert next_window(AutoUpdateSettings(), window_at("14T03:30:20")) == window_at("14T03:30")

    def test_is_today_before_it_opens_and_tomorrow_after_it_closed(self) -> None:
        assert next_window(AutoUpdateSettings(), window_at("14T01:00")) == window_at("14T03:00")
        assert next_window(AutoUpdateSettings(), window_at("14T06:00")) == window_at("15T03:00")

    def test_skips_days_the_window_does_not_open_on(self) -> None:
        settings = AutoUpdateSettings(days=("mon",))
        assert next_window(settings, window_at("14T06:00")) == window_at(
            "19T03:00"
        )  # Monday the 19th


class TestRunning:
    def test_updates_inside_the_window_and_says_what_it_did(self, harness: Harness) -> None:
        harness.turn_on()
        run = harness.manager.run()
        assert (run.outcome, run.reason, run.from_version, run.to_version) == (
            "updated",
            "updated",
            RUNNING,
            "3.4.1",
        )
        assert harness.updater.started == [{"target": "3.4.1", "actor": "auto-update"}]
        assert harness.manager.last_run()[0] == run

    def test_records_the_update_in_the_audit_trail(self, harness: Harness) -> None:
        harness.turn_on()
        harness.manager.run()
        events = [
            event for event in audit.get_log().read() if event["action"] == "system.auto_update"
        ]
        assert len(events) == 1
        assert events[0]["result"] == "ok"
        assert events[0]["details"]["to_version"] == "3.4.1"
        assert events[0]["who"]["name"] == "auto-update"

    def test_does_nothing_while_it_is_off(self, harness: Harness) -> None:
        run = harness.manager.run()
        assert (run.outcome, run.reason) == ("skipped", "disabled")
        assert harness.checks == 0
        assert harness.updater.started == []

    def test_does_nothing_outside_the_window_whatever_started_it(self, harness: Harness) -> None:
        harness.turn_on()
        harness.now = window_at("14T12:00")
        run = harness.manager.run()
        assert run.reason == "outside_window"
        assert harness.checks == 0
        assert harness.updater.started == []

    def test_does_nothing_on_a_day_the_window_does_not_open_on(self, harness: Harness) -> None:
        harness.turn_on(days="mon")
        assert harness.manager.run().reason == "outside_window"

    def test_an_operator_who_runs_it_now_means_it(self, harness: Harness) -> None:
        harness.turn_on()
        harness.now = window_at("14T12:00")
        run = harness.manager.run(trigger="manual", force=True)
        assert run.outcome == "updated"
        assert run.trigger == "manual"

    def test_a_forced_run_still_obeys_the_channel(self, harness: Harness) -> None:
        harness.found = check(installable="3.5.0")
        run = harness.manager.run(trigger="manual", force=True)
        assert (run.outcome, run.reason) == ("skipped", "outside_channel")
        assert harness.updater.started == []

    def test_waits_while_the_console_is_working_and_tries_again_at_the_next_look(
        self, harness: Harness
    ) -> None:
        harness.turn_on()
        harness.busy = True
        assert harness.manager.run().reason == "busy"
        assert harness.checks == 0
        harness.busy = False
        assert harness.manager.run().outcome == "updated"

    def test_waits_for_a_package_still_being_built_and_asks_again_at_the_next_look(
        self, harness: Harness
    ) -> None:
        harness.turn_on()
        harness.found = check(installable=None, published="3.4.1")
        assert harness.manager.run().reason == "on_the_way"
        harness.found = check(installable="3.4.1")
        assert harness.manager.run().outcome == "updated"

    def test_answers_a_window_once(self, harness: Harness) -> None:
        harness.turn_on()
        harness.found = check()
        assert harness.manager.run().reason == "up_to_date"
        assert harness.manager.run().reason == "done_today"
        assert harness.checks == 1
        harness.now = window_at("15T03:30")
        assert harness.manager.run().reason == "up_to_date"
        assert harness.checks == 2

    def test_does_not_update_twice_in_one_window(self, harness: Harness) -> None:
        harness.turn_on()
        harness.manager.run()
        assert harness.manager.run().reason == "done_today"
        assert len(harness.updater.started) == 1

    def test_never_updates_with_the_update_check_off(self, harness: Harness) -> None:
        harness.turn_on()
        harness.check_enabled = False
        run = harness.manager.run()
        assert run.reason == "check_off"
        assert harness.checks == 0

    def test_a_check_somebody_else_is_reading_is_tried_again(self, harness: Harness) -> None:
        harness.turn_on()
        harness.found = None
        assert harness.manager.run().reason == "checking"
        harness.found = check(installable="3.4.1")
        assert harness.manager.run().outcome == "updated"

    def test_a_refusal_a_person_must_read_ends_the_windows_try_and_is_audited(
        self, tmp_path: Path, config_path: Path
    ) -> None:
        refusal = SelfUpdateRefused(
            "preflight_failed",
            "Only 80 MB are free on /var; nothing was installed",
            "Free some space.",
            output="df says 80M",
        )
        harness = Harness(
            tmp_path, found=check(installable="3.4.1"), updater=FakeUpdater(start_refusal=refusal)
        )
        harness.turn_on()
        run = harness.manager.run()
        assert (run.outcome, run.reason, run.settled) == ("failed", "preflight_failed", True)
        assert "Free some space" in run.detail
        assert run.output == "df says 80M"
        assert harness.manager.run().reason == "done_today"

    def test_a_refusal_that_may_pass_is_tried_again_at_the_next_look(
        self, tmp_path: Path, config_path: Path
    ) -> None:
        refusal = SelfUpdateRefused(
            "refresh_failed",
            "The package lists could not be refreshed; nothing was installed",
            "Fix the repository.",
        )
        harness = Harness(
            tmp_path, found=check(installable="3.4.1"), updater=FakeUpdater(start_refusal=refusal)
        )
        harness.turn_on()
        run = harness.manager.run()
        assert (run.outcome, run.settled) == ("skipped", False)
        harness.updater._start_refusal = None
        assert harness.manager.run().outcome == "updated"

    def test_an_installation_that_fails_is_a_failure_with_the_tools_own_words(
        self, tmp_path: Path, config_path: Path
    ) -> None:
        harness = Harness(
            tmp_path, found=check(installable="3.4.1"), updater=FakeUpdater(ends="failed")
        )
        harness.turn_on()
        run = harness.manager.run()
        assert (run.outcome, run.reason) == ("failed", "install_failed")
        assert run.output == "ERROR: no matching distribution"

    def test_the_record_survives_a_new_process(self, harness: Harness) -> None:
        harness.turn_on()
        harness.manager.run()
        document = json.loads(harness.manager.state_path.read_text())
        assert document["last"]["outcome"] == "updated"
        assert document["settled_day"] == "2026-10-14"

    def test_status_says_what_is_set_and_what_it_did_without_reading_the_network(
        self, harness: Harness
    ) -> None:
        harness.turn_on()
        harness.manager.run()
        status = harness.manager.status()
        assert status["settings"]["enabled"] is True
        assert status["supported"] is True
        assert status["window_open"] is True
        assert status["last_run"]["outcome"] == "updated"
        assert status["next_window"] == "2026-10-14T03:30:00"
        assert harness.checks == 1  # the run's, not status's


def test_the_real_timer_and_service_templates_render_and_follow_the_calendars(
    tmp_path: Path,
) -> None:
    """The templates the manager names are the files that ship, and they keep a window across midnight whole."""
    from noust.managers.backup_scheduler import BackupScheduler

    scheduler = BackupScheduler()
    timer = scheduler._render(
        "auto-update-timer.j2",
        schedule="Sun *-*-* 23:00/10:00",
        more="Mon *-*-* 00:00/10:00",
        window="23:00-01:00",
    )
    service = scheduler._render("auto-update-service.j2", noust="/usr/bin/noust")

    assert auto_update.TIMER_DESCRIPTION in timer
    assert timer.count("OnCalendar=") == 2
    assert "OnCalendar=Mon *-*-* 00:00/10:00" in timer
    assert "Persistent=false" in timer
    assert (
        'ExecStart=/usr/bin/noust self-update run --scheduled --reason "Scheduled automatic update"'
        in service
    )
    assert "Type=oneshot" in service
