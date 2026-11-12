# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Scheduling a backup on a machine whose timers are not systemd's.

The one place that makes a schedule real, ``BackupScheduler``, writes a timer and a service for systemd on a
server and says so in the store's rows. On a container host there is no systemd: the rows are the schedule and
the central's own scheduler runs them (``tests/test_central_scheduler.py``). So the same calls (create, remove,
read, list, install a policy's timer, ask a timer's state) keep the rows, write nothing for systemd and never
run ``systemctl``, and answer in the shapes their callers read. A server is held by the tests of the
scheduler itself, which are not touched.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.managers.backup_scheduler import BackupSchedule, BackupScheduler

DOMAIN = "shop.example.com"
APP = "shop-example-com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def a_container_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
    monkeypatch.setenv("NOUST_DEPLOY_HERE", "containers")
    Config.reset_instance()
    yield
    Config.reset_instance()


@pytest.fixture
def machine(tmp_path: Path, store: NoustStore) -> tuple[BackupScheduler, FakeRunner, Path]:
    runner = FakeRunner()
    units = tmp_path / "units"
    units.mkdir()
    scheduler = BackupScheduler(runner=runner)
    scheduler._unit_path = lambda name: units / name  # type: ignore[method-assign]
    return scheduler, runner, units


def test_creating_a_schedule_keeps_the_row_and_writes_nothing_for_systemd(
    machine: tuple[BackupScheduler, FakeRunner, Path], store: NoustStore
) -> None:
    scheduler, runner, units = machine

    created = scheduler.create_schedule(
        BackupSchedule(domain=DOMAIN, app_name=APP, schedule="daily", retention_count=5)
    )

    assert created is True
    record = store.get_backup_schedule(DOMAIN)
    assert record is not None
    assert (record.schedule, record.retention_count) == ("*-*-* 02:00:00", 5)
    assert list(units.iterdir()) == []
    assert runner.calls == []


def test_a_schedule_that_exists_is_read_back_from_its_row(
    machine: tuple[BackupScheduler, FakeRunner, Path],
) -> None:
    scheduler, runner, _ = machine
    assert scheduler.get_schedule(DOMAIN) is None

    scheduler.create_schedule(BackupSchedule(domain=DOMAIN, app_name=APP, schedule="weekly"))
    found = scheduler.get_schedule(DOMAIN)

    assert found is not None and found.schedule == "Mon *-*-* 02:00:00"
    assert runner.calls == []


def test_removing_it_deletes_the_row_and_runs_nothing(
    machine: tuple[BackupScheduler, FakeRunner, Path], store: NoustStore
) -> None:
    scheduler, runner, _ = machine
    scheduler.create_schedule(BackupSchedule(domain=DOMAIN, app_name=APP, schedule="daily"))

    assert scheduler.remove_schedule(DOMAIN) is True

    assert store.get_backup_schedule(DOMAIN) is None
    assert runner.calls == []


def test_the_listing_is_the_rows_in_the_shape_the_listing_of_timers_has(
    machine: tuple[BackupScheduler, FakeRunner, Path],
) -> None:
    scheduler, runner, _ = machine
    scheduler.create_schedule(
        BackupSchedule(
            domain=DOMAIN, app_name=APP, schedule="daily", retention_count=7, retention_days=30
        )
    )

    (entry,) = scheduler.list_schedules()

    assert entry["domain"] == DOMAIN and entry["timer"] == f"noust-backup-{APP}"
    assert entry["on_calendar"] == "*-*-* 02:00:00"
    assert (entry["retention_count"], entry["retention_days"]) == ("7", "30")
    assert (entry["include_databases"], entry["remote_only"]) == ("true", "false")
    assert entry["last_run"] == "never" and entry["next_run"] != "pending"
    assert runner.calls == []


def test_a_policy_s_timer_is_recorded_and_not_written_and_asking_its_state_asks_the_scheduler(
    machine: tuple[BackupScheduler, FakeRunner, Path], store: NoustStore
) -> None:
    from noust.managers.volume_backup_records import VolumePolicy, VolumeRecords

    scheduler, runner, units = machine
    VolumeRecords(store).save_policy(VolumePolicy(app_domain=DOMAIN, schedule="*-*-* 02:00:00"))

    unit = scheduler.install_volume_timer(DOMAIN, "*-*-* 02:00:00")
    state = scheduler.volume_timer_state(DOMAIN)

    assert unit == f"noust-backup-vol-{APP}"
    assert list(units.iterdir()) == [] and runner.calls == []
    assert state["installed"] is True and state["next_run"] is not None
    assert scheduler.remove_volume_timer(DOMAIN) is False


def test_what_has_no_task_is_not_installed(
    machine: tuple[BackupScheduler, FakeRunner, Path],
) -> None:
    scheduler, _, _ = machine

    assert scheduler.volume_timer_state("other.example.com")["installed"] is False
