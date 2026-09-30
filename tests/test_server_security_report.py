# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the hardening report nobody has to ask for.

From the owner's server: the Security tab said "The checks have not run yet"
above views full of live data, because the report only existed in the process
that last ran the checks, and only after someone ran them. What is pinned here:
a complete report is kept beside the store for every process to read; it goes
stale after a change and old after an hour; the monitor asks systemd to run
the checks outside its own sandbox when the report is missing or due; and the
console runs them once, in the background, instead of saying they never ran.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core.fs import RecordingFileSystem
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.managers.server import host as host_module
from noust.managers.server import security_checks
from noust.managers.server.security import ServerSecurity
from noust.managers.server.security_checks import REPORT_PERIOD, CheckReport, run_checks
from noust.managers.server.security_probe import SecurityProbe
from noust.managers.server.security_risks import AcceptedRisks
from tests.server_security_support import NOW, FakeHost, FakeSshd

NOW_DT = datetime.fromtimestamp(NOW, tz=timezone.utc)


@pytest.fixture(autouse=True)
def fresh() -> None:
    host_module.reset_platform_cache()
    security_checks.forget_report()
    yield
    security_checks.wait_for_refresh(timeout=10)
    host_module.reset_platform_cache()
    security_checks.forget_report()


@pytest.fixture
def store(tmp_path: Path) -> NoustStore:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db", fs=RecordingFileSystem())
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def host(tmp_path: Path) -> FakeHost:
    fake = FakeHost(tmp_path / "root")
    fake.write("/etc/os-release", 'ID=debian\nVERSION_ID="12"\n')
    return fake


@pytest.fixture
def runner(host: FakeHost) -> FakeSshd:
    fake = FakeSshd(host)
    fake.only_knows("ufw", "systemctl", "sshd")
    fake.script(["ufw", "status", "verbose"], stdout="Status: inactive\n")
    fake.script(["ufw", "show", "added"], stdout="")
    return fake


@pytest.fixture
def risks(store: NoustStore) -> AcceptedRisks:
    return AcceptedRisks(store, clock=lambda: NOW_DT)


def _probe(runner: FakeSshd, host: FakeHost, now: float = NOW) -> SecurityProbe:
    return SecurityProbe(runner=runner, host=host.paths, clock=lambda: now)


def _security(runner: FakeSshd, host: FakeHost, risks: AcceptedRisks, tmp_path: Path):
    return ServerSecurity(
        actor="tester",
        runner=runner,
        host=host.paths,
        changes=tmp_path / "changes",
        console_port=8080,
        clock=lambda: NOW,
        python="/usr/bin/python3",
        risks=risks,
    )


def _report(checked_at: datetime, **kwargs: Any) -> CheckReport:
    return CheckReport([], checked_at.isoformat(timespec="seconds"), **kwargs)


# Kept for every process ---------------------------------------------------------------


class TestKept:
    def test_a_complete_report_is_read_back_by_another_process(self, runner, host, risks, store):
        report = run_checks(_probe(runner, host), risks=risks, console_port=8080)
        # What another process sees: nothing in its memory, the file beside the store.
        security_checks.forget_memory()

        read = security_checks.cached_report()

        assert read is not None and read is not report
        assert read.checked_at == report.checked_at
        assert [check.to_dict() for check in read.checks] == [
            check.to_dict() for check in report.checks
        ]
        path = security_checks.report_path()
        assert path.parent == store.db_path.parent / "security"
        assert path.stat().st_mode & 0o777 == 0o600

    def test_a_quick_report_stays_in_its_process(self, runner, host, risks, store):
        """The quick checks skip the package manager: they would hide its findings from everyone."""
        run_checks(_probe(runner, host), risks=risks, host_checks=False, console_port=8080)
        security_checks.forget_memory()

        assert security_checks.cached_report() is None

    def test_the_newest_report_wins(self, runner, host, risks, store):
        run_checks(_probe(runner, host), risks=risks, console_port=8080)
        later = run_checks(
            _probe(runner, host, NOW + 60), risks=risks, host_checks=False, console_port=8080
        )

        assert security_checks.cached_report() is later

    def test_an_unreadable_file_is_no_report(self, store):
        path = security_checks.report_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")

        assert security_checks.cached_report() is None


# Stale and old ----------------------------------------------------------------------


class TestDue:
    def test_a_fresh_report_is_not_due(self):
        assert not _report(NOW_DT).due(NOW + REPORT_PERIOD - 1)

    def test_a_report_older_than_the_period_is_due(self):
        assert _report(NOW_DT).due(NOW + REPORT_PERIOD)

    def test_a_stale_or_quick_report_is_due(self):
        assert _report(NOW_DT, stale=True).due(NOW)
        assert _report(NOW_DT, complete=False).due(NOW)

    def test_a_change_marks_the_kept_report_stale(self, runner, host, risks, store, tmp_path):
        run_checks(_probe(runner, host), risks=risks, console_port=8080)

        _security(runner, host, risks, tmp_path)._changed()

        kept = security_checks.cached_report()
        assert kept is not None and kept.stale
        assert json.loads(security_checks.report_path().read_text())["stale"] is True


# The console's first look -----------------------------------------------------------


class TestConsole:
    def test_the_overview_with_no_report_runs_the_checks_once_in_the_background(
        self, runner, host, risks, store, tmp_path
    ):
        security = _security(runner, host, risks, tmp_path)
        gate = threading.Event()
        started: list[int] = []
        real = security_checks.run_checks

        def slow(*args: Any, **kwargs: Any) -> CheckReport:
            started.append(1)
            gate.wait(10)
            return real(*args, **kwargs)

        security_checks_run = pytest.MonkeyPatch()
        security_checks_run.setattr("noust.managers.server.security.run_checks", slow)
        try:
            first = security.overview(refresh_if_due=True)
            second = security.overview(refresh_if_due=True)
            gate.set()
            assert security_checks.wait_for_refresh(timeout=10)
        finally:
            security_checks_run.undo()

        assert first["checking"] is True and first["checked_at"] is None
        assert second["checking"] is True
        assert len(started) == 1
        done = security.overview(refresh_if_due=True)
        assert done["checking"] is False
        assert done["checked_at"] is not None and done["counts"] is not None

    def test_the_overview_of_a_fresh_report_starts_nothing(
        self, runner, host, risks, store, tmp_path
    ):
        run_checks(_probe(runner, host), risks=risks, console_port=8080)
        security = _security(runner, host, risks, tmp_path)

        data = security.overview(refresh_if_due=True)

        assert data["checking"] is False and data["checked_at"] is not None
        assert not security_checks.refreshing()

    def test_an_old_report_is_shown_while_it_is_checked_again(
        self, runner, host, risks, store, tmp_path
    ):
        run_checks(_probe(runner, host, NOW - REPORT_PERIOD - 5), risks=risks, console_port=8080)
        security = _security(runner, host, risks, tmp_path)

        data = security.overview(refresh_if_due=True)

        assert data["checking"] is True and data["counts"] is not None
        security_checks.wait_for_refresh(timeout=10)

    def test_the_checks_wait_for_the_run_in_flight_instead_of_starting_another(
        self, runner, host, risks, store, tmp_path
    ):
        security = _security(runner, host, risks, tmp_path)
        security.overview(refresh_if_due=True)

        report = security.checks()

        assert report.checked_at is not None
        assert runner.calls.count(("ufw", "status", "verbose")) == 1

    def test_the_cli_summary_never_starts_a_background_run(
        self, runner, host, risks, store, tmp_path
    ):
        data = _security(runner, host, risks, tmp_path).overview()

        assert data["checking"] is False
        assert not security_checks.refreshing()


# The monitor ---------------------------------------------------------------------------


class TestMonitor:
    @pytest.fixture
    def monitor(self, store) -> Any:
        from noust.monitor.process_monitor import MonitorConfig, ProcessMonitor

        fake = FakeRunner()
        instance = ProcessMonitor(config=MonitorConfig(), runner=fake)
        instance.runner = fake
        return instance

    def _refreshes(self, monitor: Any) -> list[tuple[str, ...]]:
        return [call for call in monitor.runner.calls if call[0] == "systemd-run"]

    def test_with_no_report_the_monitor_has_systemd_run_the_checks(self, monitor):
        monitor._refresh_security_report()

        (call,) = self._refreshes(monitor)
        assert "--unit=noust-security-checks" in call
        assert "--collect" in call and "--no-block" in call
        assert call[-3:] == ("-m", "noust.managers.server.security_checks", "refresh")

    def test_a_fresh_report_is_left_alone(self, monitor, runner, host, risks):
        now = datetime.now(timezone.utc).timestamp()
        run_checks(_probe(runner, host, now), risks=risks, console_port=8080)

        monitor._refresh_security_report()

        assert self._refreshes(monitor) == []

    def test_an_hour_old_report_is_refreshed(self, monitor, runner, host, risks):
        old = datetime.now(timezone.utc) - timedelta(seconds=REPORT_PERIOD + 60)
        run_checks(_probe(runner, host, old.timestamp()), risks=risks, console_port=8080)
        security_checks.forget_memory()

        monitor._refresh_security_report()

        assert len(self._refreshes(monitor)) == 1

    def test_a_run_just_started_is_not_started_again(self, monitor):
        monitor._refresh_security_report()
        monitor._refresh_security_report()

        assert len(self._refreshes(monitor)) == 1


# What systemd runs --------------------------------------------------------------------


class TestMain:
    def test_refresh_runs_the_complete_checks_and_keeps_them(self, monkeypatch, store):
        seen: dict[str, Any] = {}

        def fake_run(**kwargs: Any) -> CheckReport:
            seen.update(kwargs)
            return _report(NOW_DT)

        monkeypatch.setattr(security_checks, "run_checks", fake_run)

        assert security_checks.main(["refresh"]) == 0
        assert seen.get("host_checks", True) is True
