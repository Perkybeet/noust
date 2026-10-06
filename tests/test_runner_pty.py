"""
Tests for interactive terminals: :meth:`CommandRunner.pty` and its handles.

A terminal is the one thing Noust runs that a person types into, so what makes
it safe is not the command (the person chooses it) but what surrounds it: no
shell between Noust and the program, a deadline and an idle limit that the
runner itself enforces, a process group that dies with the session, and the
same sandbox a build gets. These tests pin each of them against the real
runner (a throwaway ``sh`` on a real pseudo-terminal) and against the fake
every other test uses.
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
    PTY_END_CLOSED,
    PTY_END_EXITED,
    PTY_END_IDLE,
    PTY_END_LIMIT,
    CommandError,
    CommandRunner,
    DryRunRunner,
    FakePty,
    FakeRunner,
    SandboxSpec,
    SubprocessRunner,
    terminate_all_processes,
)


class Clock:
    """A clock a test moves by hand, for the limits the handles enforce."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        """
        Args:
            seconds: How far to move it.
        """
        self.now += seconds


def _read_until(handle, needle: bytes, timeout: float = 5.0) -> bytes:
    """
    Read from a handle until some bytes appear, or it ends.

    Args:
        handle: The terminal.
        needle: What to wait for.
        timeout: Seconds to wait at most.

    Returns:
        Everything read.
    """
    seen = b""
    deadline = time.monotonic() + timeout
    while needle not in seen and time.monotonic() < deadline:
        chunk = handle.read(0.2)
        if chunk is None:
            break
        seen += chunk
    return seen


def _drain(handle, timeout: float = 5.0) -> bytes:
    """
    Read a handle to its end.

    Args:
        handle: The terminal.
        timeout: Seconds to wait at most.

    Returns:
        Everything it wrote before ending.
    """
    seen = b""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        chunk = handle.read(0.2)
        if chunk is None:
            return seen
        seen += chunk
    raise AssertionError(f"the terminal did not end within {timeout}s; read {seen!r}")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            return handle.read().split(")")[-1].split()[0] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False


LIMITS = {"max_seconds": 30, "idle_seconds": 30}


@pytest.mark.allow_subprocess
class TestSubprocessPty:
    def test_what_a_person_types_reaches_the_program_and_its_answer_comes_back(self):
        handle = SubprocessRunner().pty(["cat"], **LIMITS)
        try:
            handle.write(b"hello\n")
            seen = _read_until(handle, b"hello\r\nhello")
            # The terminal echoes the typed line, then cat writes it again.
            assert b"hello\r\nhello" in seen
            assert handle.is_alive()
        finally:
            handle.terminate()
            handle.close()

    def test_the_program_sees_a_terminal_of_the_size_asked_for(self):
        handle = SubprocessRunner().pty(["stty", "size"], rows=31, cols=97, **LIMITS)
        try:
            assert b"31 97" in _drain(handle)
        finally:
            handle.close()

    def test_a_resize_reaches_the_program_that_is_running(self):
        handle = SubprocessRunner().pty(["sh", "-c", "read x; stty size"], **LIMITS)
        try:
            handle.resize(40, 120)
            handle.write(b"\n")
            assert b"40 120" in _drain(handle)
        finally:
            handle.close()

    def test_the_program_is_a_session_leader_with_the_terminal_as_its_own(self):
        # Job control and Ctrl+C need a controlling terminal: without one a
        # shell prints "no job control" and a typed ^C is only a character.
        handle = SubprocessRunner().pty(["sh", "-c", "ps -o tty= -p $$"], **LIMITS)
        try:
            output = _drain(handle).decode()
            assert "pts/" in output, output
        finally:
            handle.close()

    def test_ctrl_c_interrupts_what_the_person_started(self):
        handle = SubprocessRunner().pty(["sh", "-c", "sleep 30; echo unreachable"], **LIMITS)
        try:
            time.sleep(0.3)
            handle.write(b"\x03")
            output = _drain(handle)
            assert b"unreachable" not in output
            assert not handle.is_alive()
        finally:
            handle.close()

    def test_a_program_that_exits_ends_the_session_with_its_status(self):
        handle = SubprocessRunner().pty(["sh", "-c", "echo bye; exit 7"], **LIMITS)
        try:
            assert b"bye" in _drain(handle)
            assert handle.exit_code == 7
            assert handle.end_reason == PTY_END_EXITED
            assert not handle.is_alive()
        finally:
            handle.close()

    def test_the_session_has_a_deadline_the_runner_enforces(self):
        handle = SubprocessRunner().pty(["sleep", "30"], max_seconds=1, idle_seconds=30)
        try:
            started = time.monotonic()
            _drain(handle, timeout=10)
            assert time.monotonic() - started < 6
            assert handle.end_reason == PTY_END_LIMIT
            assert not _pid_alive(handle.pid)
        finally:
            handle.close()

    def test_a_session_nobody_types_into_ends_when_it_has_been_idle_too_long(self):
        handle = SubprocessRunner().pty(["sleep", "30"], max_seconds=30, idle_seconds=1)
        try:
            _drain(handle, timeout=10)
            assert handle.end_reason == PTY_END_IDLE
            assert not _pid_alive(handle.pid)
        finally:
            handle.close()

    def test_typing_keeps_a_session_from_going_idle(self):
        handle = SubprocessRunner().pty(["cat"], max_seconds=30, idle_seconds=2)
        try:
            for _ in range(4):
                time.sleep(0.8)
                handle.write(b"x")
                _read_until(handle, b"x", timeout=1)
            assert handle.is_alive()
            assert handle.end_reason is None
        finally:
            handle.terminate()
            handle.close()

    def test_terminating_stops_everything_the_program_started(self):
        handle = SubprocessRunner().pty(["sh", "-c", "sleep 30 & echo child:$!; wait"], **LIMITS)
        try:
            seen = _read_until(handle, b"child:")
            child = int(seen.split(b"child:")[1].split()[0])
            assert _pid_alive(child)
            handle.terminate()
            deadline = time.monotonic() + 5
            while _pid_alive(child) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert not _pid_alive(child), "the program's children outlived the terminal"
            assert handle.end_reason == PTY_END_CLOSED
        finally:
            handle.close()

    def test_the_program_gets_the_environment_it_is_given_and_not_this_processs(self, monkeypatch):
        monkeypatch.setenv("NOUST_TEST_SECRET_TOKEN", "do-not-leak-me")
        handle = SubprocessRunner().pty(
            ["sh", "-c", 'echo "[$NOUST_TEST_SECRET_TOKEN][$GREETING]"'],
            env={"GREETING": "hola"},
            **LIMITS,
        )
        try:
            assert b"[][hola]" in _drain(handle)
        finally:
            handle.close()

    def test_the_working_directory_is_honoured(self, tmp_path: Path):
        handle = SubprocessRunner().pty(["pwd"], cwd=tmp_path, **LIMITS)
        try:
            assert str(tmp_path.resolve()).encode() in _drain(handle)
        finally:
            handle.close()

    def test_a_program_that_does_not_exist_is_an_ended_session_not_an_exception(self):
        handle = SubprocessRunner().pty(["/nonexistent/program-for-noust"], **LIMITS)
        assert not handle.is_alive()
        assert handle.exit_code == 127
        assert b"Command not found" in (handle.read(0.1) or b"")
        assert handle.read(0.1) is None

    def test_what_is_written_to_a_closed_terminal_is_dropped_not_raised(self):
        handle = SubprocessRunner().pty(["true"], **LIMITS)
        _drain(handle)
        handle.close()
        handle.write(b"late input")  # must not raise
        handle.resize(10, 10)

    def test_a_terminal_still_open_at_interpreter_exit_is_stopped(self):
        handle = SubprocessRunner().pty(["sleep", "30"], **LIMITS)
        pid = handle.pid
        assert _pid_alive(pid)
        terminate_all_processes(timeout=2)
        assert not _pid_alive(pid)
        handle.close()

    def test_the_command_line_the_hooks_hear_has_no_secret_in_it(self):
        heard: list[tuple[str, ...]] = []
        runner = SubprocessRunner(on_command=heard.append)
        handle = runner.pty(["echo", "token=s3cr3t-value"], secrets=["s3cr3t-value"], **LIMITS)
        try:
            _drain(handle)
        finally:
            handle.close()
        assert heard and all("s3cr3t-value" not in " ".join(call) for call in heard)

    def test_argv_is_validated_like_every_other_command(self):
        with pytest.raises(ValueError, match="string"):
            SubprocessRunner().pty("ls -l", **LIMITS)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="empty"):
            SubprocessRunner().pty([], **LIMITS)
        with pytest.raises(ValueError, match="NUL"):
            SubprocessRunner().pty(["ls", "a\x00b"], **LIMITS)


class TestMandatoryLimits:
    @pytest.mark.parametrize(
        "limits", [{"max_seconds": 0}, {"idle_seconds": 0}, {"max_seconds": -5}]
    )
    def test_a_terminal_without_a_deadline_and_an_idle_limit_is_refused(self, limits):
        merged = {**LIMITS, **limits}
        for runner in (SubprocessRunner(), FakeRunner(), DryRunRunner(FakeRunner())):
            with pytest.raises(ValueError, match="positive"):
                runner.pty(["sh"], **merged)

    def test_the_limits_have_no_default(self):
        with pytest.raises(TypeError):
            FakeRunner().pty(["sh"])  # type: ignore[call-arg]

    def test_a_size_the_kernel_would_misread_is_clamped(self):
        handle = FakeRunner().pty(["sh"], rows=0, cols=100000, **LIMITS)
        assert 1 <= handle.rows <= runner_module.PTY_MAX_ROWS
        assert 1 <= handle.cols <= runner_module.PTY_MAX_COLS


class TestBaseRunner:
    def test_a_runner_that_cannot_open_terminals_says_so(self):
        class Bare(CommandRunner):
            def run(self, *a, **k):  # type: ignore[override]
                raise NotImplementedError

            def stream(self, *a, **k):  # type: ignore[override]
                raise NotImplementedError

            def capture_to_file(self, *a, **k):  # type: ignore[override]
                raise NotImplementedError

        with pytest.raises(CommandError, match="cannot open terminals"):
            Bare().pty(["sh"], **LIMITS)


class TestDryRun:
    def test_a_rehearsal_never_opens_a_terminal(self):
        inner = FakeRunner()
        dry = DryRunRunner(inner)
        handle = dry.pty(["bash"], **LIMITS)
        assert not handle.is_alive()
        assert b"dry run" in (handle.read(0.1) or b"")
        assert handle.read(0.1) is None
        assert inner.ptys == []
        assert dry.skipped == [("bash",)]


class TestFakeRunner:
    def test_what_the_code_under_test_asked_for_is_recorded_exactly(self):
        runner = FakeRunner()
        spec = SandboxSpec(user="noust-app-shop", interactive=True, name="shop-terminal")
        handle = runner.pty(
            ["/bin/bash", "--norc"],
            cwd=Path("/srv/shop"),
            env={"TERM": "xterm-256color"},
            sandbox=spec,
            rows=30,
            cols=100,
            max_seconds=600,
            idle_seconds=120,
        )
        assert runner.ptys == [handle]
        assert runner.sandboxed[-1][0] == ("/bin/bash", "--norc")
        assert runner.sandboxed[-1][1] is spec
        # The recorded call is the systemd-run the real runner would execute,
        # with the terminal flag and without the build compatibility mode.
        call = runner.calls[-1]
        assert call[0] == "systemd-run" and "--pty" in call
        assert runner.envs[-1]["TERM"] == "xterm-256color"
        assert "NO_COLOR" not in runner.envs[-1] and "CI" not in runner.envs[-1]
        assert (handle.rows, handle.cols) == (30, 100)
        assert (handle.max_seconds, handle.idle_seconds) == (600, 120)

    def test_account_and_sandbox_are_exclusive_like_everywhere_else(self):
        with pytest.raises(ValueError, match="exclusive"):
            FakeRunner().pty(
                ["sh"], user="x", sandbox=SandboxSpec(user="y", interactive=True), **LIMITS
            )

    def test_a_scripted_failure_starts_dead_with_its_words_on_the_terminal(self):
        runner = FakeRunner().script(["docker", "exec"], exit_code=1, stderr="no such container")
        handle = runner.pty(["docker", "exec", "-it", "abc", "sh"], **LIMITS)
        assert not handle.is_alive()
        assert handle.exit_code == 1
        assert b"no such container" in _drain(handle)

    def test_the_test_drives_the_terminal(self):
        handle = FakeRunner().pty(["sh"], **LIMITS)
        handle.feed("prompt$ ")
        assert handle.read(0.1) == b"prompt$ "
        assert handle.read(0.01) == b""
        handle.write(b"ls\r")
        handle.resize(50, 60)
        assert handle.written == [b"ls\r"]
        assert handle.resizes == [(50, 60)]
        handle.die(3)
        assert handle.read(0.1) is None
        assert handle.exit_code == 3
        assert handle.end_reason == PTY_END_EXITED

    def test_the_fake_enforces_the_same_limits_as_the_real_one(self):
        clock = Clock()
        handle = FakePty(["sh"], max_seconds=60, idle_seconds=10, rows=24, cols=80, clock=clock)
        clock.advance(9)
        assert handle.read(0.01) == b""
        handle.write(b"x")
        clock.advance(9)
        assert handle.read(0.01) == b""
        clock.advance(2)
        assert handle.read(0.01) is None
        assert handle.end_reason == PTY_END_IDLE

        again = FakePty(["sh"], max_seconds=60, idle_seconds=40, rows=24, cols=80, clock=clock)
        for _ in range(4):
            clock.advance(19)
            again.write(b"x")
        assert again.read(0.01) is None
        assert again.end_reason == PTY_END_LIMIT
        assert again.exit_code is not None

    def test_the_remaining_time_is_what_a_header_shows(self):
        clock = Clock()
        handle = FakePty(["sh"], max_seconds=100, idle_seconds=30, rows=24, cols=80, clock=clock)
        clock.advance(10)
        assert handle.limit_remaining() == pytest.approx(90)
        assert handle.idle_remaining() == pytest.approx(20)
        handle.write(b"a")
        assert handle.idle_remaining() == pytest.approx(30)

    def test_terminating_ends_it_as_closed(self):
        handle = FakeRunner().pty(["sh"], **LIMITS)
        assert handle.terminate() == -signal.SIGHUP
        assert handle.end_reason == PTY_END_CLOSED
        assert handle.read(0.01) is None

    def test_the_echo_shell_makes_a_terminal_a_person_can_try(self):
        runner = FakeRunner()
        runner.pty_echo = True
        handle = runner.pty(["sh"], **LIMITS)
        assert b"$ " in _read_until(handle, b"$ ")
        handle.write(b"hi")
        assert b"hi" in _read_until(handle, b"hi")
        handle.write(b"\r")
        assert b"\r\n" in _read_until(handle, b"\r\n")
        handle.write(b"exit\r")
        _drain(handle)
        assert handle.end_reason == PTY_END_EXITED


class TestSandboxedTerminal:
    def test_an_interactive_spec_runs_on_a_terminal_without_the_build_compatibility_mode(self):
        spec = SandboxSpec(user="noust-app-shop", interactive=True)
        argv = runner_module.sandbox_prefix(
            spec, unit="noust-build-shop-1", env_file=Path("/run/noust/sandbox/x.env")
        )
        assert "--pty" in argv and "--pipe" not in argv
        env = runner_module.sandbox_environment(spec, {"TERM": "xterm-256color"}, {})
        assert env["TERM"] == "xterm-256color"
        assert "NO_COLOR" not in env and "CI" not in env

    def test_a_build_on_a_terminal_still_gets_the_compatibility_mode(self):
        spec = SandboxSpec(user="noust-build", pty=True)
        env = runner_module.sandbox_environment(spec, None, {})
        assert env["TERM"] == "dumb" and env["CI"] == "1" and env["NO_COLOR"] == "1"

    def test_the_deadline_of_the_session_is_the_units_own(self):
        spec = SandboxSpec(user="noust-app-shop", interactive=True)
        argv = runner_module.sandbox_prefix(
            spec, unit="u", env_file=Path("/run/noust/sandbox/x.env"), timeout=600
        )
        assert f"--property=RuntimeMaxSec={600 + runner_module.SANDBOX_TIMEOUT_MARGIN}" in argv


def test_the_only_module_that_opens_a_terminal_is_the_runner():
    # Rule 1: the PTY is a way of running a process, so it lives where the
    # others do. tests/test_architecture.py holds the whole tree to it.
    assert runner_module.__file__ is not None
    source = Path(runner_module.__file__).read_text(encoding="utf-8")
    assert "os.openpty" in source
    assert sys.modules["noust.core.runner"] is runner_module
