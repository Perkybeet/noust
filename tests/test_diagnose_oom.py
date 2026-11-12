# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The diagnosis says which application the kernel killed for running out of memory (item 175).

The kernel's log line names a process and a pid, not a unit, and the diagnosis used to say so: it
blamed the kernel for any out-of-memory line of the week whenever the unit was failing. In cgroup v2
the kernel counts the kills by cgroup (``memory.events``, ``oom_kill``), so the diagnosis can say it
was this application, how many times, since when, and what its limit was against the most it used.
Without cgroup v2, or without the file, it is as it was, and says that it cannot tell.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.runner import FakeRunner
from tests.support.diagnose import (
    DOMAIN,
    UNIT_FILE,
    _app,
    _check,
    _diagnose,
    _fragment,
    _listening,
    _no_findings,
    _unit_state,
)

MIB = 1024 * 1024
CONTROL_GROUP = f"/system.slice/{UNIT_FILE}"
STARTED = "Tue 2026-11-10 03:00:00 UTC"


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def mount(tmp_path: Path) -> Path:
    """A cgroup v2 hierarchy: its root says which controllers exist."""
    (tmp_path / "cgroup.controllers").write_text("cpuset cpu io memory pids\n")
    return tmp_path


def _write(mount: Path, **files: str) -> Path:
    directory = mount / CONTROL_GROUP.lstrip("/")
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (directory / name.replace("_", ".", 1)).write_text(text)
    return directory


def _systemd_says(
    runner: FakeRunner, *, result: str = "success", group: str = CONTROL_GROUP
) -> None:
    runner.script(
        ["systemctl", "show", "-p", "ControlGroup,ActiveEnterTimestamp,Result", UNIT_FILE],
        stdout=f"ControlGroup={group}\nActiveEnterTimestamp={STARTED}\nResult={result}\n",
    )


def _running(runner: FakeRunner) -> None:
    _fragment(runner)
    _unit_state(runner)
    _listening(runner, 3000)
    _no_findings(runner)


def _crash_looping(runner: FakeRunner) -> None:
    _fragment(runner)
    _unit_state(runner, active="activating", sub="auto-restart", exec_status="137", restarts="6")
    _no_findings(runner)


def _kernel_log_mentions_oom(runner: FakeRunner) -> None:
    runner.script(
        ["journalctl", "-k", "--since", "-7d", "--grep", "oom", "-o", "short-iso"],
        stdout="2026-11-10T03:12:00+00:00 host kernel: Out of memory: Killed process 100 (node)\n",
    )


class TestAnApplicationTheKernelKilled:
    def test_the_check_says_how_many_times_and_since_when(
        self, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner, mount: Path
    ) -> None:
        _running(runner)
        _systemd_says(runner)
        _write(
            mount,
            memory_events="low 0\nhigh 0\nmax 9\noom 4\noom_kill 3\n",
            memory_max=f"{512 * MIB}\n",
            memory_peak=f"{511 * MIB}\n",
        )

        result = _diagnose(monkeypatch, runner, app=_app(ssl_enabled=False), cgroup_mount=mount)

        check = _check(result, "oom")
        assert check.status == "warn"
        assert "killed this application" in check.summary
        assert "3 times" in check.summary
        assert STARTED in check.summary
        assert "oom_kill=3" in check.evidence
        assert f"memory.max={512 * MIB}" in check.evidence
        assert f"memory.peak={511 * MIB}" in check.evidence

    def test_one_kill_is_said_in_the_singular(
        self, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner, mount: Path
    ) -> None:
        _running(runner)
        _systemd_says(runner)
        _write(mount, memory_events="oom_kill 1\n")

        result = _diagnose(monkeypatch, runner, app=_app(ssl_enabled=False), cgroup_mount=mount)

        assert "once" in _check(result, "oom").summary

    def test_a_running_application_that_was_killed_is_degraded_and_the_cause_is_its_limit(
        self, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner, mount: Path
    ) -> None:
        _running(runner)
        _systemd_says(runner)
        _write(
            mount,
            memory_events="oom_kill 3\n",
            memory_max=f"{512 * MIB}\n",
            memory_peak=f"{511 * MIB}\n",
        )

        result = _diagnose(monkeypatch, runner, app=_app(ssl_enabled=False), cgroup_mount=mount)

        assert result.verdict == "degraded"
        cause = result.probable_cause or ""
        assert "killed example.com for running out of memory" in cause
        assert "512.0 MB" in cause and "511.0 MB" in cause
        assert f"noust app limits {DOMAIN} --memory 768M --restart" in cause

    def test_a_crash_looping_application_that_was_killed_is_down_with_the_same_cause(
        self, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner, mount: Path
    ) -> None:
        _crash_looping(runner)
        _systemd_says(runner)
        _write(mount, memory_events="oom_kill 6\n", memory_max=f"{256 * MIB}\n")

        result = _diagnose(monkeypatch, runner, app=_app(ssl_enabled=False), cgroup_mount=mount)

        assert result.verdict == "down"
        assert "killed example.com for running out of memory" in (result.probable_cause or "")

    def test_with_no_limit_it_is_the_machine_that_ran_out(
        self, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner, mount: Path
    ) -> None:
        _running(runner)
        _systemd_says(runner)
        _write(
            mount, memory_events="oom_kill 2\n", memory_max="max\n", memory_peak=f"{900 * MIB}\n"
        )

        result = _diagnose(monkeypatch, runner, app=_app(ssl_enabled=False), cgroup_mount=mount)

        cause = result.probable_cause or ""
        assert "no memory limit" in cause
        assert "the server ran out of memory" in cause
        assert "noust app limits example.com --memory 1408M --restart" in cause

    def test_the_kill_is_this_applications_even_when_the_kernel_log_has_no_line(
        self, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner, mount: Path
    ) -> None:
        """The journal rotates, or is volatile: the cgroup still counts."""
        _running(runner)
        _systemd_says(runner)
        _write(mount, memory_events="oom_kill 1\n")

        result = _diagnose(monkeypatch, runner, app=_app(ssl_enabled=False), cgroup_mount=mount)

        assert result.probable_cause is not None

    def test_the_last_run_of_the_unit_ending_in_oom_kill_is_enough_when_the_cgroup_is_gone(
        self, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner, mount: Path
    ) -> None:
        """A unit that systemd restarts gets a new cgroup, and its counter starts again at zero."""
        _crash_looping(runner)
        _systemd_says(runner, result="oom-kill", group="")

        result = _diagnose(monkeypatch, runner, app=_app(ssl_enabled=False), cgroup_mount=mount)

        assert result.verdict == "down"
        assert "systemd records" in (result.probable_cause or "")
        assert _check(result, "oom").status == "warn"


class TestAnApplicationTheKernelDidNotKill:
    def test_a_cgroup_that_counted_nothing_and_a_clean_log_is_fine(
        self, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner, mount: Path
    ) -> None:
        _running(runner)
        _systemd_says(runner)
        _write(mount, memory_events="oom_kill 0\n")

        result = _diagnose(monkeypatch, runner, app=_app(ssl_enabled=False), cgroup_mount=mount)

        assert _check(result, "oom").status == "ok"
        assert result.verdict == "healthy"


class TestAServerThatCannotSay:
    def test_without_cgroup_v2_the_log_is_all_there_is_and_the_diagnosis_says_so(
        self, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner, tmp_path: Path
    ) -> None:
        _crash_looping(runner)
        _kernel_log_mentions_oom(runner)
        _systemd_says(runner)

        result = _diagnose(monkeypatch, runner, app=_app(ssl_enabled=False), cgroup_mount=tmp_path)

        check = _check(result, "oom")
        assert check.status == "warn"
        assert "cannot say which" in check.summary
        assert "cgroup v2" in check.summary
        # As before: the log mentions it and the unit is failing.
        assert (
            result.probable_cause
            == "Killed by the kernel for running out of memory (limit or machine)."
        )

    def test_without_the_file_it_says_which_file(
        self, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner, mount: Path
    ) -> None:
        _crash_looping(runner)
        _kernel_log_mentions_oom(runner)
        _systemd_says(runner)
        _write(mount)  # the cgroup is there, memory.events is not

        result = _diagnose(monkeypatch, runner, app=_app(ssl_enabled=False), cgroup_mount=mount)

        assert "memory.events" in _check(result, "oom").summary

    def test_an_application_with_no_unit_to_ask_is_left_as_it_was(
        self, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner, mount: Path
    ) -> None:
        _no_findings(runner)

        result = _diagnose(monkeypatch, runner, app=_app(is_static=True), cgroup_mount=mount)

        assert _check(result, "oom").status == "ok"

    def test_the_log_with_no_line_and_no_cgroup_is_as_it_was(
        self, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner, tmp_path: Path
    ) -> None:
        _running(runner)

        result = _diagnose(monkeypatch, runner, app=_app(ssl_enabled=False), cgroup_mount=tmp_path)

        assert _check(result, "oom").status == "ok"
