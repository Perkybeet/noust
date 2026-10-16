# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The checks the monitor runs on a clock of their own.

Checklist K-17 (a scheduled backup that did not run), K-18 and G-20 (cron jobs
that failed), K-22 (operating system updates waiting) and K-23 (API tokens about
to expire). Every watch is driven with a controlled clock, a real store for what
it remembers and fakes for what it looks at: nothing touches the machine.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.notification_state import NotificationState
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import Notification, State
from noust.core.runner import FakeRunner
from noust.core.store import BackupScheduleRecord, NoustStore
from noust.managers.cron_manager import CronManager, CronOutcome
from noust.managers.server.errors import ServerError
from noust.monitor.watch_backups import BackupWatch, allowed_days
from noust.monitor.watch_cron import CronWatch
from noust.monitor.watch_patches import PatchWatch, signature
from noust.monitor.watch_tokens import TokenWatch, stage_of
from noust.monitor.watches import Watch, WatchServices, run_due

DAY = 86_400.0
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc).timestamp()


class Settings:
    """A configuration with ``get``, answering what a test set and the default otherwise."""

    def __init__(self, **values: Any) -> None:
        self.values = {key.replace("__", "."): value for key, value in values.items()}

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Rig:
    """The services a watch is lent, and the notifications it published."""

    def __init__(self, tmp_path: Path, **settings: Any) -> None:
        NoustStore.reset_instance()
        self.store = NoustStore(tmp_path / "noust.db")
        self.clock = Clock()
        self.sent: list[Notification] = []
        self.context = NotificationContext(locale="en", server="web-1", public_url="")
        self.state = NotificationState(self.store, clock=self.clock)
        self.settings = Settings(**settings)
        self.services = WatchServices(
            publish=lambda build: self.sent.append(build(self.context)),
            state=self.state,
            runner=FakeRunner(),
            config=self.settings,  # type: ignore[arg-type]
            clock=self.clock,
        )

    def close(self) -> None:
        NoustStore.reset_instance()


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    instance = Rig(tmp_path)
    yield instance
    instance.close()


class TestTheMemory:
    def test_a_value_is_remembered_replaced_and_forgotten(self, rig: Rig) -> None:
        assert rig.state.get("cron", "backup") is None

        rig.state.put("cron", "backup", "a")
        rig.state.put("cron", "backup", "b")
        assert rig.state.get("cron", "backup") == "b"

        rig.state.delete("cron", "backup")
        assert rig.state.get("cron", "backup") is None

    def test_scopes_do_not_mix(self, rig: Rig) -> None:
        rig.state.put("cron", "x", "1")
        rig.state.put("backups", "x", "2")

        assert rig.state.items("cron") == {"x": "1"}
        assert rig.state.items("backups") == {"x": "2"}

    def test_what_is_no_longer_watched_is_dropped(self, rig: Rig) -> None:
        for item in ("a", "b", "c"):
            rig.state.put("cron", item, "x")

        rig.state.keep_only("cron", {"b"})

        assert rig.state.items("cron") == {"b": "x"}

    def test_structured_values_round_trip_and_junk_reads_as_nothing(self, rig: Rig) -> None:
        rig.state.put_json("patches", "announced", {"signature": "abc", "at": 5})
        rig.state.put("patches", "junk", "{not json")

        assert rig.state.get_json("patches", "announced") == {"signature": "abc", "at": 5}
        assert rig.state.get_json("patches", "junk") is None
        assert rig.state.get_json("patches", "missing") is None


class Counting(Watch):
    def __init__(self, name: str, interval: float, fail: bool = False) -> None:
        self.name = name
        self._interval = interval
        self.fail = fail
        self.runs = 0

    def interval_s(self, services: WatchServices) -> float:
        return self._interval

    def check(self, services: WatchServices) -> None:
        self.runs += 1
        if self.fail:
            raise ServerError("the package manager is locked")


class TestRunningTheWatches:
    def test_a_watch_runs_when_its_interval_has_passed(self, rig: Rig) -> None:
        watch = Counting("slow", 3600)

        assert run_due([watch], rig.services) == ["slow"]
        rig.clock.now += 3599
        assert run_due([watch], rig.services) == []
        rig.clock.now += 1
        assert run_due([watch], rig.services) == ["slow"]
        assert watch.runs == 2

    def test_it_remembers_across_a_restart_of_the_monitor(self, rig: Rig) -> None:
        run_due([Counting("slow", 3600)], rig.services)

        restarted = Counting("slow", 3600)
        assert run_due([restarted], rig.services) == []
        assert restarted.runs == 0

    def test_a_watch_set_to_zero_is_off(self, rig: Rig) -> None:
        watch = Counting("off", 0)

        assert run_due([watch], rig.services) == []
        assert watch.runs == 0

    def test_a_failing_watch_is_logged_and_waits_its_turn_like_any_other(
        self, rig: Rig, caplog: pytest.LogCaptureFixture
    ) -> None:
        broken = Counting("broken", 3600, fail=True)
        fine = Counting("fine", 60)

        ran = run_due([broken, fine], rig.services)

        assert ran == ["fine"] and "package manager is locked" in caplog.text
        rig.clock.now += 120
        run_due([broken, fine], rig.services)
        assert (broken.runs, fine.runs) == (1, 2)  # no retry every scan


class TestCronOutcomes:
    """CronManager.last_outcomes: two commands for every job."""

    @pytest.fixture
    def systemd_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        path = tmp_path / "systemd"
        path.mkdir()
        monkeypatch.setattr(CronManager, "SYSTEMD_DIR", path)
        for name, app in (("backup", "shop.example.com"), ("cleanup", "")):
            (path / f"noust-cron-{name}.timer").write_text("# Generated by Noust\n[Timer]\n")
            comment = f"# Application: {app}\n" if app else ""
            (path / f"noust-cron-{name}.service").write_text(
                f"# Generated by Noust\n{comment}[Service]\nExecStart=/usr/bin/true\n"
            )
        return path

    def test_every_job_is_answered_by_one_show(self, systemd_dir: Path) -> None:
        runner = FakeRunner()
        runner.script(
            ["systemctl", "list-unit-files"],
            stdout="noust-cron-backup.timer enabled enabled\nnoust-cron-cleanup.timer enabled enabled\n",
        )
        runner.script(
            ["systemctl", "show"],
            stdout=(
                "Id=noust-cron-backup.service\nInvocationID=aaa\nActiveState=inactive\n"
                "Result=exit-code\nExecMainStatus=2\n\n"
                "Id=noust-cron-cleanup.service\nInvocationID=\nActiveState=inactive\n"
                "Result=success\nExecMainStatus=0\n"
            ),
        )

        outcomes = CronManager(runner=runner).last_outcomes()

        assert [c[:2] for c in runner.calls] == [
            ("systemctl", "list-unit-files"),
            ("systemctl", "show"),
        ]
        backup, cleanup = outcomes
        assert (backup.name, backup.invocation, backup.result, backup.exit_code) == (
            "backup",
            "aaa",
            "exit-code",
            2,
        )
        assert backup.app_domain == "shop.example.com" and not backup.ok and not backup.running
        # A job that never ran has no invocation, and no result worth reading.
        assert (cleanup.invocation, cleanup.result, cleanup.exit_code) == ("", "", None)
        assert cleanup.ok

    def test_a_running_job_is_marked_so(self, systemd_dir: Path) -> None:
        runner = FakeRunner()
        runner.script(["systemctl", "list-unit-files"], stdout="noust-cron-backup.timer enabled\n")
        runner.script(
            ["systemctl", "show"],
            stdout="Id=noust-cron-backup.service\nInvocationID=bbb\nActiveState=activating\nResult=success\n",
        )

        (outcome,) = CronManager(runner=runner).last_outcomes()

        assert outcome.running

    def test_a_unit_somebody_wrote_by_hand_is_not_listed(self, systemd_dir: Path) -> None:
        (systemd_dir / "noust-cron-mine.timer").write_text("[Timer]\n")
        runner = FakeRunner()
        runner.script(
            ["systemctl", "list-unit-files"],
            stdout="noust-cron-mine.timer enabled\n",
        )

        assert CronManager(runner=runner).last_outcomes() == []

    def test_systemd_that_cannot_be_asked_says_nothing(self, systemd_dir: Path) -> None:
        runner = FakeRunner(default_exit_code=1)

        assert CronManager(runner=runner).last_outcomes() == []


class Cron:
    """A cron manager that answers what a test scripts."""

    def __init__(self) -> None:
        self.outcomes: list[CronOutcome] = []
        self.output = ["Traceback (most recent call last):", "KeyError: 'sku'"]

    def last_outcomes(self) -> list[CronOutcome]:
        return list(self.outcomes)

    def runs(self, name: str, *, limit: int = 10, lines: int = 20) -> list[dict[str, Any]]:
        return [{"started": "x", "exit_code": 1, "success": False, "lines": self.output}]


def outcome(
    invocation: str, result: str = "success", exit_code: int = 0, **extra: Any
) -> CronOutcome:
    return CronOutcome("shop-sync", invocation, result=result, exit_code=exit_code, **extra)


class TestCronWatch:
    @pytest.fixture
    def cron(self) -> Cron:
        return Cron()

    def test_the_first_look_remembers_and_says_nothing(self, rig: Rig, cron: Cron) -> None:
        cron.outcomes = [outcome("a", "exit-code", 1)]

        CronWatch(cron).check(rig.services)  # type: ignore[arg-type]

        assert rig.sent == []
        assert rig.state.get("cron", "shop-sync") == "a"

    def test_a_run_that_failed_is_announced_with_what_it_printed(
        self, rig: Rig, cron: Cron
    ) -> None:
        watch = CronWatch(cron)  # type: ignore[arg-type]
        cron.outcomes = [outcome("a")]
        watch.check(rig.services)

        cron.outcomes = [outcome("b", "exit-code", 1, app_domain="shop.example.com")]
        watch.check(rig.services)

        (note,) = rig.sent
        assert (note.kind, note.code, note.state) == ("cron_failed", "cron.failed", State.FAILED)
        assert note.subject == "shop-sync" and note.domain == "shop.example.com"
        assert note.excerpt is not None and "KeyError: 'sku'" in note.excerpt.lines
        assert {f.key: f.value for f in note.facts}["exit_status"] == "1"
        assert note.command is not None and note.command.value == "noust cron runs shop-sync"

    def test_a_success_is_a_heartbeat_with_its_own_switch(self, rig: Rig, cron: Cron) -> None:
        watch = CronWatch(cron)  # type: ignore[arg-type]
        cron.outcomes = [outcome("a")]
        watch.check(rig.services)

        cron.outcomes = [outcome("b")]
        watch.check(rig.services)

        (note,) = rig.sent
        assert (note.kind, note.state, note.excerpt) == ("cron_success", State.OK, None)

    def test_the_same_run_is_not_announced_twice(self, rig: Rig, cron: Cron) -> None:
        watch = CronWatch(cron)  # type: ignore[arg-type]
        cron.outcomes = [outcome("a")]
        watch.check(rig.services)
        cron.outcomes = [outcome("b", "exit-code", 1)]

        watch.check(rig.services)
        watch.check(rig.services)

        assert len(rig.sent) == 1

    def test_a_job_that_is_still_running_waits_until_it_ends(self, rig: Rig, cron: Cron) -> None:
        watch = CronWatch(cron)  # type: ignore[arg-type]
        cron.outcomes = [outcome("a")]
        watch.check(rig.services)

        cron.outcomes = [outcome("b", running=True)]
        watch.check(rig.services)
        assert rig.sent == []

        cron.outcomes = [outcome("b", "timeout", 1)]
        watch.check(rig.services)
        assert [n.code for n in rig.sent] == ["cron.failed"]

    def test_a_job_created_after_the_monitor_started_is_announced_the_first_time_it_runs(
        self, rig: Rig, cron: Cron
    ) -> None:
        watch = CronWatch(cron)  # type: ignore[arg-type]
        cron.outcomes = [outcome("")]  # exists, never ran
        watch.check(rig.services)

        cron.outcomes = [outcome("first", "exit-code", 127)]
        watch.check(rig.services)

        assert [n.code for n in rig.sent] == ["cron.failed"]

    def test_a_deleted_job_is_forgotten(self, rig: Rig, cron: Cron) -> None:
        watch = CronWatch(cron)  # type: ignore[arg-type]
        cron.outcomes = [outcome("a")]
        watch.check(rig.services)

        cron.outcomes = []
        watch.check(rig.services)

        assert rig.state.items("cron") == {}

    def test_a_journal_that_cannot_be_read_does_not_cost_the_message(
        self, rig: Rig, cron: Cron
    ) -> None:
        cron.runs = lambda *a, **k: [{"lines": []}]  # type: ignore[method-assign]
        watch = CronWatch(cron)  # type: ignore[arg-type]
        cron.outcomes = [outcome("a")]
        watch.check(rig.services)

        cron.outcomes = [outcome("b", "exit-code", 1)]
        watch.check(rig.services)

        assert len(rig.sent) == 1 and rig.sent[0].excerpt is None


class TestCronTimers:
    """G-20: a job whose timer is not running will not run, and nothing fails to say so."""

    @pytest.fixture
    def cron(self) -> Cron:
        return Cron()

    def test_a_timer_that_stopped_is_told_once_and_again_when_it_runs(
        self, rig: Rig, cron: Cron
    ) -> None:
        watch = CronWatch(cron)  # type: ignore[arg-type]
        cron.outcomes = [outcome("a", timer="active")]
        watch.check(rig.services)
        assert rig.sent == []

        cron.outcomes = [outcome("a", timer="failed", app_domain="shop.example.com")]
        watch.check(rig.services)
        watch.check(rig.services)
        (note,) = rig.sent
        assert (note.kind, note.code, note.state) == (
            "cron_missed",
            "cron.timer_stopped",
            State.WARNING,
        )
        assert note.subject == "shop-sync" and note.domain == "shop.example.com"
        assert {f.key: f.value for f in note.facts}["timer"] == "failed"
        assert note.command is not None and note.command.value == "noust cron enable shop-sync"

        cron.outcomes = [outcome("a", timer="active")]
        watch.check(rig.services)
        assert [n.code for n in rig.sent] == ["cron.timer_stopped", "cron.timer_running"]
        assert rig.sent[1].state is State.OK

    def test_a_timer_that_is_down_the_first_time_it_is_seen_is_told(
        self, rig: Rig, cron: Cron
    ) -> None:
        cron.outcomes = [outcome("a", timer="inactive")]

        CronWatch(cron).check(rig.services)  # type: ignore[arg-type]

        assert [n.code for n in rig.sent] == ["cron.timer_stopped"]

    def test_a_job_somebody_disabled_is_left_alone(self, rig: Rig, cron: Cron) -> None:
        cron.outcomes = [outcome("a", timer="inactive", timer_enabled=False)]

        CronWatch(cron).check(rig.services)  # type: ignore[arg-type]

        assert rig.sent == []

    def test_a_timer_that_was_not_asked_is_not_a_fault(self, rig: Rig, cron: Cron) -> None:
        cron.outcomes = [outcome("a")]

        CronWatch(cron).check(rig.services)  # type: ignore[arg-type]

        assert rig.sent == []

    def test_a_deleted_job_is_forgotten(self, rig: Rig, cron: Cron) -> None:
        watch = CronWatch(cron)  # type: ignore[arg-type]
        cron.outcomes = [outcome("a", timer="inactive")]
        watch.check(rig.services)

        cron.outcomes = []
        watch.check(rig.services)

        assert rig.state.items("cron-timer") == {}


class TestCronTimerStates:
    """CronManager.last_outcomes reads the timers in the same two commands."""

    @pytest.fixture
    def systemd_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        path = tmp_path / "systemd"
        path.mkdir()
        monkeypatch.setattr(CronManager, "SYSTEMD_DIR", path)
        for name in ("backup", "cleanup"):
            (path / f"noust-cron-{name}.timer").write_text("# Generated by Noust\n[Timer]\n")
            (path / f"noust-cron-{name}.service").write_text(
                "# Generated by Noust\n[Service]\nExecStart=/usr/bin/true\n"
            )
        return path

    def test_the_timers_are_asked_in_the_same_show(self, systemd_dir: Path) -> None:
        runner = FakeRunner()
        runner.script(
            ["systemctl", "list-unit-files"],
            stdout=(
                "noust-cron-backup.timer enabled enabled\n"
                "noust-cron-cleanup.timer disabled enabled\n"
            ),
        )
        runner.script(
            ["systemctl", "show"],
            stdout=(
                "Id=noust-cron-backup.service\nInvocationID=a\nActiveState=inactive\nResult=success\n\n"
                "Id=noust-cron-backup.timer\nActiveState=failed\n\n"
                "Id=noust-cron-cleanup.service\nInvocationID=\nActiveState=inactive\n\n"
                "Id=noust-cron-cleanup.timer\nActiveState=inactive\n"
            ),
        )

        backup, cleanup = CronManager(runner=runner).last_outcomes()

        assert [c[:2] for c in runner.calls] == [
            ("systemctl", "list-unit-files"),
            ("systemctl", "show"),
        ]
        assert (backup.timer, backup.timer_enabled) == ("failed", True)
        assert (cleanup.timer, cleanup.timer_enabled) == ("inactive", False)


def schedule(domain: str, spec: str = "daily", *, created: datetime | None = None) -> Any:
    return BackupScheduleRecord(
        app_domain=domain,
        schedule=spec,
        created_at=(created or datetime(2026, 9, 1, tzinfo=timezone.utc)).isoformat(),
    )


def backup(domain: str, taken: datetime) -> Any:
    return SimpleNamespace(domain=domain, created_at=taken.isoformat())


class TestBackupWatch:
    def put(self, rig: Rig, *records: Any) -> None:
        for record in records:
            rig.store.set_backup_schedule(record) if hasattr(
                rig.store, "set_backup_schedule"
            ) else None

    @pytest.fixture
    def schedules(self, rig: Rig, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
        found: list[Any] = []
        monkeypatch.setattr(
            "noust.monitor.watch_backups.get_store",
            lambda: SimpleNamespace(list_backup_schedules=lambda: found),
        )
        return found

    def at(self, days_ago: float) -> datetime:
        return datetime.fromtimestamp(NOW - days_ago * DAY, tz=timezone.utc)

    def test_a_recent_backup_is_nothing_to_say(self, rig: Rig, schedules: list[Any]) -> None:
        schedules.append(schedule("shop.example.com"))

        BackupWatch(lambda: [backup("shop.example.com", self.at(1))]).check(rig.services)

        assert rig.sent == []

    def test_no_backup_for_days_is_announced_with_the_last_one(
        self, rig: Rig, schedules: list[Any]
    ) -> None:
        schedules.append(schedule("shop.example.com"))

        BackupWatch(lambda: [backup("shop.example.com", self.at(5))]).check(rig.services)

        (note,) = rig.sent
        assert (note.kind, note.code, note.state) == (
            "backup_missing",
            "backup.missing",
            State.WARNING,
        )
        assert note.domain == "shop.example.com"
        assert "last 5 days" in note.summary
        assert (
            note.command is not None
            and note.command.value == "noust backup create shop.example.com"
        )
        assert {f.key: f.value for f in note.facts}["schedule"] == "daily"

    def test_the_newest_backup_counts_not_the_first_listed(
        self, rig: Rig, schedules: list[Any]
    ) -> None:
        schedules.append(schedule("shop.example.com"))
        listed = [backup("shop.example.com", self.at(9)), backup("shop.example.com", self.at(1))]

        BackupWatch(lambda: listed).check(rig.services)

        assert rig.sent == []

    def test_a_schedule_that_never_produced_a_backup_is_announced_once_it_is_old_enough(
        self, rig: Rig, schedules: list[Any]
    ) -> None:
        schedules.append(schedule("new.example.com", created=self.at(1)))
        schedules.append(schedule("old.example.com", created=self.at(30)))

        BackupWatch(lambda: []).check(rig.services)

        (note,) = rig.sent
        assert (note.code, note.domain) == ("backup.never", "old.example.com")

    def test_it_repeats_only_after_the_renotify_hours(self, rig: Rig, schedules: list[Any]) -> None:
        schedules.append(schedule("shop.example.com"))
        watch = BackupWatch(lambda: [backup("shop.example.com", self.at(5))])
        watch.check(rig.services)

        rig.clock.now += 23 * 3600
        watch.check(rig.services)
        assert len(rig.sent) == 1

        rig.clock.now += 2 * 3600
        watch.check(rig.services)
        assert len(rig.sent) == 2

    def test_a_backup_that_came_back_clears_the_memory(
        self, rig: Rig, schedules: list[Any]
    ) -> None:
        schedules.append(schedule("shop.example.com"))
        BackupWatch(lambda: [backup("shop.example.com", self.at(5))]).check(rig.services)
        assert rig.state.get("backups", "shop.example.com") is not None

        BackupWatch(lambda: [backup("shop.example.com", self.at(0.1))]).check(rig.services)

        assert rig.state.get("backups", "shop.example.com") is None

    def test_a_weekly_schedule_is_allowed_its_period(self) -> None:
        assert allowed_days("daily", 2) == 2
        assert allowed_days("weekly", 2) == 8
        assert allowed_days("monthly", 2) == 32
        assert allowed_days("Mon *-*-* 02:00:00", 2) == 2
        assert allowed_days("weekly", 30) == 30

    def test_zero_days_turns_the_check_off(self, rig: Rig) -> None:
        rig.settings.values["monitor.backup_missing_days"] = 0

        assert BackupWatch().interval_s(rig.services) == 0

    def test_the_interval_is_configurable(self, rig: Rig) -> None:
        rig.settings.values["monitor.backup_check_hours"] = 2

        assert BackupWatch().interval_s(rig.services) == 7200


def update(name: str, security: bool = False, candidate: str = "1.1") -> Any:
    return SimpleNamespace(name=name, candidate=candidate, security=security, kind="package")


def pending(*packages: Any) -> Any:
    return SimpleNamespace(packages=list(packages))


class TestPatchWatch:
    def watch(self, source: Callable[[WatchServices], Any]) -> PatchWatch:
        return PatchWatch(source)

    def test_security_updates_are_announced(self, rig: Rig) -> None:
        found = pending(update("openssl", True), update("curl"))

        self.watch(lambda _s: (found, True)).check(rig.services)

        (note,) = rig.sent
        assert (note.kind, note.code, note.state) == (
            "os_patches",
            "patches.security",
            State.WARNING,
        )
        assert "1 of the 2 updates" in note.summary
        facts = {f.key: f.value for f in note.facts}
        assert facts["packages"] == "2" and facts["security"] == "1" and "reboot" in facts
        assert facts["detail"].startswith("openssl")  # security ones first

    def test_the_same_set_is_not_announced_again_until_a_week_has_passed(self, rig: Rig) -> None:
        found = pending(update("openssl", True))
        watch = self.watch(lambda _s: (found, None))
        watch.check(rig.services)

        rig.clock.now += 3 * DAY
        watch.check(rig.services)
        assert len(rig.sent) == 1

        rig.clock.now += 5 * DAY
        watch.check(rig.services)
        assert len(rig.sent) == 2

    def test_a_new_security_update_is_news_at_once(self, rig: Rig) -> None:
        packages = [update("openssl", True)]
        watch = self.watch(lambda _s: (pending(*packages), None))
        watch.check(rig.services)

        packages.append(update("libc6", True))
        watch.check(rig.services)

        assert len(rig.sent) == 2

    def test_a_newer_version_of_a_package_already_pending_is_news(self, rig: Rig) -> None:
        packages = [update("openssl", True, "3.0.1")]
        watch = self.watch(lambda _s: (pending(*packages), None))
        watch.check(rig.services)

        packages[0] = update("openssl", True, "3.0.2")
        watch.check(rig.services)

        assert len(rig.sent) == 2

    def test_ordinary_updates_are_information_and_rare(self, rig: Rig) -> None:
        packages = [update("curl")]
        watch = self.watch(lambda _s: (pending(*packages), None))
        watch.check(rig.services)
        (note,) = rig.sent
        assert (note.code, note.state) == ("patches.available", State.INFO)

        packages.append(update("vim"))
        watch.check(rig.services)
        assert len(rig.sent) == 1  # the set changed, but not enough time passed

        rig.clock.now += 8 * DAY
        watch.check(rig.services)
        assert len(rig.sent) == 2

    def test_nothing_pending_says_nothing_and_forgets(self, rig: Rig) -> None:
        found = [update("openssl", True)]
        watch = self.watch(lambda _s: (pending(*found), None))
        watch.check(rig.services)

        found.clear()
        watch.check(rig.services)

        assert len(rig.sent) == 1
        assert rig.state.get("patches", "announced") is None

    def test_a_check_that_cannot_be_made_is_announced_once_per_stretch(self, rig: Rig) -> None:
        def broken(_services: WatchServices) -> Any:
            raise ServerError("Could not get lock /var/lib/dpkg/lock-frontend", "held by apt-get")

        watch = self.watch(broken)
        watch.check(rig.services)
        watch.check(rig.services)

        (note,) = rig.sent
        assert (note.code, note.state) == ("patches.check_failed", State.WARNING)
        assert note.excerpt is not None and "dpkg/lock-frontend" in " ".join(note.excerpt.lines)

        # It recovers, then fails again: a new stretch is news.
        watch = self.watch(lambda _s: (pending(), None))
        watch.check(rig.services)
        self.watch(broken).check(rig.services)
        assert len(rig.sent) == 2

    def test_a_machine_where_updates_are_not_managed_is_silent(self, rig: Rig) -> None:
        self.watch(lambda _s: None).check(rig.services)  # type: ignore[arg-type,return-value]

        assert rig.sent == []

    def test_the_signature_ignores_order_and_sees_a_version(self) -> None:
        a = [update("a"), update("b")]

        assert signature(a) == signature(list(reversed(a)))
        assert signature(a) != signature([update("a"), update("b", candidate="2")])
        assert signature(a) != signature([update("a"), update("b", security=True)])

    def test_the_interval_is_configurable_and_zero_is_off(self, rig: Rig) -> None:
        assert PatchWatch().interval_s(rig.services) == 24 * 3600
        rig.settings.values["monitor.os_patches_check_hours"] = 0
        assert PatchWatch().interval_s(rig.services) == 0


def token(
    key: int, expires: datetime | None, *, name: str = "ci-deploy", revoked: bool = False
) -> dict[str, Any]:
    return {
        "id": key,
        "name": name,
        "expires_at": expires.timestamp() if expires else None,
        "revoked_at": NOW if revoked else None,
        "created_by": "alice",
    }


class TestTokenWatch:
    def in_days(self, days: float) -> datetime:
        return datetime.fromtimestamp(NOW + days * DAY, tz=timezone.utc)

    def test_the_stages(self) -> None:
        assert stage_of(30, 7) is None
        assert stage_of(7, 7) == "warn"
        assert stage_of(1, 7) == "warn"
        assert stage_of(0, 7) == "today"
        assert stage_of(-1, 7) == "expired"
        assert stage_of(-8, 7) is None

    def test_a_token_in_the_window_is_announced_once(self, rig: Rig) -> None:
        records = [token(1, self.in_days(5))]
        watch = TokenWatch(lambda: records)

        watch.check(rig.services)
        watch.check(rig.services)

        (note,) = rig.sent
        assert (note.kind, note.code, note.state) == (
            "token_expiring",
            "token.expiring",
            State.WARNING,
        )
        assert note.subject == "ci-deploy" and "5 days" in note.summary
        assert {f.key: f.value for f in note.facts}["owner"] == "alice"

    def test_each_stage_is_announced_as_it_comes(self, rig: Rig) -> None:
        records = [token(1, self.in_days(5))]
        watch = TokenWatch(lambda: records)
        watch.check(rig.services)

        rig.clock.now += 5 * DAY
        records[0] = token(1, datetime.fromtimestamp(rig.clock.now + 0.2 * DAY, tz=timezone.utc))
        watch.check(rig.services)
        rig.clock.now += 2 * DAY
        records[0] = token(1, datetime.fromtimestamp(rig.clock.now - 1.5 * DAY, tz=timezone.utc))
        watch.check(rig.services)

        assert [n.code for n in rig.sent] == ["token.expiring", "token.today", "token.expired"]
        assert rig.sent[-1].state is State.FAILED

    def test_far_off_revoked_and_forever_tokens_are_not_news(self, rig: Rig) -> None:
        records = [
            token(1, self.in_days(60)),
            token(2, self.in_days(3), revoked=True),
            token(3, None),
            token(4, self.in_days(-30)),
        ]

        TokenWatch(lambda: records).check(rig.services)

        assert rig.sent == []

    def test_a_token_that_is_gone_is_forgotten(self, rig: Rig) -> None:
        records = [token(1, self.in_days(3))]
        watch = TokenWatch(lambda: records)
        watch.check(rig.services)

        records.clear()
        watch.check(rig.services)

        assert rig.state.items("tokens") == {}

    def test_zero_days_turns_it_off(self, rig: Rig) -> None:
        rig.settings.values["monitor.token_warning_days"] = 0

        assert TokenWatch().interval_s(rig.services) == 0

    def test_a_window_set_by_the_operator_is_honoured(self, rig: Rig) -> None:
        rig.settings.values["monitor.token_warning_days"] = 30

        TokenWatch(lambda: [token(1, self.in_days(20))]).check(rig.services)

        assert len(rig.sent) == 1


def test_a_week_of_the_clock_is_what_the_tests_assume() -> None:
    assert timedelta(days=1).total_seconds() == DAY
