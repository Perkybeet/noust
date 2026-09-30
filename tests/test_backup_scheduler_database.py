# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The timer of a database's backup policy: the same scheduler, another unit.

An application's schedule and a database's share the systemd timer machinery
(the escaped templates, the calendar validation, the unit directory), and this
module holds them to not stepping on each other:

- the units carry the database as the description and run ``noust db
  backup-run``, with ``$`` and ``%`` doubled so a name is never expanded;
- a timer that is not a database's is never overwritten or removed, whatever
  it is called;
- ``list_schedules`` (the applications' listing) neither shows nor adopts a
  database's timer.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.exceptions import BackupError
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore, get_store
from noust.managers.backup_scheduler import BackupScheduler

UNIT = "noust-backup-db-postgresql-shop"


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    """Force a fresh store singleton per test."""
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def systemd_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The unit directory, inside the sandbox.
    """
    path = tmp_path / "systemd"
    path.mkdir()
    monkeypatch.setattr(BackupScheduler, "SYSTEMD_DIR", path)
    return path


@pytest.fixture
def scheduler(runner: FakeRunner, systemd_dir: Path) -> BackupScheduler:
    """
    Args:
        runner: The fake runner.
        systemd_dir: The sandboxed unit directory.

    Returns:
        A scheduler over both.
    """
    return BackupScheduler(verbose=False, runner=runner)


class TestInstall:
    def test_it_writes_a_timer_and_a_service_and_enables_the_timer(
        self, scheduler: BackupScheduler, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        unit = scheduler.install_database_timer("postgresql", "shop", "daily")

        assert unit == UNIT
        timer = (systemd_dir / f"{UNIT}.timer").read_text()
        assert "Description=Noust database backup timer for postgresql/shop" in timer
        assert "OnCalendar=*-*-* 02:00:00" in timer
        assert "Persistent=true" in timer
        service = (systemd_dir / f"{UNIT}.service").read_text()
        assert "db backup-run shop --engine postgresql" in service
        assert "Type=oneshot" in service
        assert runner.ran("systemctl", "daemon-reload")
        assert runner.ran("systemctl", "enable", "--now", f"{UNIT}.timer")

    def test_the_service_carries_a_reason_so_the_timer_keeps_running_under_the_ens_profile(
        self, scheduler: BackupScheduler, systemd_dir: Path
    ) -> None:
        scheduler.install_database_timer("postgresql", "shop", "daily")

        service = (systemd_dir / f"{UNIT}.service").read_text()
        assert '--reason "Scheduled database backup policy"' in service

    def test_a_calendar_that_could_become_a_directive_is_refused_before_anything_is_written(
        self, scheduler: BackupScheduler, systemd_dir: Path
    ) -> None:
        with pytest.raises(BackupError):
            scheduler.install_database_timer("postgresql", "shop", "daily\nExecStart=/bin/evil")

        assert list(systemd_dir.iterdir()) == []

    def test_a_name_that_cannot_be_a_unit_name_is_refused(
        self, scheduler: BackupScheduler, systemd_dir: Path
    ) -> None:
        with pytest.raises(BackupError, match="Cannot schedule backups"):
            scheduler.install_database_timer("postgresql", "sh$op", "daily")

        assert list(systemd_dir.iterdir()) == []

    def test_a_timer_that_is_not_a_database_timer_is_not_overwritten(
        self, scheduler: BackupScheduler, systemd_dir: Path
    ) -> None:
        # An application on the domain db.postgresql.shop has this very unit name.
        app_timer = systemd_dir / f"{UNIT}.timer"
        app_timer.write_text("[Unit]\nDescription=Noust backup timer for db.postgresql.shop\n")

        with pytest.raises(BackupError, match="is not a database backup timer"):
            scheduler.install_database_timer("postgresql", "shop", "daily")

        assert "db.postgresql.shop" in app_timer.read_text()
        assert not (systemd_dir / f"{UNIT}.service").exists()

    def test_installing_again_changes_the_calendar(
        self, scheduler: BackupScheduler, systemd_dir: Path
    ) -> None:
        scheduler.install_database_timer("postgresql", "shop", "daily")
        scheduler.install_database_timer("postgresql", "shop", "weekly")

        assert "OnCalendar=Mon *-*-* 02:00:00" in (systemd_dir / f"{UNIT}.timer").read_text()

    def test_a_failing_enable_is_an_error_with_systemctls_words(
        self, scheduler: BackupScheduler, runner: FakeRunner
    ) -> None:
        runner.script(
            ("systemctl", "enable"), stderr="Failed to enable unit: no space", exit_code=1
        )

        with pytest.raises(BackupError) as failure:
            scheduler.install_database_timer("postgresql", "shop", "daily")

        assert "no space" in str(failure.value)

    def test_a_dollar_and_a_percent_in_a_name_are_doubled_in_the_command(
        self, scheduler: BackupScheduler
    ) -> None:
        text = scheduler._render(
            "database-backup-service.j2",
            target="mysql/a$b%c",
            engine="mysql",
            database="a$b%c",
            noust="/usr/bin/noust",
        )

        assert "db backup-run a$$b%%c --engine mysql" in text


class TestRemove:
    def test_it_removes_only_its_own_units(
        self, scheduler: BackupScheduler, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        scheduler.install_database_timer("postgresql", "shop", "daily")

        assert scheduler.remove_database_timer("postgresql", "shop") is True

        assert list(systemd_dir.iterdir()) == []
        assert runner.ran("systemctl", "disable", f"{UNIT}.timer")

    def test_a_timer_that_is_not_a_database_timer_is_left_alone(
        self, scheduler: BackupScheduler, systemd_dir: Path
    ) -> None:
        app_timer = systemd_dir / f"{UNIT}.timer"
        app_timer.write_text("[Unit]\nDescription=Noust backup timer for db.postgresql.shop\n")

        assert scheduler.remove_database_timer("postgresql", "shop") is False

        assert app_timer.exists()

    def test_removing_what_is_not_there_says_so(self, scheduler: BackupScheduler) -> None:
        assert scheduler.remove_database_timer("postgresql", "shop") is False


class TestState:
    def test_it_reads_when_the_timer_fires_next(
        self, scheduler: BackupScheduler, runner: FakeRunner
    ) -> None:
        runner.script(
            ("systemctl", "show", f"{UNIT}.timer"),
            stdout=(
                "LoadState=loaded\n"
                "NextElapseUSecRealtime=Tue 2026-09-30 02:03:11 UTC\n"
                "LastTriggerUSec=n/a\n"
            ),
        )

        state = scheduler.database_timer_state("postgresql", "shop")

        assert state == {
            "installed": True,
            "next_run": "Tue 2026-09-30 02:03:11 UTC",
            "last_run": None,
        }

    def test_a_timer_systemd_does_not_have_is_not_installed(
        self, scheduler: BackupScheduler, runner: FakeRunner
    ) -> None:
        runner.script(("systemctl", "show"), stdout="LoadState=not-found\n")

        assert scheduler.database_timer_state("postgresql", "shop")["installed"] is False


class TestApplicationListing:
    """The applications' listing is not the databases'."""

    def test_a_database_timer_is_not_listed_and_not_adopted_as_an_application(
        self, scheduler: BackupScheduler, runner: FakeRunner
    ) -> None:
        runner.script(
            ("systemctl", "list-timers"),
            stdout=(f"Tue 2026-09-30 02:00:00 UTC 5h left n/a n/a {UNIT}.timer {UNIT}.service\n"),
        )
        runner.script(
            ("systemctl", "show", f"{UNIT}.timer"),
            stdout=(
                "Description=Noust database backup timer for postgresql/shop\n"
                "TimersCalendar={ OnCalendar=*-*-* 02:00:00 ; next_elapse=x }\n"
            ),
        )

        schedules = scheduler.list_schedules()

        assert schedules == []
        assert get_store().get_backup_schedule("postgresql/shop") is None
        assert get_store().get_backup_schedule(UNIT.removeprefix("noust-backup-")) is None
