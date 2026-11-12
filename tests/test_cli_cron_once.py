# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust cron create --once``, ``settle`` and ``history`` (item 164).

The manager is exercised for real, through the templates, a sandboxed unit directory and the fake
runner, so a refusal here is the manager's refusal in the manager's words, the same the console
shows. What is pinned is what a person at a terminal reads: the moment in the server's time with
the zone said, a date that has passed refused with what time it is, the two options that only make
sense together, the history with what each run printed, and the command a unit runs.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.commands import cron as cron_cli
from noust.core.config import Config
from noust.core.cron_once_history import CronOnceHistory, OnceRun
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.managers.cron_manager import CronManager

CET = "CET-1CEST,M3.5.0,M10.5.0/3"


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


@pytest.fixture
def cli() -> CliRunner:
    return CliRunner()


def ahead(**delta: float) -> str:
    moment = datetime.now().astimezone() + timedelta(**delta)
    return moment.strftime("%Y-%m-%d %H:%M")


def create(cli: CliRunner, *extra: str, at: str | None = None, name: str = "reindex"):
    args = ["create", name, "/usr/bin/true", "--user", "www-data", *extra]
    if at is not None:
        args += ["--once", "--at", at]
    return cli.invoke(cron_cli.cli, args)


class TestCreate:
    def test_a_task_of_one_time_says_when_in_the_servers_time(
        self, cli: CliRunner, runner: FakeRunner, systemd_dir: Path, store: NoustStore
    ) -> None:
        at = ahead(days=2)

        result = create(cli, at=at)

        assert result.exit_code == 0, result.output
        assert "Created one-time task: reindex" in result.output
        assert at in result.output and "server time" in result.output and "UTC+0" in result.output
        assert "retires itself" in result.output
        assert "noust cron history" in result.output
        assert (systemd_dir / "noust-cron-reindex.timer").exists()

    def test_a_date_that_has_passed_is_refused_with_what_time_it_is_here(
        self, cli: CliRunner, runner: FakeRunner, systemd_dir: Path, store: NoustStore
    ) -> None:
        result = create(cli, at=ahead(hours=-2))

        assert result.exit_code == 1
        assert "has already passed" in result.output
        assert "It is " in result.output and "here" in result.output
        assert list(systemd_dir.iterdir()) == []

    def test_a_moment_without_once_says_what_to_add(
        self, cli: CliRunner, runner: FakeRunner, systemd_dir: Path, store: NoustStore
    ) -> None:
        result = cli.invoke(
            cron_cli.cli, ["create", "reindex", "/usr/bin/true", "--at", ahead(days=1)]
        )

        assert result.exit_code == 2
        assert "--once" in result.output

    def test_once_without_a_moment_says_what_to_add(
        self, cli: CliRunner, runner: FakeRunner, systemd_dir: Path, store: NoustStore
    ) -> None:
        result = cli.invoke(cron_cli.cli, ["create", "reindex", "/usr/bin/true", "--once"])

        assert result.exit_code == 2
        assert "--at" in result.output

    def test_a_schedule_that_repeats_does_not_go_with_once(
        self, cli: CliRunner, runner: FakeRunner, systemd_dir: Path, store: NoustStore
    ) -> None:
        result = create(cli, "--schedule", "weekly", at=ahead(days=1))

        assert result.exit_code == 2
        assert "--schedule" in result.output

    def test_the_list_shows_a_task_of_one_time_as_that(
        self, cli: CliRunner, runner: FakeRunner, systemd_dir: Path, store: NoustStore
    ) -> None:
        at = ahead(days=2)
        create(cli, at=at)
        runner.script(
            ["systemctl", "list-unit-files"], stdout="noust-cron-reindex.timer enabled enabled\n"
        )

        shown = cli.invoke(cron_cli.cli, ["list"])
        as_json = json.loads(cli.invoke(cron_cli.cli, ["list", "--json"]).output)

        assert "once " + at in shown.output
        job = as_json["jobs"][0]
        assert job["once"] is True and job["schedule"] == "once"
        assert job["once_at"].startswith(at.replace(" ", "T"))


class TestSettle:
    def test_the_hook_systemd_runs_records_the_run_and_retires_the_task(
        self, cli: CliRunner, runner: FakeRunner, systemd_dir: Path, store: NoustStore
    ) -> None:
        create(cli, at=ahead(days=1))
        runner.script(
            ["systemctl", "show"], stdout="InvocationID=inv-1\nActiveState=deactivating\n"
        )

        result = cli.invoke(
            cron_cli.cli,
            ["settle", "reindex"],
            env={"SERVICE_RESULT": "exit-code", "EXIT_CODE": "exited", "EXIT_STATUS": "2"},
        )

        assert result.exit_code == 0, result.output
        assert "reindex" in result.output and "exit-code" in result.output
        [found] = CronOnceHistory().recent()
        assert found.result == "exit-code" and found.exit_code == 2
        assert not (systemd_dir / "noust-cron-reindex.timer").exists()

    def test_a_command_that_was_killed_has_no_exit_status(
        self, cli: CliRunner, runner: FakeRunner, systemd_dir: Path, store: NoustStore
    ) -> None:
        create(cli, at=ahead(days=1))
        runner.script(["systemctl", "show"], stdout="InvocationID=inv-1\n")

        cli.invoke(
            cron_cli.cli,
            ["settle", "reindex"],
            env={"SERVICE_RESULT": "signal", "EXIT_CODE": "killed", "EXIT_STATUS": "TERM"},
        )

        [found] = CronOnceHistory().recent()
        assert found.result == "signal" and found.exit_code is None

    def test_a_task_that_is_not_there_is_an_error_that_says_so(
        self, cli: CliRunner, runner: FakeRunner, systemd_dir: Path, store: NoustStore
    ) -> None:
        result = cli.invoke(cron_cli.cli, ["settle", "nothing"])

        assert result.exit_code == 1
        assert "Unknown cron job" in result.output

    def test_a_task_that_repeats_is_refused_with_the_command_that_removes_it(
        self, cli: CliRunner, runner: FakeRunner, systemd_dir: Path, store: NoustStore
    ) -> None:
        cli.invoke(cron_cli.cli, ["create", "reindex", "/usr/bin/true", "--user", "www-data"])

        result = cli.invoke(cron_cli.cli, ["settle", "reindex"])

        assert result.exit_code == 1
        assert "not a one-time task" in result.output
        assert "noust cron delete reindex" in result.output


class TestHistory:
    def seed(self, store: NoustStore) -> None:
        history = CronOnceHistory(store)
        history.record(
            OnceRun(
                name="reindex",
                command="/usr/bin/php artisan search:reindex",
                run_at="2026-12-01T14:30:00+01:00",
                result="success",
                exit_code=0,
                app_domain="shop.example.com",
                started_at="2026-12-01T13:30:02+00:00",
                output="reindexed 120 rows\n",
            )
        )
        history.record(
            OnceRun(
                name="cleanup",
                command="/usr/bin/true",
                run_at="2026-12-02T03:00:00+01:00",
                result="exit-code",
                exit_code=2,
                late=True,
                started_at="2026-12-02T02:40:00+00:00",
                output="ERROR: disk full\n",
            )
        )

    def test_nothing_has_run(
        self, cli: CliRunner, runner: FakeRunner, systemd_dir: Path, store: NoustStore
    ) -> None:
        result = cli.invoke(cron_cli.cli, ["history"])

        assert result.exit_code == 0
        assert "No one-time task has run in the last 30 days" in result.output

    def test_the_runs_are_listed_newest_first_and_late_ones_say_so(
        self, cli: CliRunner, runner: FakeRunner, systemd_dir: Path, store: NoustStore
    ) -> None:
        self.seed(store)

        result = cli.invoke(cron_cli.cli, ["history"])

        assert result.exit_code == 0
        assert result.output.index("cleanup") < result.output.index("reindex")
        assert "late" in result.output and "exit 2" in result.output

    def test_one_task_shows_what_it_printed(
        self, cli: CliRunner, runner: FakeRunner, systemd_dir: Path, store: NoustStore
    ) -> None:
        self.seed(store)

        result = cli.invoke(cron_cli.cli, ["history", "cleanup"])

        assert "ERROR: disk full" in result.output
        assert "reindexed 120 rows" not in result.output

    def test_json_is_what_the_api_answers(
        self, cli: CliRunner, runner: FakeRunner, systemd_dir: Path, store: NoustStore
    ) -> None:
        self.seed(store)

        body = json.loads(cli.invoke(cron_cli.cli, ["history", "--json"]).output)

        assert body["total"] == 2 and body["days"] == 30
        assert body["items"][0]["name"] == "cleanup" and body["items"][0]["late"] is True
        assert body["items"][1]["output"] == "reindexed 120 rows\n"
        assert body["zone"].startswith("UTC+0")

    def test_the_command_the_failure_points_to_exists_and_runs(
        self, cli: CliRunner, runner: FakeRunner, systemd_dir: Path, store: NoustStore
    ) -> None:
        # compose_cron tells the operator to run `noust cron history` after a one-time task failed.
        assert cli.invoke(cron_cli.cli, ["history"]).exit_code == 0
