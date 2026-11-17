# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A unit that says how its run ended is not said again by the monitor (item 201).

A backup's timer runs a command that announces its own failure (``backup_failed``), and so does a
scheduled task (``cron_failed``). The monitor, which watches every unit Noust manages, said the same
failure a second time as ``unit_failed``: nine failed sends were nine notices and nine more. Which
units say it themselves is a closed list in one place (:func:`announces_its_own_failure`), and only a
run that ended by its own exit counts as having said it: a run systemd killed (its time limit, the
memory limit, a signal) could not announce anything, and the monitor still does.
"""

from __future__ import annotations

import pytest

from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.managers.unit_states import announces_its_own_failure
from tests.support.monitor_units import make_monitor, scan, show_block
from tests.support.notifier import config  # noqa: F401  (pytest resolves fixtures by name)

# ruff: noqa: F811


class TestWhichUnitsSayItThemselves:
    @pytest.mark.parametrize(
        "unit",
        [
            "noust-backup-shop-example-com",
            "noust-backup-vol-shop-example-com",
            "noust-backup-db-postgresql-shop",
            "noust-backup-db-postgresql--all",
            "noust-backup-self",
            "noust-backup-self.service",
            "noust-cron-nightly",
            "noust-cron-shop-example-com-reindex",
        ],
    )
    def test_the_runs_of_backups_and_scheduled_tasks_do(self, unit: str) -> None:
        assert announces_its_own_failure(unit) is True

    @pytest.mark.parametrize(
        "unit",
        [
            # What runs without saying how it ended: the sweep of retention, the archiving of a
            # write-ahead log, an application, the console, the monitor itself.
            "noust-backup-sweep",
            "noust-backup-pitr-postgresql",
            "noust-backup-resend",
            "shop-example-com",
            "noust-web",
            "noust-monitor",
            "postgresql",
            "noust-update-shop-example-com",
        ],
    )
    def test_nothing_else_does(self, unit: str) -> None:
        assert announces_its_own_failure(unit) is False


def failure_of(unit: str, result: str, *, config: Config) -> list[str]:
    """What the monitor announces when this unit's last run ended with this result."""
    runner = FakeRunner()
    monitor, announced = make_monitor(runner, [unit])
    scan(
        runner,
        monitor,
        show_block(
            f"{unit}.service",
            active="failed",
            sub="failed",
            result=result,
            status=1,
        ),
    )
    return [event.code for event in announced.events]


class TestTheMonitor:
    def test_a_backup_that_failed_its_run_is_not_said_twice(self, config: Config) -> None:
        assert failure_of("noust-backup-shop-example-com", "exit-code", config=config) == []

    def test_a_scheduled_task_that_failed_its_run_is_not_said_twice(self, config: Config) -> None:
        assert failure_of("noust-cron-nightly", "exit-code", config=config) == []

    @pytest.mark.parametrize("result", ["timeout", "oom-kill", "signal", "watchdog", "core-dump"])
    def test_a_run_systemd_killed_could_not_say_anything_and_is_said(
        self, result: str, config: Config
    ) -> None:
        assert failure_of("noust-backup-shop-example-com", result, config=config) == ["unit.failed"]

    def test_the_sweep_has_nobody_else_to_say_it(self, config: Config) -> None:
        assert failure_of("noust-backup-sweep", "exit-code", config=config) == ["unit.failed"]

    def test_an_application_that_failed_is_said(self, config: Config) -> None:
        assert failure_of("shop-example-com", "exit-code", config=config) == ["unit.failed"]

    def test_a_unit_that_said_it_itself_and_runs_again_is_not_announced_as_back(
        self, config: Config
    ) -> None:
        runner = FakeRunner()
        unit = "noust-backup-shop-example-com"
        monitor, announced = make_monitor(runner, [unit])
        failed = show_block(
            f"{unit}.service", active="failed", sub="failed", result="exit-code", status=1
        )
        fine = show_block(f"{unit}.service", active="inactive", sub="dead", result="success")

        scan(runner, monitor, failed)
        scan(runner, monitor, failed)
        scan(runner, monitor, fine)

        # The run said it; there is no alert open for a recovery to close.
        assert announced.events == []
