# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust health`` and ``GET /api/system/health`` show the files the console and the monitor hold open.

Issue 19: a console reached its limit of 1024 files in three days and nothing said so until every
request failed. The health report now shows what ``noust-web`` and ``noust-monitor`` have open
against the limit each was given, warns from 80 % and is an issue where requests are failing, so a
server is told while there is still time to restart it.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from noust.cli.app import cli
from noust.core import descriptors
from noust.core.descriptors import Usage
from noust.core.runner import FakeRunner, set_runner
from noust.managers import health


def machine(
    monkeypatch: pytest.MonkeyPatch,
    runner: FakeRunner,
    *,
    web: Usage | None,
    monitor: Usage | None,
) -> None:
    """A server whose two units run (or not) holding what each is given."""
    for unit, pid, usage in (
        ("noust-web", 4242, web),
        ("noust-monitor", 4343, monitor),
    ):
        runner.script(
            ["systemctl", "show", "-p", "MainPID", "--value", f"{unit}.service"],
            stdout=f"{pid if usage is not None else 0}\n",
        )
    held = {4242: web, 4343: monitor}
    monkeypatch.setattr(descriptors, "read_usage", lambda pid, **_: held.get(pid))


@pytest.fixture
def runner() -> FakeRunner:
    fake = FakeRunner()
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


class TestTheCheck:
    def test_it_shows_what_each_unit_holds_against_its_limit(
        self, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
    ) -> None:
        machine(monkeypatch, runner, web=Usage(193, 65536), monitor=Usage(66, 65536))
        issues: list[str] = []
        warnings: list[str] = []

        check = health._check_descriptors(issues, warnings)

        assert check.name == "File descriptors"
        assert check.status == "ok"
        assert check.value == "noust-web 193 of 65536, noust-monitor 66 of 65536"
        assert (issues, warnings) == ([], [])

    def test_from_80_percent_it_warns_and_says_what_to_do(
        self, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
    ) -> None:
        machine(monkeypatch, runner, web=Usage(850, 1024), monitor=Usage(66, 65536))
        issues: list[str] = []
        warnings: list[str] = []

        check = health._check_descriptors(issues, warnings)

        assert check.status == "warning"
        assert issues == []
        (warning,) = warnings
        assert warning.startswith("noust-web holds 850 of its 1024 file descriptors (83%)")
        assert "noust web restart" in warning and "noust setup doctor" in warning

    def test_where_requests_are_failing_it_is_an_issue(
        self, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
    ) -> None:
        machine(monkeypatch, runner, web=Usage(1024, 1024), monitor=Usage(66, 65536))
        issues: list[str] = []
        warnings: list[str] = []

        check = health._check_descriptors(issues, warnings)

        assert check.status == "error"
        (issue,) = issues
        assert issue.startswith("noust-web holds 1024 of its 1024 file descriptors (100%)")
        assert warnings == []

    def test_a_unit_that_is_not_running_is_left_out_and_none_running_is_info(
        self, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
    ) -> None:
        machine(monkeypatch, runner, web=None, monitor=Usage(66, 65536))
        assert health._check_descriptors([], []).value == "noust-monitor 66 of 65536"

        machine(monkeypatch, runner, web=None, monitor=None)
        check = health._check_descriptors([], [])

        assert (check.status, check.value) == ("info", "No Noust service is running as a unit")

    def test_a_process_with_no_limit_is_shown_without_one_and_never_warns(
        self, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
    ) -> None:
        machine(monkeypatch, runner, web=Usage(90000, None), monitor=None)
        warnings: list[str] = []

        check = health._check_descriptors([], warnings)

        assert check.value == "noust-web 90000, no limit"
        assert check.status == "ok" and warnings == []

    def test_a_process_that_cannot_be_read_is_said_not_to_have_been(
        self, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
    ) -> None:
        # Running, with a pid, and /proc will not say: not an error of the server.
        runner.script(
            ["systemctl", "show", "-p", "MainPID", "--value", "noust-web.service"], stdout="4242\n"
        )
        monkeypatch.setattr(descriptors, "read_usage", lambda pid, **_: None)

        check = health._check_descriptors([], [])

        assert check.status == "info"
        assert "could not be read" in check.value

    def test_the_units_are_the_two_noust_runs_for_itself(self) -> None:
        from noust.core import paths

        assert tuple(health.DESCRIPTOR_UNITS) == (paths.WEB_UNIT, paths.MONITOR_UNIT)


class TestInTheReport:
    def patched_report(self, monkeypatch: pytest.MonkeyPatch) -> health.HealthReport:
        ok = health.HealthCheck("x", "y", "ok")
        for name in (
            "_check_disk_space",
            "_check_applications",
            "_check_certificates",
            "_check_memory",
            "_check_builds",
            "_check_accounts",
            "_check_names",
            "_check_updates",
            "_check_recipes",
            "_check_audit",
            "_check_security_posture",
        ):
            monkeypatch.setattr(health, name, lambda *a, **k: ok)
        monkeypatch.setattr(health, "_check_web_servers", lambda *a, **k: (ok, ok))

        def starved(issues: list[str], warnings: list[str]) -> health.HealthCheck:
            issues.append("noust-web holds 1024 of its 1024 file descriptors (100%)")
            return health.HealthCheck("File descriptors", "noust-web 1024 of 1024", "error")

        monkeypatch.setattr(health, "_check_descriptors", starved)
        return health.collect_health_report(hardening="off")

    def test_the_check_and_its_issue_are_in_the_report_both_clients_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        report = self.patched_report(monkeypatch)

        assert "File descriptors" in [check.name for check in report.checks]
        assert report.verdict == "error"
        assert report.issues == ["noust-web holds 1024 of its 1024 file descriptors (100%)"]

    def test_the_command_prints_it_with_the_resources_and_the_json_carries_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        report = self.patched_report(monkeypatch)
        monkeypatch.setattr("noust.cli.commands.health.collect_health_report", lambda **_: report)

        shown = CliRunner().invoke(cli, ["health"])
        as_json = CliRunner().invoke(cli, ["health", "--json"])

        assert "File descriptors" in shown.output and "noust-web 1024 of 1024" in shown.output
        assert "System has issues that need attention" in shown.output
        names = [check["name"] for check in json.loads(as_json.output)["checks"]]
        assert "File descriptors" in names

    def test_the_api_lists_it_among_the_checks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from noust.web.api import system as system_api

        report = self.patched_report(monkeypatch)
        monkeypatch.setattr(system_api, "collect_health_report", lambda **_: report)

        out = system_api.get_system_health({})

        assert "File descriptors" in [check.name for check in out.checks]
