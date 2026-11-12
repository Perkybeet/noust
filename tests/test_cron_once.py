# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tasks that run once and retire themselves (item 164).

A one-time task is the same pair of units as any task, with a timer that names one date and a
service that, when its command has ended, tells Noust to record how it ended and remove the pair.
What is pinned: the units that are written (and that a recurring task is written as it was), a
date that has passed is refused before anything is written, the record keeps the result and the
output the units no longer hold, a task the server ran late says so, a task that is not a one-time
one is never settled, and the hook that settles can arrive twice.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from noust.core.config import Config
from noust.core.cron_once_history import CronOnceHistory
from noust.core.exceptions import ServiceError, ValidationError
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.managers.cron_manager import CronJob, CronManager

CET = "CET-1CEST,M3.5.0,M10.5.0/3"

#: What ``systemctl show`` answers about the service while its stop hook runs.
SHOW_SUCCESS = "InvocationID=inv-1\nActiveState=deactivating\nResult=success\nExecMainStatus=0\n"
SHOW_FAILED = "InvocationID=inv-1\nActiveState=deactivating\nResult=exit-code\nExecMainStatus=2\n"


@pytest.fixture(autouse=True)
def madrid(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TZ", CET)
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def systemd_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "systemd"
    path.mkdir()
    monkeypatch.setattr(CronManager, "SYSTEMD_DIR", path)
    return path


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
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
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def noust_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "noust.managers.cron_manager.find_noust_executable", lambda: "/usr/bin/noust"
    )


def ahead(**delta: float) -> datetime:
    """A moment in the server's zone, ahead of the clock by a whole number of minutes."""
    moment = datetime.now().astimezone() + timedelta(**delta)
    return moment.replace(second=0, microsecond=0)


def once(at: datetime, **overrides: object) -> CronJob:
    fields: dict = {
        "name": "reindex",
        "command": "/usr/bin/php artisan search:reindex",
        "schedule": "daily",
        "user": "www-data",
        "working_directory": "/srv/shop",
        "once_at": at.strftime("%Y-%m-%d %H:%M"),
    }
    fields.update(overrides)
    return CronJob(**fields)


def units(systemd_dir: Path, name: str = "reindex") -> tuple[Path, Path]:
    return systemd_dir / f"noust-cron-{name}.timer", systemd_dir / f"noust-cron-{name}.service"


def journal(started: float, *, lines: tuple[str, ...] = ("reindexed 120 rows",)) -> str:
    """The journal of one run that began at ``started`` (epoch seconds)."""
    rows = [
        {
            "__REALTIME_TIMESTAMP": str(int(started * 1_000_000)),
            "UNIT": "noust-cron-reindex.service",
            "INVOCATION_ID": "inv-1",
            "MESSAGE": "Starting Noust cron job reindex...",
        }
    ]
    for offset, line in enumerate(lines, start=1):
        rows.append(
            {
                "__REALTIME_TIMESTAMP": str(int((started + offset) * 1_000_000)),
                "MESSAGE": line,
                "_SYSTEMD_INVOCATION_ID": "inv-1",
            }
        )
    return "\n".join(json.dumps(row) for row in rows)


class TestWhatIsWritten:
    def test_the_timer_names_one_date_and_the_service_settles_when_the_command_ends(
        self, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        at = ahead(days=3)

        created = CronManager().create_job(once(at))

        timer, service = units(systemd_dir)
        timer_text, service_text = timer.read_text(), service.read_text()
        calendar = at.strftime("%Y-%m-%d %H:%M:00")
        assert f"OnCalendar={calendar}\n" in timer_text
        # Off at that moment: it runs when the server comes back, and the record says it was late.
        assert "Persistent=true" in timer_text
        assert f"# Once: {calendar}\n" in service_text
        assert "ExecStopPost=-+/usr/bin/noust cron settle reindex\n" in service_text
        assert created.once_at == calendar and created.on_calendar == calendar
        assert ("systemctl", "enable", "--now", "noust-cron-reindex.timer") in runner.calls

    def test_a_task_that_repeats_is_written_as_it_always_was(
        self, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        CronManager().create_job(
            CronJob(name="reindex", command="/usr/bin/true", schedule="daily", user="www-data")
        )

        _, service = units(systemd_dir)
        assert "# Once:" not in service.read_text()
        assert "ExecStopPost" not in service.read_text()

    def test_the_schedule_is_ignored_when_a_moment_is_given(
        self, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        created = CronManager().create_job(once(ahead(days=1), schedule="hourly"))

        assert created.on_calendar == created.once_at

    def test_a_moment_that_has_passed_is_refused_and_nothing_is_written(
        self, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        with pytest.raises(ValidationError, match="has already passed"):
            CronManager().create_job(once(ahead(minutes=-30)))

        assert list(systemd_dir.iterdir()) == []
        assert not runner.ran("systemctl", "enable")

    def test_what_the_person_typed_is_never_copied_into_the_unit(
        self, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        with pytest.raises(ValidationError):
            CronManager().create_job(once(ahead(days=1), once_at="2030-01-01 10:00\nOnBootSec=1"))

        assert list(systemd_dir.iterdir()) == []

    def test_a_task_of_one_time_lists_with_its_moment_and_says_so(
        self, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        at = ahead(days=2)
        CronManager().create_job(once(at))
        runner.script(
            ["systemctl", "list-unit-files"], stdout="noust-cron-reindex.timer enabled enabled\n"
        )

        [entry] = CronManager().list_jobs()

        assert entry["once"] is True
        assert entry["once_at"] == at.isoformat(timespec="seconds")

    def test_a_task_that_repeats_lists_as_not_once(
        self, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        CronManager().create_job(
            CronJob(name="reindex", command="/usr/bin/true", schedule="daily", user="www-data")
        )
        runner.script(
            ["systemctl", "list-unit-files"], stdout="noust-cron-reindex.timer enabled enabled\n"
        )

        [entry] = CronManager().list_jobs()

        assert entry["once"] is False and entry["once_at"] == ""


class TestSettling:
    @pytest.fixture
    def task(self, runner: FakeRunner, systemd_dir: Path, store: NoustStore, config_file: Path):
        at = ahead(days=1)
        CronManager().create_job(once(at))
        return at

    def test_a_task_that_succeeded_is_recorded_and_retired(
        self, runner: FakeRunner, systemd_dir: Path, task: datetime
    ) -> None:
        started = task.timestamp() + 1
        runner.script(["systemctl", "show"], stdout=SHOW_SUCCESS)
        runner.script(["journalctl"], stdout=journal(started))

        run = CronManager().settle_once("reindex")

        assert run.ok and run.result == "success" and run.exit_code == 0 and not run.late
        assert run.output == "reindexed 120 rows\n"
        assert run.run_at == task.isoformat(timespec="seconds")
        [found] = CronOnceHistory().recent()
        assert found.name == "reindex" and found.command == "/usr/bin/php artisan search:reindex"
        assert found.run_as == "www-data" and found.working_directory == "/srv/shop"
        # The units are gone, the timer is disabled and systemd read the change.
        assert not any(path.exists() for path in units(systemd_dir))
        assert ("systemctl", "disable", "noust-cron-reindex.timer") in runner.calls
        assert runner.calls[-1] == ("systemctl", "daemon-reload")

    def test_the_service_is_never_stopped_from_inside_its_own_hook(
        self, runner: FakeRunner, systemd_dir: Path, task: datetime
    ) -> None:
        runner.script(["systemctl", "show"], stdout=SHOW_SUCCESS)
        runner.script(["journalctl"], stdout=journal(task.timestamp()))

        CronManager().settle_once("reindex")

        assert not any(
            call[:2] in (("systemctl", "stop"), ("systemctl", "restart"))
            and call[2].endswith(".service")
            for call in runner.calls
        )

    def test_a_failure_keeps_the_exit_code_and_what_the_command_printed(
        self, runner: FakeRunner, systemd_dir: Path, task: datetime
    ) -> None:
        runner.script(["systemctl", "show"], stdout=SHOW_FAILED)
        runner.script(
            ["journalctl"], stdout=journal(task.timestamp(), lines=("starting", "ERROR: disk full"))
        )

        run = CronManager().settle_once("reindex")

        assert not run.ok and run.result == "exit-code" and run.exit_code == 2
        assert run.output == "starting\nERROR: disk full\n"
        assert CronOnceHistory().recent()[0].result == "exit-code"

    def test_what_systemd_tells_the_hook_is_what_is_recorded(
        self, runner: FakeRunner, systemd_dir: Path, task: datetime
    ) -> None:
        runner.script(
            ["systemctl", "show"], stdout="InvocationID=inv-1\nActiveState=deactivating\n"
        )
        runner.script(["journalctl"], stdout=journal(task.timestamp()))

        run = CronManager().settle_once("reindex", result="timeout", exit_code=None)

        assert run.result == "timeout" and run.exit_code is None

    def test_a_task_the_server_ran_late_says_so(
        self, runner: FakeRunner, systemd_dir: Path, task: datetime
    ) -> None:
        # The server was off at that moment and ran it ten minutes after.
        runner.script(["systemctl", "show"], stdout=SHOW_SUCCESS)
        runner.script(["journalctl"], stdout=journal(task.timestamp() + 600))

        run = CronManager().settle_once("reindex")

        assert run.late

    def test_a_task_that_started_within_two_minutes_is_not_late(
        self, runner: FakeRunner, systemd_dir: Path, task: datetime
    ) -> None:
        runner.script(["systemctl", "show"], stdout=SHOW_SUCCESS)
        runner.script(["journalctl"], stdout=journal(task.timestamp() + 90))

        assert not CronManager().settle_once("reindex").late

    def test_a_journal_that_cannot_be_read_still_retires_the_task_with_what_is_known(
        self, runner: FakeRunner, systemd_dir: Path, task: datetime
    ) -> None:
        runner.script(["systemctl", "show"], stdout=SHOW_SUCCESS)
        runner.script(["journalctl"], stderr="No journal files were found.", exit_code=1)

        run = CronManager().settle_once("reindex")

        assert run.started_at is None and not run.late and run.output == ""
        assert not any(path.exists() for path in units(systemd_dir))

    def test_a_credential_the_task_printed_is_not_kept(
        self, runner: FakeRunner, systemd_dir: Path, task: datetime
    ) -> None:
        token = "ghp_abcdefghijklmnopqrstuvwxyz0123456789ab"
        runner.script(["systemctl", "show"], stdout=SHOW_SUCCESS)
        runner.script(["journalctl"], stdout=journal(task.timestamp(), lines=(f"token {token}",)))

        CronManager().settle_once("reindex")

        assert token not in CronOnceHistory().recent()[0].output

    def test_the_hook_arriving_twice_settles_once(
        self, runner: FakeRunner, systemd_dir: Path, task: datetime
    ) -> None:
        runner.script(["systemctl", "show"], stdout=SHOW_SUCCESS)
        runner.script(["journalctl"], stdout=journal(task.timestamp()))
        CronManager().settle_once("reindex")

        with pytest.raises(ServiceError, match="Unknown cron job"):
            CronManager().settle_once("reindex")

        assert len(CronOnceHistory().recent()) == 1


class TestWhatIsNotSettled:
    def test_a_task_that_repeats_is_not_a_one_time_task(
        self, runner: FakeRunner, systemd_dir: Path, store: NoustStore
    ) -> None:
        CronManager().create_job(
            CronJob(name="reindex", command="/usr/bin/true", schedule="daily", user="www-data")
        )

        with pytest.raises(ServiceError, match="not a one-time task") as refused:
            CronManager().settle_once("reindex")

        assert "noust cron delete" in refused.value.details
        assert all(path.exists() for path in units(systemd_dir))

    def test_a_unit_noust_did_not_write_is_never_touched(
        self, runner: FakeRunner, systemd_dir: Path, store: NoustStore
    ) -> None:
        timer, service = units(systemd_dir)
        timer.write_text("[Timer]\nOnCalendar=2030-01-01 10:00:00\n")
        service.write_text("# Once: 2030-01-01 10:00:00\n[Service]\nExecStart=/bin/true\n")

        with pytest.raises(ServiceError, match="does not manage"):
            CronManager().settle_once("reindex")

        assert timer.exists() and service.exists()

    def test_a_name_that_is_not_a_task(self, runner: FakeRunner, systemd_dir: Path) -> None:
        with pytest.raises(ServiceError, match="Invalid cron job name"):
            CronManager().settle_once("../etc/passwd")


class TestTheMonitorsView:
    def test_the_outcome_says_a_task_is_one_time_and_a_stop_hook_is_still_a_run(
        self, runner: FakeRunner, systemd_dir: Path, store: NoustStore, config_file: Path
    ) -> None:
        CronManager().create_job(once(ahead(days=1)))
        runner.script(
            ["systemctl", "list-unit-files"], stdout="noust-cron-reindex.timer enabled enabled\n"
        )
        runner.script(
            ["systemctl", "show"],
            stdout=(
                "Id=noust-cron-reindex.service\nInvocationID=inv-1\nActiveState=deactivating\n"
                "Result=success\nExecMainStatus=0\n\n"
                "Id=noust-cron-reindex.timer\nActiveState=inactive\n"
            ),
        )

        [outcome] = CronManager().last_outcomes()

        assert outcome.once
        assert outcome.running
