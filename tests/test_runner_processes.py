"""
Tests for long-lived processes: :meth:`CommandRunner.start` and its handles.

A tunnel is the one command Noust runs that is meant to keep running. What
replaces the mandatory deadline is ownership: the handle stops the process and
everything it started, and interpreter shutdown stops whatever is left.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

import pytest

from noust.core import runner as runner_module
from noust.core.runner import (
    EXIT_NOT_FOUND,
    STDERR_TAIL_LINES,
    CommandError,
    CommandResult,
    CommandRunner,
    DryRunRunner,
    FakeRunner,
    SubprocessRunner,
    terminate_all_processes,
)


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); it is dead for every purpose here.
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            return handle.read().split(")")[-1].split()[0] != "Z"
    except FileNotFoundError:
        return False


@pytest.mark.allow_subprocess
class TestSubprocessStart:
    def test_a_started_process_runs_until_terminated(self):
        handle = SubprocessRunner().start([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            assert handle.pid > 0
            assert handle.is_alive()
            assert handle.exit_code is None
        finally:
            code = handle.terminate(timeout=5)
        assert not handle.is_alive()
        assert code == -signal.SIGTERM

    def test_terminate_kills_the_whole_group(self, tmp_path: Path):
        """A grandchild dies with its parent: ssh's helpers must not be left behind."""
        pid_file = tmp_path / "child.pid"
        script = (
            "import subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
            "time.sleep(60)\n"
        )
        handle = SubprocessRunner().start([sys.executable, "-c", script])
        assert _wait_for(lambda: pid_file.exists() and pid_file.read_text().strip() != "")
        grandchild = int(pid_file.read_text())
        assert _pid_alive(grandchild)

        handle.terminate(timeout=5)

        assert _wait_for(lambda: not _pid_alive(grandchild))

    def test_the_stderr_tail_is_kept_verbatim_and_capped(self):
        count = STDERR_TAIL_LINES + 20
        script = f"import sys\nfor i in range({count}): print('line', i, file=sys.stderr)\n"
        handle = SubprocessRunner().start([sys.executable, "-c", script])
        assert _wait_for(lambda: not handle.is_alive())

        tail = handle.stderr_tail().splitlines()

        assert len(tail) == STDERR_TAIL_LINES
        assert tail[-1] == f"line {count - 1}"
        assert handle.exit_code == 0

    def test_a_missing_program_is_a_process_that_already_ended(self):
        handle = SubprocessRunner().start(["noust-definitely-not-a-program"])

        assert not handle.is_alive()
        assert handle.exit_code == EXIT_NOT_FOUND
        assert "Command not found" in handle.stderr_tail()
        assert handle.terminate() == EXIT_NOT_FOUND

    def test_secrets_are_redacted_from_the_recorded_argv(self):
        handle = SubprocessRunner().start(
            [sys.executable, "-c", "pass", "s3cr" + "et"], secrets=["s3cr" + "et"]
        )
        handle.terminate()
        assert "s3cr" + "et" not in " ".join(handle.argv)

    def test_shutdown_stops_every_live_process(self):
        handle = SubprocessRunner().start([sys.executable, "-c", "import time; time.sleep(60)"])
        assert handle.is_alive()

        terminate_all_processes()

        assert not handle.is_alive()
        assert handle not in runner_module._live_processes

    def test_a_string_argv_is_refused(self):
        with pytest.raises(ValueError):
            SubprocessRunner().start("ssh -N host")  # type: ignore[arg-type]


class TestDryRunStart:
    def test_a_mutating_process_is_not_started(self):
        inner = FakeRunner()
        dry = DryRunRunner(inner)

        handle = dry.start(["ssh", "-N", "node"])

        assert inner.calls == []
        assert dry.skipped == [("ssh", "-N", "node")]
        assert not handle.is_alive()
        assert "dry run" in handle.stderr_tail()

    def test_a_read_only_process_is_started(self):
        inner = FakeRunner()
        handle = DryRunRunner(inner).start(["journalctl", "-f", "-u", "x"])

        assert inner.calls == [("journalctl", "-f", "-u", "x")]
        assert handle.is_alive()


class TestFakeStart:
    def test_an_unscripted_process_runs_until_terminated(self):
        fake = FakeRunner()
        handle = fake.start(["ssh", "-N", "host"])

        assert fake.calls == [("ssh", "-N", "host")]
        assert fake.processes == [handle]
        assert handle.is_alive() and handle.exit_code is None

        handle.terminate()

        assert not handle.is_alive()
        assert fake.processes[0].terminated

    def test_a_scripted_failure_starts_dead_with_its_stderr(self):
        fake = FakeRunner().script(["ssh"], exit_code=255, stderr="Permission denied")
        handle = fake.start(["ssh", "-N", "host"])

        assert not handle.is_alive()
        assert handle.exit_code == 255
        assert handle.stderr_tail() == "Permission denied"

    def test_die_simulates_a_dropped_connection(self):
        handle = FakeRunner().start(["ssh"])
        handle.die(255, "Connection reset by peer")

        assert not handle.is_alive()
        assert handle.exit_code == 255
        assert handle.stderr_tail() == "Connection reset by peer"
        assert not handle.terminated


def test_a_runner_without_long_lived_support_says_so():
    class Minimal(CommandRunner):
        def run(self, argv, **kwargs) -> CommandResult:  # type: ignore[override]
            return CommandResult(argv=tuple(argv), exit_code=0)

        def stream(self, argv, **kwargs) -> CommandResult:  # type: ignore[override]
            return CommandResult(argv=tuple(argv), exit_code=0)

        def capture_to_file(self, argv, destination, **kwargs) -> CommandResult:  # type: ignore[override]
            return CommandResult(argv=tuple(argv), exit_code=0)

    with pytest.raises(CommandError):
        Minimal().start(["ssh"])
