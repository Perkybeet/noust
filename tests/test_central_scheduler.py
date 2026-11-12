# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The central runs its schedules itself where there is no systemd to keep their timers.

On a server a schedule is a timer that systemd runs; every schedule of Noust (the copies of an application,
of its volumes, the retention sweep) is a row in the store and a timer written from it. The central of a NAS
has no timers. There the same rows are read by a scheduler inside the central's process, which runs the same
commands the timers would (``noust backup volumes run``, ``noust backup run-schedule``): one list, which is the
rows, and two engines that realise it, systemd's timers and this. What is held here is that the tasks are the
rows and nothing else, that what is due runs once and what was missed while the central was off runs when it
comes back and is *said*, that a schedule nobody asked for never runs, that a failure is kept with its words
and does not stop the others, and that a server's backup scheduler writes its timers as it always did while a
container host's only records the row.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from noust.central import scheduler as sched
from noust.central.scheduler import Scheduler, Task
from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.store import BackupScheduleRecord, NoustStore
from noust.managers.volume_backup_records import VolumePolicy, VolumeRecords

DOMAIN = "shop.example.com"
APP = "shop-example-com"

#: A Thursday.
NOON = datetime(2026, 11, 12, 12, 0, 0)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def fresh_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    monkeypatch.delenv("NOUST_DEPLOY_HERE", raising=False)
    Config.reset_instance()
    yield
    Config.reset_instance()


def volume_policy(
    store: NoustStore, *, schedule: str = "*-*-* 02:00:00", enabled: bool = True
) -> None:
    VolumeRecords(store).save_policy(
        VolumePolicy(app_domain=DOMAIN, schedule=schedule, enabled=enabled)
    )


def app_schedule(store: NoustStore, *, schedule: str = "*-*-* 03:30:00") -> None:
    store.save_backup_schedule(BackupScheduleRecord(app_domain=DOMAIN, schedule=schedule))


def make(
    tmp_path: Path, store: NoustStore, *, now: datetime = NOON, runner: FakeRunner | None = None
) -> tuple[Scheduler, FakeRunner, list[str], list[datetime]]:
    said: list[str] = []
    clock = [now]
    fake = runner or FakeRunner()
    scheduler = Scheduler(
        runner=fake,
        state_path=tmp_path / "scheduler.json",
        say=said.append,
        clock=lambda: clock[0],
        store=store,
    )
    return scheduler, fake, said, clock


def noust_calls(runner: FakeRunner, *, sweep: bool = False) -> list[tuple[str, ...]]:
    """The commands the scheduler ran, without the retention sweep every policy brings along."""
    calls = [call[3:] for call in runner.calls if call[1:3] == ("-m", "noust")]
    return calls if sweep else [call for call in calls if call[:2] != ("backup", "sweep")]


class TestWhatIsScheduled:
    """The tasks are the rows of the store: one list, whichever engine runs it."""

    def test_nothing_is_scheduled_until_somebody_schedules_something(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        scheduler, *_ = make(tmp_path, store)

        assert scheduler.tasks() == []

    def test_a_volume_policy_is_the_command_its_timer_would_run(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        volume_policy(store)
        scheduler, *_ = make(tmp_path, store)

        volumes = next(task for task in scheduler.tasks() if task.kind == "volume_backup")

        assert volumes == Task(
            key=f"volume_backup:{DOMAIN}",
            kind="volume_backup",
            subject=DOMAIN,
            unit=f"noust-backup-vol-{APP}",
            calendar="*-*-* 02:00:00",
            argv=("backup", "volumes", "run", DOMAIN, "--reason", "Scheduled volume backup policy"),
        )

    def test_an_application_s_schedule_is_run_schedule_and_the_sweep_comes_with_the_first(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        app_schedule(store)
        scheduler, *_ = make(tmp_path, store)

        tasks = {task.kind: task for task in scheduler.tasks()}

        assert tasks["backup"].argv == ("backup", "run-schedule", DOMAIN)
        assert tasks["backup"].unit == f"noust-backup-{APP}"
        assert tasks["sweep"].argv == ("backup", "sweep", "--reason", "Scheduled retention sweep")
        assert tasks["sweep"].calendar == "*-*-* 04:30:00"

    def test_a_policy_that_is_switched_off_does_not_run(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        volume_policy(store, enabled=False)
        scheduler, *_ = make(tmp_path, store)

        assert scheduler.tasks() == []

    def test_the_names_noust_gives_a_schedule_are_read_as_noust_means_them(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        volume_policy(store, schedule="daily")
        scheduler, *_ = make(tmp_path, store)

        status = next(item for item in scheduler.status() if item.task.kind == "volume_backup")

        assert status.next_run == datetime(2026, 11, 13, 2, 0, 0)

    def test_another_lane_s_schedules_join_the_same_list(
        self, tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(
            sched.SOURCES,
            "images",
            lambda store: [
                Task(
                    key="images:check",
                    kind="image_check",
                    subject="",
                    unit="noust-image-check",
                    calendar="*-*-* 05:00:00",
                    argv=("docker", "images", "check"),
                )
            ],
        )
        scheduler, *_ = make(tmp_path, store)

        assert [task.kind for task in scheduler.tasks()] == ["image_check"]


class TestRunningWhatIsDue:
    def test_it_runs_the_command_the_timer_would_and_remembers_when(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        volume_policy(store)
        scheduler, runner, said, clock = make(tmp_path, store, now=datetime(2026, 11, 12, 1, 0, 0))
        scheduler.tick()
        assert noust_calls(runner) == []

        clock[0] = datetime(2026, 11, 12, 2, 0, 5)
        scheduler.tick()

        assert noust_calls(runner) == [
            ("backup", "volumes", "run", DOMAIN, "--reason", "Scheduled volume backup policy")
        ]
        recorded = json.loads((tmp_path / "scheduler.json").read_text())["tasks"]
        assert recorded[f"volume_backup:{DOMAIN}"]["last_run"] == "2026-11-12T02:00:05"
        assert recorded[f"volume_backup:{DOMAIN}"]["last_status"] == "ok"

    def test_it_runs_once_however_many_ticks_see_it_due(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        volume_policy(store)
        scheduler, runner, _, clock = make(tmp_path, store, now=datetime(2026, 11, 12, 1, 0, 0))
        scheduler.tick()
        clock[0] = datetime(2026, 11, 12, 2, 0, 5)

        for _ in range(3):
            scheduler.tick()
            clock[0] += timedelta(seconds=30)

        assert len(noust_calls(runner)) == 1

    def test_it_runs_the_program_as_this_python_so_it_is_the_noust_of_the_central(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        import sys

        volume_policy(store)
        scheduler, runner, _, clock = make(tmp_path, store, now=datetime(2026, 11, 12, 1, 0, 0))
        scheduler.tick()
        clock[0] = datetime(2026, 11, 12, 2, 0, 1)

        scheduler.tick()

        assert runner.calls[0][:3] == (sys.executable, "-m", "noust")

    def test_a_schedule_created_today_does_not_owe_the_days_before_it_existed(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        volume_policy(store, schedule="*-*-* 02:00:00")
        scheduler, runner, said, _ = make(tmp_path, store, now=NOON)

        scheduler.tick()

        assert noust_calls(runner) == [] and said == []

    def test_a_failure_is_kept_with_its_words_and_does_not_stop_the_others(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        volume_policy(store)
        app_schedule(store, schedule="*-*-* 02:00:00")
        runner = FakeRunner()
        runner.script(
            [sched.PROGRAM[0], "-m", "noust", "backup", "run-schedule"],
            stderr="Backup failed: no space left on device\n",
            exit_code=1,
        )
        scheduler, runner, said, clock = make(
            tmp_path, store, now=datetime(2026, 11, 12, 1, 0, 0), runner=runner
        )
        scheduler.tick()
        clock[0] = datetime(2026, 11, 12, 2, 0, 5)

        scheduler.tick()

        assert (
            "backup",
            "volumes",
            "run",
            DOMAIN,
            "--reason",
            "Scheduled volume backup policy",
        ) in noust_calls(runner)
        statuses = {status.task.kind: status for status in scheduler.status()}
        assert statuses["backup"].last_status == "failed"
        assert "no space left on device" in statuses["backup"].last_detail
        assert statuses["volume_backup"].last_status == "ok"
        assert any("no space left on device" in line for line in said)

    def test_a_task_whose_calendar_is_not_understood_is_said_and_never_run_at_a_guess(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        volume_policy(store, schedule="someday")
        scheduler, runner, said, clock = make(tmp_path, store, now=datetime(2026, 11, 12, 1, 0, 0))

        scheduler.tick()
        clock[0] += timedelta(days=2)
        scheduler.tick()

        assert noust_calls(runner) == []
        status = next(item for item in scheduler.status() if item.task.kind == "volume_backup")
        assert status.next_run is None and "someday" in (status.problem or "")
        assert any("someday" in line for line in said)


class TestWhatWasMissed:
    def test_what_came_due_while_the_central_was_off_runs_when_it_comes_back_and_is_said(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        volume_policy(store)
        before, runner, _, clock = make(tmp_path, store, now=datetime(2026, 11, 9, 12, 0, 0))
        before.tick()
        clock[0] = datetime(2026, 11, 10, 2, 0, 5)
        before.tick()
        assert len(noust_calls(runner)) == 1

        # The central stops on the 10th and is started again on the 12th at noon: the runs of
        # the 11th and the 12th at two in the morning were missed.
        after, runner_after, said, _ = make(tmp_path, store, now=NOON)
        after.tick()

        assert len(noust_calls(runner_after)) == 1
        (line,) = [
            line for line in said if "while the central was not running" in line and DOMAIN in line
        ]
        assert "2 runs" in line and DOMAIN in line and "2026-11-12 02:00" in line
        recorded = json.loads((tmp_path / "scheduler.json").read_text())["tasks"]
        assert recorded[f"volume_backup:{DOMAIN}"]["missed"] == 1

    def test_a_run_that_is_only_a_little_late_is_not_called_a_missed_one(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        volume_policy(store)
        scheduler, runner, said, clock = make(tmp_path, store, now=datetime(2026, 11, 12, 1, 0, 0))
        scheduler.tick()
        clock[0] = datetime(2026, 11, 12, 2, 0, 40)

        scheduler.tick()

        assert len(noust_calls(runner)) == 1
        assert not any("while the central was not running" in line for line in said)

    def test_a_month_off_is_one_run_and_not_thirty(self, tmp_path: Path, store: NoustStore) -> None:
        volume_policy(store)
        before, _, _, clock = make(tmp_path, store, now=datetime(2026, 10, 1, 12, 0, 0))
        before.tick()
        clock[0] = datetime(2026, 10, 2, 2, 0, 5)
        before.tick()

        after, runner, said, _ = make(tmp_path, store, now=datetime(2026, 11, 12, 12, 0, 0))
        after.tick()

        assert len(noust_calls(runner)) == 1
        assert any("runs" in line and "while the central was not running" in line for line in said)


class TestTheState:
    def test_what_it_remembers_survives_a_restart_and_a_broken_file_is_started_over(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        (tmp_path / "scheduler.json").write_text("{ not json", encoding="utf-8")
        volume_policy(store)
        scheduler, runner, said, _ = make(tmp_path, store)

        scheduler.tick()

        assert noust_calls(runner) == []
        assert any("scheduler.json" in line for line in said)
        assert json.loads((tmp_path / "scheduler.json").read_text())["version"] == 1

    def test_the_file_is_private_because_it_names_what_is_backed_up(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        volume_policy(store)
        scheduler, *_ = make(tmp_path, store)

        scheduler.tick()

        assert (tmp_path / "scheduler.json").stat().st_mode & 0o777 == 0o600

    def test_a_task_that_is_gone_is_forgotten_so_the_file_does_not_grow_forever(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        volume_policy(store)
        scheduler, _, _, _ = make(tmp_path, store)
        scheduler.tick()
        VolumeRecords(store).delete_policy(DOMAIN)

        scheduler.tick()

        assert json.loads((tmp_path / "scheduler.json").read_text())["tasks"] == {}


class TestTheThread:
    def test_it_ticks_when_it_starts_and_stops_when_asked(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        volume_policy(store)
        scheduler, *_ = make(tmp_path, store)
        ticks: list[int] = []
        scheduler.tick = lambda: ticks.append(1)  # type: ignore[method-assign]

        thread = sched.SchedulerThread(scheduler, interval=0.01)
        thread.start()
        thread.stop()

        assert ticks, "the first look is at once: that is how what was missed is found"
        assert not thread.is_alive()

    def test_a_tick_that_fails_is_logged_and_the_next_one_still_comes(
        self, tmp_path: Path, store: NoustStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        scheduler, *_ = make(tmp_path, store)
        calls: list[Any] = []

        def tick() -> None:
            calls.append(1)
            if len(calls) == 1:
                raise OSError("the disk went away")

        scheduler.tick = tick  # type: ignore[method-assign]
        thread = sched.SchedulerThread(scheduler, interval=0.01)

        thread.start()
        while len(calls) < 3:
            pass
        thread.stop()

        assert len(calls) >= 3
        assert "the disk went away" in caplog.text


class TestTheCentral:
    def test_a_machine_with_systemd_timers_has_no_scheduler_of_the_central_s(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        assert sched.start_if_needed(lambda line: None) is None

    def test_a_container_host_starts_it_and_it_can_be_stopped(
        self, tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
        monkeypatch.setenv("NOUST_DEPLOY_HERE", "containers")
        Config.reset_instance()
        monkeypatch.setattr(
            sched, "default_scheduler", lambda: Scheduler(state_path=tmp_path / "s.json")
        )
        looked: list[int] = []
        monkeypatch.setattr(Scheduler, "tick", lambda self: looked.append(1))

        thread = sched.start_if_needed(lambda line: None)

        assert thread is not None and thread.is_alive()
        thread.stop()
        assert not thread.is_alive() and looked

    def test_the_command_says_what_is_scheduled_and_how_it_went(
        self, tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from click.testing import CliRunner

        from noust.cli.app import cli as root_cli

        volume_policy(store)
        monkeypatch.setattr(
            sched,
            "default_scheduler",
            lambda: Scheduler(
                runner=FakeRunner(), state_path=tmp_path / "s.json", clock=lambda: NOON, store=store
            ),
        )

        text = CliRunner().invoke(root_cli, ["central", "schedule", "status"])
        data = CliRunner().invoke(root_cli, ["central", "schedule", "status", "--json"])

        assert text.exit_code == 0, text.output
        assert f"volume backup of {DOMAIN}" in text.output and "2026-11-13 02:00:00" in text.output
        rows = json.loads(data.output)
        assert {row["kind"] for row in rows} == {"volume_backup", "sweep"}
        assert next(row for row in rows if row["kind"] == "volume_backup")["next_run"] == (
            "2026-11-13 02:00:00"
        )

    def test_nothing_scheduled_is_said(
        self, tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from click.testing import CliRunner

        from noust.cli.app import cli as root_cli

        monkeypatch.setattr(
            sched,
            "default_scheduler",
            lambda: Scheduler(state_path=tmp_path / "s.json", clock=lambda: NOON, store=store),
        )

        result = CliRunner().invoke(root_cli, ["central", "schedule", "status"])

        assert "Nothing is scheduled" in result.output

    def test_the_console_reads_the_same_rows(
        self, tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from noust.web.api import central as central_api
        from noust.web.api.deps import install_error_handlers

        volume_policy(store)
        monkeypatch.setattr(
            sched,
            "default_scheduler",
            lambda: Scheduler(
                runner=FakeRunner(), state_path=tmp_path / "s.json", clock=lambda: NOON, store=store
            ),
        )
        app = FastAPI()
        install_error_handlers(app)
        app.include_router(central_api.router, prefix="/api/central")

        body = TestClient(app).get("/api/central/schedule").json()

        assert {row["kind"] for row in body} == {"volume_backup", "sweep"}
        row = next(row for row in body if row["kind"] == "volume_backup")
        assert (row["subject"], row["next_run"], row["last_status"]) == (
            DOMAIN,
            "2026-11-13 02:00:00",
            None,
        )

    def test_it_is_a_read_of_the_server_for_the_instance_not_a_team_s(self) -> None:
        from noust.web.permissions import Permission
        from noust.web.permissions.routes_central import ROUTES
        from noust.web.permissions.scopes import INSTANCE, rule_for

        assert ROUTES[("GET", "/api/central/schedule")] == Permission.SERVER_READ
        rule = rule_for("GET", "/api/central/schedule")
        assert rule is not None and rule.scoping == INSTANCE
