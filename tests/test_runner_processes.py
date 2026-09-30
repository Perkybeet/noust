"""
Tests for long-lived processes: :meth:`CommandRunner.start` and its handles.

A tunnel is the one command Noust runs that is meant to keep running. What
replaces the mandatory deadline is ownership: the handle stops the process and
everything it started, and interpreter shutdown stops whatever is left.
"""

from __future__ import annotations

import os
import signal
import socket
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


def _open_files(pid: int) -> set[str]:
    """
    What a process holds open, by what each descriptor points at.

    A child still starting closes descriptors while they are listed (CI saw
    one vanish between iterdir and readlink); those are skipped.
    """
    held: set[str] = set()
    for fd in Path(f"/proc/{pid}/fd").iterdir():
        try:
            held.add(os.readlink(fd))
        except FileNotFoundError:
            continue
    return held


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

    @pytest.mark.allow_sockets
    def test_a_started_process_never_holds_the_consoles_listening_socket(self):
        """
        A tunnel outliving the console must not keep its port bound.

        The socket is made inheritable on purpose, as systemd socket activation
        or a library could leave it: the runner has to close it in the child
        whatever the flag says.
        """
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.set_inheritable(True)
        port = listener.getsockname()[1]
        inode = os.fstat(listener.fileno()).st_ino
        handle = SubprocessRunner().start([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            held = _open_files(handle.pid)
            assert f"socket:[{inode}]" not in held

            listener.close()
            # The child still runs, and the port is free for the next console.
            assert handle.is_alive()
            again = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            again.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                again.bind(("127.0.0.1", port))
                again.listen()
            finally:
                again.close()
        finally:
            listener.close()
            handle.terminate(timeout=5)

    def test_shutdown_stops_every_process_within_one_deadline(self):
        """
        Processes that ignore SIGTERM are killed together, not one deadline each:
        a console with a dozen tunnels must still stop before systemd gives up.
        """
        ignore = (
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
        )
        handles = [SubprocessRunner().start([sys.executable, "-c", ignore]) for _ in range(4)]
        # Let each install its handler before it is signalled.
        time.sleep(0.5)
        started = time.monotonic()

        terminate_all_processes(timeout=1.0)

        elapsed = time.monotonic() - started
        assert all(not handle.is_alive() for handle in handles)
        assert elapsed < 3.0, f"took {elapsed:.1f}s: one deadline per process"

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
