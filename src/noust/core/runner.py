# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The single seam through which Noust executes external processes.

Every call to nginx, systemctl, certbot, git, npm, mysqldump and friends goes
through a ``CommandRunner``. Nothing else in the codebase may import
``subprocess`` directly; a test fixture enforces that rule by making real
process execution fail.

Three properties are non-negotiable here, because each one maps to a defect
class that this module exists to make impossible:

- **Argv only, never a shell.** Commands are sequences of arguments. There is
  no ``shell=`` parameter and no string splitting, so a domain name or a
  database dump can never be reinterpreted as shell syntax.
- **Timeouts are mandatory.** Every call has a deadline. A hung ``git clone``
  or ``certbot`` blocks a deploy, not the process forever.
- **Secrets never travel in argv.** Anything on a command line is visible in
  ``ps`` to every user on the box. Passwords go through ``env`` or ``stdin``,
  and ``redact`` keeps them out of the logs.

An operation can also be cancelled while its commands run: see
:func:`cancellable`.

Two more things live here because they are properties of executing a process,
not of any one caller:

- **Sandboxed builds.** ``run`` and ``stream`` take ``sandbox=``, a
  :class:`SandboxSpec`, and then execute the command in a transient systemd
  unit (``systemd-run --wait --collect``) as another account, with the
  filesystem mostly read-only, the configuration and the store hidden and a
  clean environment. :func:`sandbox_prefix` is the one place that argv is
  assembled, and :class:`FakeRunner` records exactly what the real runner
  would execute. The policy of *who* gets a sandbox is not here: see
  :mod:`noust.deployers.helpers.sandbox`.
- **Interactive terminals.** :meth:`CommandRunner.pty` runs a program on a
  pseudo-terminal a person types into (the console's terminal). It is the same
  rules with one more property: the session itself has a deadline and an idle
  limit that this module enforces, because a terminal nobody is watching is the
  one process that must not outlive its welcome. The terminal is opened here
  (``os.openpty``) and nowhere else, with the program in a session of its own
  that dies with the handle, and it takes the same ``sandbox=`` as a build.
- **What only looks.** :func:`is_read_only` decides which commands a
  ``--dry-run`` may still execute. A command counts only when its exact argv
  shape is declared, never because one of its arguments looks like a status
  word (``ufw allow 22 comment status`` is not a status).

How a module declares its read-only probes
------------------------------------------

A probe is declared as a tuple of arguments, program first. Every element is
compared literally, except that an element containing ``*`` is a glob (a bare
``*`` is an operand and never matches an option, that is an argument starting
with ``-``), and a trailing ``...`` means "and any further arguments". Shapes
Noust's core runs are in :data:`READ_ONLY_PROBES` below. A module that runs
probes of its own keeps them next to its code, in a module-level
``READ_ONLY_PROBES`` tuple written in the same language, and its dotted name is
added to :data:`PROBE_MODULES`; the runner imports those modules the first time
it classifies a command. :data:`MUTATING_OPTIONS` vetoes, for every shape, the
options that turn a probe into a change (``journalctl --vacuum-size``), so an
open-ended declaration cannot let one through.
"""

from __future__ import annotations

import atexit
import contextlib
import contextvars
import errno
import fcntl
import fnmatch
import gzip
import importlib
import logging
import os
import queue
import re
import secrets as _secrets
import select
import shutil
import signal
import struct
import subprocess
import termios
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import IO, Any, Literal

from noust.core import paths
from noust.core.exceptions import ConfigError, NoustError

_log = logging.getLogger(__name__)

#: Deadline applied when a caller does not pass one. Chosen to be comfortably
#: longer than any system query (systemctl, nginx -t) and shorter than any
#: operation a user would expect to block on.
DEFAULT_TIMEOUT = 60

#: Exit code convention for a command that never completed.
EXIT_TIMEOUT = -1

#: Exit code POSIX shells use for "command not found".
EXIT_NOT_FOUND = 127

#: How often a command running inside a cancel scope looks at the scope's
#: event. Short enough that a cancelled clone stops before the operator has
#: finished reading the button they pressed.
CANCEL_POLL_INTERVAL = 0.1

_REDACTED = "***"


class CommandError(NoustError):
    """A command failed and the caller asked for failures to be fatal."""


class CommandCancelled(NoustError):
    """A command was stopped, or never started, because its operation was cancelled."""


#: The event of the innermost :func:`cancellable` block, per thread (and per
#: task). A context variable rather than a ``cancel=`` argument on every
#: method: cancellation belongs to an operation, not to one call, and every
#: command the operation runs has to stop, however many managers deep it is
#: issued. Threading an argument through each of them is a rule with as many
#: holes as there are callers.
_cancel_event: contextvars.ContextVar[threading.Event | None] = contextvars.ContextVar(
    "noust_cancel_event", default=None
)


@contextlib.contextmanager
def cancellable(event: threading.Event) -> Iterator[threading.Event]:
    """
    Make every command run inside the block stop when ``event`` is set.

    A command that would start after the event is set raises
    :class:`CommandCancelled` without starting; one that is running is killed
    together with every process it started (it runs in its own session, so
    ``git clone``'s ``git-remote-https`` and ``index-pack`` go with it), then
    :class:`CommandCancelled` is raised. The event is set from another thread,
    typically the web request whose client went away.

    Commands outside a scope run exactly as before, in the caller's session,
    so a Ctrl+C at a terminal still reaches them.

    Args:
        event: Set it to cancel.

    Yields:
        The same event.
    """
    token = _cancel_event.set(event)
    try:
        yield event
    finally:
        _cancel_event.reset(token)


def _cancel_scope(redacted: Sequence[str]) -> threading.Event | None:
    """
    Return the active cancel event, refusing to start once it is set.

    Args:
        redacted: The command about to start, for the error message.

    Returns:
        The event of the enclosing :func:`cancellable` block, or None.

    Raises:
        CommandCancelled: When the operation was already cancelled.
    """
    event = _cancel_event.get()
    if event is not None and event.is_set():
        raise CommandCancelled(f"Cancelled before it started: {' '.join(redacted)}")
    return event


def _kill_session(process: subprocess.Popen, *, drain: bool = True) -> None:
    """
    Kill a process started in its own session, and everything it started.

    Args:
        process: A process started with ``start_new_session=True``, so its
            pid is also its process group id.
        drain: Read the pipes to their end while reaping. False when another
            thread already reads them (:meth:`SubprocessRunner.stream`).
    """
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    try:
        # Reaps the child; draining also closes the pipes, which reach their
        # end once every member of the group is dead.
        if drain:
            process.communicate(timeout=5)
        else:
            process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        # Something left the group (setsid) and holds the pipes. The child
        # itself is dead; the pipes close when the object is collected. It
        # still has to be reaped, or it stays a zombie for as long as this
        # process lives: wait() only waits for the exit, not for the pipes.
        process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)


@dataclass(frozen=True)
class CommandResult:
    """
    Outcome of a single external process execution.

    Attributes:
        argv: The exact argument vector that was executed, already redacted.
        exit_code: Process exit status. Negative values mean the process was
            killed by a signal, except EXIT_TIMEOUT which means it never
            finished.
        stdout: Captured standard output, or empty when streaming.
        stderr: Captured standard error, or empty when streaming.
        duration: Wall-clock seconds the process ran.
        timed_out: True when the deadline was hit.
        sandbox_unit: The transient unit a sandboxed command ran in, without
            ``.service``; None for a command that ran directly.
        sandbox_result: systemd's ``Result`` for that unit (``success``,
            ``exit-code``, ``timeout``, ``oom-kill``, ``signal``...), when it
            was learned; None otherwise. A kill by the deadline or by the
            memory limit shows here, because ``systemd-run`` itself exits 1
            for both.
    """

    argv: tuple[str, ...]
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0
    timed_out: bool = False
    sandbox_unit: str | None = None
    sandbox_result: str | None = None

    @property
    def out_of_memory(self) -> bool:
        """True when the kernel killed it for exceeding its memory limit."""
        return self.sandbox_result == "oom-kill"

    @property
    def success(self) -> bool:
        """True when the process exited cleanly."""
        return self.exit_code == 0

    @property
    def command(self) -> str:
        """The redacted command line, for logs and error messages."""
        return " ".join(self.argv)

    @property
    def output(self) -> str:
        """Stdout with surrounding whitespace removed."""
        return self.stdout.strip()

    def __bool__(self) -> bool:
        """Allow ``if runner.run(...):`` to read naturally."""
        return self.success

    def check(self) -> CommandResult:
        """
        Return self, raising if the command failed.

        Returns:
            This result, so the call can be chained.

        Raises:
            CommandError: When the exit code is non-zero.
        """
        if self.success:
            return self
        raise CommandError(
            f"Command failed with exit code {self.exit_code}: {self.command}",
            details=(self.stderr or self.stdout).strip(),
        )


@dataclass(frozen=True)
class CommandExecution:
    """
    One process a runner executed, as reported to the execution listeners.

    It is what the host action ledger records (see
    :func:`add_execution_listener`), so it carries what the argv alone cannot:
    where it ran, as whom, whether it only looked, how it ended, and the
    operation it belonged to.

    Attributes:
        argv: The command as the caller gave it, redacted. For a sandboxed
            command this is the command itself, not the ``systemd-run``
            wrapping, whose unit is in ``sandbox_unit``.
        cwd: Its working directory, or None for the caller's.
        read_only: Whether it only observes (:func:`is_read_only`); a
            sandboxed command never does.
        result: How it ended; None for a long-lived process just started.
        correlation_id: The id of the operation that ran it (a console
            request, a CLI command), when one is bound.
        user: The account it ran as, when not the runner's own.
        sandbox_unit: The transient unit a sandboxed command ran in.
    """

    argv: tuple[str, ...]
    cwd: Path | None
    read_only: bool
    result: CommandResult | None
    correlation_id: str | None = None
    user: str | None = None
    sandbox_unit: str | None = None

    @property
    def exit_code(self) -> int | None:
        """How the process ended, or None while it runs."""
        return self.result.exit_code if self.result is not None else None

    @property
    def duration(self) -> float | None:
        """Seconds it ran, or None while it runs."""
        return self.result.duration if self.result is not None else None


#: Called once per execution, by every runner. Module-level rather than per
#: runner: --dry-run and the tests replace the runner itself, and an audit
#: ledger that silently stopped hearing about commands when that happened
#: would be worse than none.
_execution_listeners: list[Callable[[CommandExecution], None]] = []
_listeners_lock = threading.Lock()


def add_execution_listener(listener: Callable[[CommandExecution], None]) -> None:
    """
    Call ``listener`` with a :class:`CommandExecution` after every command any runner executes.

    Rehearsed commands (``--dry-run``) are not executed and not reported. A
    listener that raises is logged and does not affect the command, which has
    already run: an audit ledger that fails must say so on its own, not turn a
    successful ``systemctl restart`` into a reported failure.

    Registering the same listener twice has no effect.

    Args:
        listener: Called with one positional argument, the execution.
    """
    with _listeners_lock:
        if listener not in _execution_listeners:
            _execution_listeners.append(listener)


def remove_execution_listener(listener: Callable[[CommandExecution], None]) -> None:
    """
    Stop calling a listener added with :func:`add_execution_listener`.

    Args:
        listener: The listener; one that was never added is ignored.
    """
    with _listeners_lock:
        if listener in _execution_listeners:
            _execution_listeners.remove(listener)


def _correlation_id() -> str | None:
    """
    Return the id of the operation this command belongs to, if one is bound.

    The id is the audit's (``noust.core.audit.context``), imported at call
    time: the runner is the lowest layer and must import without it.

    Returns:
        The bound correlation id, or None.
    """
    try:
        from noust.core.audit.context import current_correlation_id
    except ImportError:
        return None
    return current_correlation_id()


def _notify_execution(
    argv: Sequence[str],
    *,
    cwd: Path | None,
    result: CommandResult | None,
    user: str | None = None,
    sandbox_unit: str | None = None,
) -> None:
    """
    Report one execution to every listener.

    Args:
        argv: The redacted command.
        cwd: Its working directory.
        result: How it ended, or None for a started long-lived process.
        user: The account it ran as, when another.
        sandbox_unit: The unit a sandboxed command ran in.
    """
    with _listeners_lock:
        listeners = tuple(_execution_listeners)
    if not listeners:
        return
    event = CommandExecution(
        argv=tuple(argv),
        cwd=cwd,
        read_only=sandbox_unit is None and is_read_only(argv),
        result=result,
        correlation_id=_correlation_id(),
        user=user,
        sandbox_unit=sandbox_unit,
    )
    for listener in listeners:
        # An error boundary: the command already ran, and a listener that
        # fails must not make its caller believe the command did.
        try:
            listener(event)
        except Exception:
            _log.exception("An execution listener failed for %s", " ".join(event.argv))


#: Lines of a long-lived process's standard error kept for diagnosis. An ssh
#: tunnel that dies says why in its last few lines; a process that logs
#: forever must not grow this process's memory with it.
STDERR_TAIL_LINES = 50

#: How long :meth:`ProcessHandle.terminate` waits after SIGTERM before SIGKILL.
TERMINATE_TIMEOUT = 5.0


class ProcessHandle(ABC):
    """
    A long-lived process started by :meth:`CommandRunner.start`.

    A tunnel, unlike every other command Noust runs, is meant to keep running:
    it has no deadline, only an owner that stops it. The handle is how the
    owner watches it and stops it, together with everything it started.

    Attributes:
        argv: The redacted argument vector, for logs and messages.
        started_at: ``time.time()`` when the process was started.
    """

    argv: tuple[str, ...]
    started_at: float

    @property
    @abstractmethod
    def pid(self) -> int:
        """The process id, which is also its process group id; 0 when it never started."""

    @property
    @abstractmethod
    def exit_code(self) -> int | None:
        """The exit status once the process ended, None while it runs."""

    @abstractmethod
    def is_alive(self) -> bool:
        """
        Report whether the process is still running.

        Returns:
            True until it exits or is terminated.
        """

    @abstractmethod
    def stderr_tail(self) -> str:
        """
        Return the last lines the process wrote to standard error.

        Returns:
            Up to :data:`STDERR_TAIL_LINES` lines, verbatim, newline-joined.
        """

    @abstractmethod
    def terminate(self, timeout: float = TERMINATE_TIMEOUT) -> int | None:
        """
        Stop the process and every process in its group.

        SIGTERM first, SIGKILL when it has not exited after ``timeout``.
        Stopping a process that already ended is a no-op.

        Args:
            timeout: Seconds to wait between the two signals.

        Returns:
            The exit status, or None when it could not be reaped.
        """


#: Every real long-lived process still running, so interpreter shutdown can
#: stop them. A tunnel left behind by a console that exited would keep a port
#: forwarded, and an SSH session open, for nobody.
_live_processes: set[_SubprocessHandle | _SubprocessPty] = set()
_live_lock = threading.Lock()


def terminate_all_processes(timeout: float = TERMINATE_TIMEOUT) -> None:
    """
    Stop every long-lived process this interpreter started and still owns.

    Every group is sent SIGTERM at once and given one shared deadline, then
    whatever is left is killed: one deadline per process would let a console
    with a dozen tunnels outlast systemd's stop timeout and be killed with
    its children still running.

    Registered with :mod:`atexit`; also safe to call from a server's shutdown
    hook, and more than once.

    Args:
        timeout: Seconds every process together gets to exit after SIGTERM.
    """
    with _live_lock:
        handles = list(_live_processes)
    for handle in handles:
        handle.signal_group(signal.SIGTERM)
    deadline = time.monotonic() + timeout
    for handle in handles:
        handle.wait_until(deadline)
    for handle in handles:
        if handle.exit_code is None:
            handle.signal_group(signal.SIGKILL)
    for handle in handles:
        # Reaps, drains standard error and forgets it; quick for every process
        # that has been sent SIGKILL.
        handle.terminate(timeout=timeout)


atexit.register(terminate_all_processes)


class _SubprocessHandle(ProcessHandle):
    """A real long-lived process, in its own session."""

    def __init__(self, process: subprocess.Popen[str], argv: tuple[str, ...]) -> None:
        """
        Args:
            process: The started process, with standard error piped.
            argv: The redacted argument vector.
        """
        self._process = process
        self.argv = argv
        self.started_at = time.time()
        self._tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
        self._tail_lock = threading.Lock()
        self._reader = threading.Thread(
            target=self._pump, name=f"stderr-{process.pid}", daemon=True
        )
        self._reader.start()
        with _live_lock:
            _live_processes.add(self)

    def _pump(self) -> None:
        """Read standard error to its end, keeping only the tail."""
        stream = self._process.stderr
        if stream is None:
            return
        for raw in stream:
            with self._tail_lock:
                self._tail.append(raw.rstrip("\n"))

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def exit_code(self) -> int | None:
        return self._process.poll()

    def is_alive(self) -> bool:
        alive = self._process.poll() is None
        if not alive:
            with _live_lock:
                _live_processes.discard(self)
        return alive

    def signal_group(self, number: int) -> None:
        """
        Send a signal to the process's group while its leader runs.

        Args:
            number: The signal.
        """
        if self._process.poll() is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self._process.pid, number)

    def wait_until(self, deadline: float) -> None:
        """
        Wait for the process to exit, at most until a monotonic deadline.

        Args:
            deadline: ``time.monotonic()`` value to give up at.
        """
        with contextlib.suppress(subprocess.TimeoutExpired):
            self._process.wait(timeout=max(0.0, deadline - time.monotonic()))

    def stderr_tail(self) -> str:
        if self._process.poll() is not None:
            # The pipe reaches its end when the process dies; give the reader
            # a moment to take the last words, which are the ones that matter.
            self._reader.join(timeout=1)
        with self._tail_lock:
            return "\n".join(self._tail)

    def terminate(self, timeout: float = TERMINATE_TIMEOUT) -> int | None:
        try:
            if self._process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self._process.pid, signal.SIGTERM)
                try:
                    self._process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(self._process.pid, signal.SIGKILL)
                    try:
                        self._process.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        return None
            else:
                # The leader is gone; anything it left in its group is not.
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(self._process.pid, signal.SIGKILL)
            self._reader.join(timeout=1)
            return self._process.returncode
        finally:
            with _live_lock:
                _live_processes.discard(self)


class _EndedProcess(ProcessHandle):
    """A process that never ran: not found, or not started by a rehearsal."""

    def __init__(self, argv: tuple[str, ...], exit_code: int, stderr: str) -> None:
        """
        Args:
            argv: The redacted argument vector.
            exit_code: The status to report.
            stderr: Why it never ran.
        """
        self.argv = argv
        self.started_at = time.time()
        self._exit_code = exit_code
        self._stderr = stderr

    @property
    def pid(self) -> int:
        return 0

    @property
    def exit_code(self) -> int | None:
        return self._exit_code

    def is_alive(self) -> bool:
        return False

    def stderr_tail(self) -> str:
        return self._stderr

    def terminate(self, timeout: float = TERMINATE_TIMEOUT) -> int | None:
        return self._exit_code


class FakeProcess(ProcessHandle):
    """
    A long-lived process for tests, started by :meth:`FakeRunner.start`.

    It runs until the test calls :meth:`die` or the code under test calls
    :meth:`terminate`.
    """

    _next_pid = 40000

    def __init__(
        self, argv: tuple[str, ...], *, alive: bool, exit_code: int | None, stderr: str
    ) -> None:
        """
        Args:
            argv: The argument vector the code under test built.
            alive: Whether it starts running.
            exit_code: Its status when it starts dead.
            stderr: What it wrote to standard error.
        """
        FakeProcess._next_pid += 1
        self._pid = FakeProcess._next_pid
        self.argv = argv
        self.started_at = time.time()
        self._alive = alive
        self._exit_code = None if alive else exit_code
        self._stderr = stderr
        self.terminated = False

    @property
    def pid(self) -> int:
        return self._pid

    @property
    def exit_code(self) -> int | None:
        return self._exit_code

    def is_alive(self) -> bool:
        return self._alive

    def stderr_tail(self) -> str:
        return self._stderr

    def die(self, exit_code: int = 255, stderr: str = "") -> None:
        """
        Make the process exit, as a dropped connection would.

        Args:
            exit_code: Its exit status.
            stderr: What it wrote on the way out.
        """
        self._alive = False
        self._exit_code = exit_code
        if stderr:
            self._stderr = stderr

    def terminate(self, timeout: float = TERMINATE_TIMEOUT) -> int | None:
        if self._alive:
            self._alive = False
            self._exit_code = -signal.SIGTERM
            self.terminated = True
        return self._exit_code


# -- Interactive terminals -----------------------------------------------------

#: Why a terminal session ended; what :attr:`PtyHandle.end_reason` says.
PTY_END_EXITED = "exited"
PTY_END_IDLE = "idle"
PTY_END_LIMIT = "limit"
PTY_END_CLOSED = "closed"

#: The terminal a program starts with when the caller does not say.
PTY_DEFAULT_ROWS = 24
PTY_DEFAULT_COLS = 80

#: The largest terminal accepted. The kernel stores both in 16 bits, and a
#: browser window far past these is a client bug, not a screen.
PTY_MAX_ROWS = 500
PTY_MAX_COLS = 1000

#: Most bytes one :meth:`PtyHandle.read` returns.
PTY_READ_CHUNK = 65536

#: How long :meth:`PtyHandle.write` waits for a program that does not read its
#: input before it gives up: a stopped process must not freeze whoever types.
PTY_WRITE_TIMEOUT = 5.0

#: What a terminal program inherits of this process's environment: nothing of
#: Noust's. The console's own variables (a master token among them) never
#: reach a shell a person types into.
PTY_ENV_ALLOW: tuple[str, ...] = ("PATH", "LANG", "LC_*", "TZ", "TERM", "COLORTERM")

#: The program that gives a child its own session and makes the terminal its
#: controlling one (``-c``): without that a shell has no job control and a
#: typed Ctrl+C is only a character. Not ``preexec_fn``, which the subprocess
#: documentation warns can deadlock in a process with threads.
_SESSION_LEADER = ("setsid", "-c")


def _clamp(value: int, upper: int) -> int:
    """
    Keep a terminal dimension inside what the kernel stores.

    Args:
        value: What was asked.
        upper: The most allowed.

    Returns:
        A value from 1 to ``upper``.
    """
    return max(1, min(int(value), upper))


class PtyHandle(ABC):
    """
    A terminal session started by :meth:`CommandRunner.pty`.

    What a tunnel's :class:`ProcessHandle` is to ``start``, this is to ``pty``:
    the owner reads what the program writes, writes what the person types,
    resizes the screen and ends it. Two limits come with it and are enforced
    here, in one place, for the real runner and the fake alike (rule 3): a
    deadline for the whole session and a limit on how long nobody types. When
    either passes, the program and everything it started are stopped and
    :meth:`read` reports the end.

    Attributes:
        argv: The redacted argument vector, for logs and messages.
        started_at: ``time.time()`` when the session started.
        rows: The terminal's height now.
        cols: The terminal's width now.
    """

    argv: tuple[str, ...]
    started_at: float
    rows: int
    cols: int

    def __init__(
        self,
        argv: tuple[str, ...],
        *,
        max_seconds: int,
        idle_seconds: int,
        rows: int,
        cols: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            argv: The redacted argument vector.
            max_seconds: The longest the session may last.
            idle_seconds: The longest it may go without anything typed.
            rows: The terminal's height.
            cols: The terminal's width.
            clock: Monotonic seconds; a test passes its own.
        """
        self.argv = argv
        self.started_at = time.time()
        self.rows = _clamp(rows, PTY_MAX_ROWS)
        self.cols = _clamp(cols, PTY_MAX_COLS)
        self._clock = clock
        self._max_seconds = max_seconds
        self._idle_seconds = idle_seconds
        now = clock()
        self._limit_at = now + max_seconds
        self._last_input = now
        self._end_reason: str | None = None

    @property
    def max_seconds(self) -> int:
        """The longest the session may last, as it was asked."""
        return self._max_seconds

    @property
    def idle_seconds(self) -> int:
        """The longest it may go without anything typed, as it was asked."""
        return self._idle_seconds

    @property
    def end_reason(self) -> str | None:
        """Why the session ended (``exited``, ``idle``, ``limit`` or ``closed``); None while it runs."""
        return self._end_reason

    def limit_remaining(self) -> float:
        """
        Returns:
            Seconds until the session's deadline; 0 once it has passed.
        """
        return max(0.0, self._limit_at - self._clock())

    def idle_remaining(self) -> float:
        """
        Returns:
            Seconds until the idle limit, counted from the last thing typed.
        """
        return max(0.0, self._last_input + self._idle_seconds - self._clock())

    def touch(self) -> None:
        """Record that the person did something, which restarts the idle limit."""
        self._last_input = self._clock()

    def expired(self) -> str | None:
        """
        Say whether a limit has passed.

        Returns:
            :data:`PTY_END_LIMIT` or :data:`PTY_END_IDLE`, or None.
        """
        if self.limit_remaining() <= 0:
            return PTY_END_LIMIT
        if self.idle_remaining() <= 0:
            return PTY_END_IDLE
        return None

    @property
    @abstractmethod
    def pid(self) -> int:
        """The process id, which is also its process group id; 0 when it never started."""

    @property
    @abstractmethod
    def exit_code(self) -> int | None:
        """The exit status once the program ended, None while it runs."""

    @abstractmethod
    def is_alive(self) -> bool:
        """
        Returns:
            True until the program exits or is stopped.
        """

    @abstractmethod
    def read(self, timeout: float = 0.5) -> bytes | None:
        """
        Wait for what the program writes.

        Also where the limits are enforced: a session past one is stopped
        here, and the read that finds it says it ended.

        Args:
            timeout: Seconds to wait for something, at most.

        Returns:
            Bytes; empty when nothing came in ``timeout``; None once the
            session ended and everything it wrote was read.
        """

    @abstractmethod
    def write(self, data: bytes) -> None:
        """
        Type something. Restarts the idle limit; a closed terminal ignores it.

        Args:
            data: The bytes, as a keyboard would send them.

        Raises:
            CommandError: The program did not read its input for
                :data:`PTY_WRITE_TIMEOUT` seconds.
        """

    @abstractmethod
    def resize(self, rows: int, cols: int) -> None:
        """
        Change the terminal's size; the program is told (SIGWINCH).

        Args:
            rows: The new height.
            cols: The new width.
        """

    def input_is_hidden(self) -> bool:
        """
        Say whether what is typed now is a secret the program asked for.

        A prompt that reads a password (``sudo``, ``passwd``, ``ssh``,
        ``mysql -p``) switches the terminal's echo off while it still reads
        whole lines. A full-screen program (an editor, ``less``) switches echo
        off too, but also leaves line mode, so it is not mistaken for one.

        Returns:
            True while the terminal is in the state of a password prompt.
        """
        return False

    @abstractmethod
    def terminate(self, timeout: float = TERMINATE_TIMEOUT) -> int | None:
        """
        End the session: hang the terminal up, then stop what is left.

        Args:
            timeout: Seconds to wait between the signals.

        Returns:
            The exit status, or None when it could not be reaped.
        """

    def close(self) -> None:
        """Release the terminal. Ends the session first when it still runs."""
        self.terminate()


class _SubprocessPty(PtyHandle):
    """A real program on a real pseudo-terminal, in a session of its own."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        master: int,
        argv: tuple[str, ...],
        *,
        max_seconds: int,
        idle_seconds: int,
        rows: int,
        cols: int,
        stop_unit: Callable[[], None] | None = None,
        cleanup: Sequence[Callable[[], None]] = (),
    ) -> None:
        """
        Args:
            process: The started program (``setsid -c`` becomes it).
            master: The master side of the terminal, which this handle owns.
            argv: The redacted argument vector.
            max_seconds: The longest the session may last.
            idle_seconds: The longest it may go without anything typed.
            rows: The terminal's height.
            cols: The terminal's width.
            stop_unit: Stops the sandbox's unit; killing ``systemd-run`` does
                not. Called only when the session is stopped while it runs.
            cleanup: Called once when the session is over: the environment file
                of a sandbox, which holds the application's secrets.
        """
        super().__init__(
            argv, max_seconds=max_seconds, idle_seconds=idle_seconds, rows=rows, cols=cols
        )
        self._process = process
        self._master = master
        os.set_blocking(master, False)
        self._read_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._stop_unit = stop_unit
        self._cleanup = list(cleanup)
        self._ended = False
        self._fd_open = True
        with _live_lock:
            _live_processes.add(self)

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def exit_code(self) -> int | None:
        return self._process.poll()

    def is_alive(self) -> bool:
        if self._process.poll() is None:
            reason = self.expired() if self._end_reason is None else None
            if reason is not None:
                self._end(reason)
                return False
            return True
        self._forget()
        return False

    def signal_group(self, number: int) -> None:
        """
        Send a signal to the program's group while its leader runs.

        Args:
            number: The signal.
        """
        if self._process.poll() is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self._process.pid, number)

    def wait_until(self, deadline: float) -> None:
        """
        Wait for the program to exit, at most until a monotonic deadline.

        Args:
            deadline: ``time.monotonic()`` value to give up at.
        """
        with contextlib.suppress(subprocess.TimeoutExpired):
            self._process.wait(timeout=max(0.0, deadline - time.monotonic()))

    def _forget(self) -> None:
        with _live_lock:
            _live_processes.discard(self)

    def _end(self, reason: str) -> None:
        """
        End the session for a reason, once.

        Args:
            reason: One of the ``PTY_END_*`` values.
        """
        with self._state_lock:
            if self._end_reason is None:
                self._end_reason = reason
        self._stop()

    def _stop(self) -> int | None:
        """
        Stop the program and everything it started, and clean up after it.

        Returns:
            The exit status, or None when it could not be reaped.
        """
        with self._state_lock:
            first = not self._ended
            self._ended = True
        running = self._process.poll() is None
        if first and running and self._stop_unit is not None:
            self._stop_unit()
        if running:
            # A hangup first, as closing a real terminal does: a shell passes
            # it to its jobs and leaves cleanly. Then what ignored it.
            for number, grace in (
                (signal.SIGHUP, TERMINATE_TIMEOUT / 2),
                (signal.SIGTERM, TERMINATE_TIMEOUT / 2),
                (signal.SIGKILL, TERMINATE_TIMEOUT),
            ):
                self.signal_group(number)
                try:
                    self._process.wait(timeout=grace)
                    break
                except subprocess.TimeoutExpired:
                    continue
        # Whatever the leader left in its group, whether it was stopped just
        # now or ended on its own: the session is over, so nothing of it stays.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self._process.pid, signal.SIGKILL)
        if first:
            for step in self._cleanup:
                try:
                    step()
                except OSError as exc:
                    _log.warning("A terminal's clean-up step failed: %s", exc)
        self._forget()
        return self._process.poll()

    def read(self, timeout: float = 0.5) -> bytes | None:
        with self._read_lock:
            if not self._fd_open:
                return None
            reason = self.expired()
            if reason is not None and self._end_reason is None:
                self._end(reason)
                return None
            wait = max(0.0, min(timeout, self.limit_remaining(), self.idle_remaining()))
            try:
                ready, _, _ = select.select([self._master], [], [], wait)
            except (OSError, ValueError):
                return None
            if not ready:
                return b""
            try:
                data = os.read(self._master, PTY_READ_CHUNK)
            except BlockingIOError:
                return b""
            except OSError as exc:
                # EIO is how Linux says every other side of the terminal is
                # closed: the program ended, and what it wrote has been read.
                if exc.errno not in (errno.EIO, errno.EBADF):
                    _log.warning("Reading a terminal failed: %s", exc)
                self._finished()
                return None
            if not data:
                self._finished()
                return None
            return data

    def _finished(self) -> None:
        """The program closed its side: reap it and clean up."""
        with self._state_lock:
            if self._end_reason is None:
                self._end_reason = PTY_END_EXITED
        self._stop()

    def write(self, data: bytes) -> None:
        self.touch()
        deadline = time.monotonic() + PTY_WRITE_TIMEOUT
        view = memoryview(data)
        while view:
            with self._write_lock:
                if not self._fd_open:
                    return
                try:
                    sent = os.write(self._master, view)
                    view = view[sent:]
                    continue
                except BlockingIOError:
                    pass
                except OSError as exc:
                    if exc.errno in (errno.EIO, errno.EBADF):
                        return
                    raise
            if time.monotonic() >= deadline:
                raise CommandError(
                    "The program did not read what was typed",
                    details="It may be stopped or busy; end the session and start another.",
                )
            time.sleep(0.01)

    def resize(self, rows: int, cols: int) -> None:
        self.rows = _clamp(rows, PTY_MAX_ROWS)
        self.cols = _clamp(cols, PTY_MAX_COLS)
        with self._write_lock:
            if not self._fd_open:
                return
            try:
                fcntl.ioctl(
                    self._master,
                    termios.TIOCSWINSZ,
                    struct.pack("HHHH", self.rows, self.cols, 0, 0),
                )
            except OSError as exc:
                _log.debug("Could not resize a terminal: %s", exc)

    def input_is_hidden(self) -> bool:
        with self._write_lock:
            if not self._fd_open:
                return False
            try:
                local_flags = termios.tcgetattr(self._master)[3]
            except (termios.error, OSError):
                return False
        return bool(local_flags & termios.ICANON) and not local_flags & termios.ECHO

    def terminate(self, timeout: float = TERMINATE_TIMEOUT) -> int | None:
        with self._state_lock:
            if self._end_reason is None:
                self._end_reason = PTY_END_CLOSED
        return self._stop()

    def close(self) -> None:
        self.terminate()
        # Every reader and writer is told, and waited for, before the descriptor
        # is closed: a number closed under a read may already be someone else's.
        with self._read_lock, self._write_lock:
            if self._fd_open:
                self._fd_open = False
                with contextlib.suppress(OSError):
                    os.close(self._master)


class _EndedPty(PtyHandle):
    """A terminal that never opened, or was never meant to: it ended before it began."""

    def __init__(self, argv: tuple[str, ...], exit_code: int, words: str) -> None:
        """
        Args:
            argv: The redacted argument vector.
            exit_code: How it ended.
            words: What it said on the way out, verbatim.
        """
        super().__init__(argv, max_seconds=1, idle_seconds=1, rows=1, cols=1)
        self._exit_code = exit_code
        self._words = words.encode("utf-8", errors="replace")
        self._end_reason = PTY_END_EXITED

    @property
    def pid(self) -> int:
        return 0

    @property
    def exit_code(self) -> int | None:
        return self._exit_code

    def is_alive(self) -> bool:
        return False

    def read(self, timeout: float = 0.5) -> bytes | None:
        words, self._words = self._words, b""
        return words or None

    def write(self, data: bytes) -> None:
        return None

    def resize(self, rows: int, cols: int) -> None:
        return None

    def terminate(self, timeout: float = TERMINATE_TIMEOUT) -> int | None:
        return self._exit_code


class _EchoShell:
    """
    What a fake terminal answers when asked to behave like a tiny shell.

    Just enough for a person to try the console against a machine that is not
    there: it echoes what is typed, runs three commands and ends on ``exit``.
    """

    PROMPT = b"$ "

    def __init__(self, account: str) -> None:
        """
        Args:
            account: What ``whoami`` answers.
        """
        self.account = account
        self._line = bytearray()

    def greeting(self) -> bytes:
        """
        Returns:
            What it prints when the session opens.
        """
        return b"Fake terminal: nothing here runs on this machine.\r\n" + self.PROMPT

    def feed(self, data: bytes) -> tuple[bytes, bool]:
        """
        Take what was typed.

        Args:
            data: The bytes typed.

        Returns:
            What to print, and whether the shell ended.
        """
        out = bytearray()
        for byte in data:
            if byte in (13, 10):
                out += b"\r\n"
                text = self._line.decode("utf-8", errors="replace").strip()
                self._line.clear()
                if text == "exit":
                    return bytes(out), True
                out += self._run(text) + self.PROMPT
            elif byte in (8, 127):
                if self._line:
                    self._line.pop()
                    out += b"\b \b"
            elif byte == 3:
                self._line.clear()
                out += b"^C\r\n" + self.PROMPT
            elif byte >= 32 or byte == 9:
                self._line.append(byte)
                out.append(byte)
        return bytes(out), False

    def _run(self, text: str) -> bytes:
        if not text:
            return b""
        if text == "whoami":
            return self.account.encode() + b"\r\n"
        if text == "pwd":
            return b"/srv/app\r\n"
        if text.startswith("echo "):
            return text[5:].encode("utf-8", errors="replace") + b"\r\n"
        return f"{text.split()[0]}: command not found\r\n".encode("utf-8", errors="replace")


_EOF = object()


class FakePty(PtyHandle):
    """
    A terminal for tests, started by :meth:`FakeRunner.pty`.

    The test plays the program: :meth:`feed` is what it writes, :attr:`written`
    what the person typed, :meth:`die` ends it. The limits are the real ones.
    """

    _next_pid = 50000

    def __init__(
        self,
        argv: tuple[str, ...],
        *,
        max_seconds: int,
        idle_seconds: int,
        rows: int,
        cols: int,
        alive: bool = True,
        exit_code: int | None = None,
        output: str = "",
        shell: _EchoShell | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            argv: The argument vector the code under test built.
            max_seconds: The longest the session may last.
            idle_seconds: The longest it may go without anything typed.
            rows: The terminal's height.
            cols: The terminal's width.
            alive: Whether it starts running.
            exit_code: Its status when it starts dead.
            output: What it has already written.
            shell: A tiny shell to answer what is typed, or None for silence.
            clock: Monotonic seconds, for the limits.
        """
        super().__init__(
            argv,
            max_seconds=max_seconds,
            idle_seconds=idle_seconds,
            rows=rows,
            cols=cols,
            clock=clock,
        )
        FakePty._next_pid += 1
        self._pid = FakePty._next_pid
        self._queue: queue.Queue[object] = queue.Queue()
        self._alive = alive
        self._exit_code = None if alive else exit_code
        self._shell = shell
        self.written: list[bytes] = []
        self.resizes: list[tuple[int, int]] = []
        self.terminated = False
        #: What :meth:`input_is_hidden` answers: a test sets it for a password prompt.
        self.hidden_input = False
        if output:
            self.feed(output)
        if shell is not None and alive:
            self.feed(shell.greeting())
        if not alive:
            self._end_reason = PTY_END_EXITED
            self._queue.put(_EOF)

    @property
    def pid(self) -> int:
        return self._pid

    @property
    def exit_code(self) -> int | None:
        return self._exit_code

    def is_alive(self) -> bool:
        self._check_limits()
        return self._alive

    def _check_limits(self) -> None:
        reason = self.expired() if self._alive else None
        if reason is not None:
            self._finish(reason, -signal.SIGHUP)

    def _finish(self, reason: str, exit_code: int) -> None:
        if not self._alive:
            return
        self._alive = False
        self._exit_code = exit_code
        self._end_reason = reason
        self._queue.put(_EOF)

    def feed(self, data: bytes | str) -> None:
        """
        Write what the program would print.

        Args:
            data: Text or bytes.
        """
        self._queue.put(data.encode("utf-8") if isinstance(data, str) else data)

    def die(self, exit_code: int = 0) -> None:
        """
        End the program, as exiting would.

        Args:
            exit_code: Its exit status.
        """
        self._finish(PTY_END_EXITED, exit_code)

    def read(self, timeout: float = 0.5) -> bytes | None:
        self._check_limits()
        try:
            item = self._queue.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return b""
        if item is _EOF:
            # Again for the next read: an ended terminal stays ended.
            self._queue.put(_EOF)
            return None
        return item if isinstance(item, bytes) else b""

    def write(self, data: bytes) -> None:
        self.touch()
        self.written.append(data)
        if self._shell is not None and self._alive:
            answer, ended = self._shell.feed(data)
            if answer:
                self.feed(answer)
            if ended:
                self.die(0)

    def input_is_hidden(self) -> bool:
        return self.hidden_input

    def resize(self, rows: int, cols: int) -> None:
        self.rows = _clamp(rows, PTY_MAX_ROWS)
        self.cols = _clamp(cols, PTY_MAX_COLS)
        self.resizes.append((rows, cols))

    def terminate(self, timeout: float = TERMINATE_TIMEOUT) -> int | None:
        if self._alive:
            self.terminated = True
            self._finish(PTY_END_CLOSED, -signal.SIGHUP)
        return self._exit_code


def _redact(argv: Sequence[str], secrets: Iterable[str]) -> tuple[str, ...]:
    """
    Replace every occurrence of a secret in an argument vector.

    Args:
        argv: The argument vector to sanitise.
        secrets: Literal values that must not reach a log.

    Returns:
        The argument vector with secrets substituted.
    """
    values = [s for s in secrets if s]
    if not values:
        return tuple(argv)
    out = []
    for arg in argv:
        for secret in values:
            arg = arg.replace(secret, _REDACTED)
        out.append(arg)
    return tuple(out)


def runuser_prefix(user: str) -> list[str]:
    """
    Build the argv prefix that switches to another account via ``runuser``.

    This is the one place that assembles it. :meth:`SubprocessRunner._prepare`
    uses it for every ``user=`` call, and :class:`FakeRunner` uses it too, so a
    test sees the same argv a real run would produce. The rare caller that
    execs a client directly instead of going through the runner - an
    interactive database session needs the real terminal, which the runner
    cannot hand over - uses it as well, so "how to run as another account" has
    one definition instead of being re-typed at each call site.

    Args:
        user: Account to switch to.

    Returns:
        The ``runuser`` prefix. The program and its arguments follow it.
    """
    return ["runuser", "-u", user, "--"]


# -- Sandboxed execution ------------------------------------------------------

#: The prefix of every transient unit a sandboxed command runs in.
SANDBOX_UNIT_PREFIX = "noust-build-"

#: The PATH a sandboxed command, and the ``systemd-run`` that starts it, see.
#: Fixed and system-wide: a PATH inherited from root's shell can point into
#: /root (nvm), which the sandbox hides, and would resolve a program the
#: build then cannot execute.
SANDBOX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

#: Corepack in a sandbox starts from an empty cache, where it would resolve the
#: newest release of a package manager instead of the version root's cache
#: remembers (seen live: pnpm 12, which the distribution's corepack cannot
#: start). Its known-good default is what a root build got; auto-pinning would
#: write ``packageManager`` into the application's package.json; a prompt would
#: wait forever. A caller's own env still wins.
SANDBOX_COREPACK_ENV = {
    "COREPACK_DEFAULT_TO_LATEST": "0",
    "COREPACK_ENABLE_AUTO_PIN": "0",
    "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0",
}

#: Seconds the unit's own ``RuntimeMaxSec`` adds to the caller's deadline. The
#: runner's deadline is what stops a command; this only stops the unit if the
#: process that started it died first.
SANDBOX_TIMEOUT_MARGIN = 60

#: How long stopping a unit may take: systemd sends SIGTERM, then SIGKILL
#: after the unit's stop timeout (90 s by default).
SANDBOX_STOP_TIMEOUT = 120

#: Exit code of a sandboxed command whose sandbox could not be set up at all
#: (the convention of env, docker and systemd-nspawn for "the wrapper failed").
EXIT_SANDBOX_FAILED = 125

#: What systemd reports when the program could not be executed (EXIT_EXEC).
_SYSTEMD_EXIT_EXEC = 203

#: Variables of this process's environment a sandboxed command may inherit:
#: the locale, the time zone, the proxy a corporate network needs to reach a
#: registry, the CA bundle that proxy is trusted with, and Node's options (the
#: usual place for --max-old-space-size). Nothing else crosses: this process
#: holds Noust's own secrets and whatever the operator's shell exported.
SANDBOX_ENV_ALLOW: tuple[str, ...] = (
    "LANG",
    "LANGUAGE",
    "LC_*",
    "TZ",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "ALL_PROXY",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "NODE_EXTRA_CA_CERTS",
    "REQUESTS_CA_BUNDLE",
    "NODE_OPTIONS",
)

#: What ``clean_env=True`` keeps for a command that is not sandboxed: the
#: sandbox's list plus the account's own identity, so a root build still
#: finds root's caches where it always did.
CLEAN_ENV_ALLOW: tuple[str, ...] = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "TMPDIR",
    *SANDBOX_ENV_ALLOW,
)

#: What the compatibility mode (``pty=True``) adds: a terminal makes tools
#: draw progress bars and colours, which these turn back into lines.
PTY_ENV: dict[str, str] = {"CI": "1", "NO_COLOR": "1", "TERM": "dumb"}

#: Families a sandboxed build may open sockets in. AF_NETLINK is there because
#: Node's os.networkInterfaces() fails without it (uv_interface_addresses,
#: error 97), and every Next.js build calls it.
SANDBOX_ADDRESS_FAMILIES = "AF_UNIX AF_INET AF_INET6 AF_NETLINK"

#: Level 1 of the build sandbox: each property was tested against a hostile
#: postinstall and none broke an npm install (docs research 3.1,
#: privilege-model.md 5.2). The minimum systemd is 249 (Ubuntu 22.04).
_STRICT_PROPERTIES: tuple[str, ...] = (
    "PrivateDevices=yes",
    "ProtectSystem=strict",
    "ProtectKernelTunables=yes",
    "ProtectKernelModules=yes",
    "ProtectKernelLogs=yes",
    "ProtectControlGroups=yes",
    "ProtectClock=yes",
    "ProtectHostname=yes",
    "RestrictSUIDSGID=yes",
    "RestrictNamespaces=yes",
    "LockPersonality=yes",
    "RestrictRealtime=yes",
    f"RestrictAddressFamilies={SANDBOX_ADDRESS_FAMILIES}",
    "CapabilityBoundingSet=",
)

#: Characters a path may hold to be named in a unit property. ``:`` separates
#: a bind's source from its target, whitespace separates list entries, ``%``
#: is a specifier and ``$`` a variable; a path with any of them would be
#: misread rather than refused, so it is refused here.
_UNIT_PATH = re.compile(r"^/[A-Za-z0-9._/@+=,-]*$")

#: An account name systemd accepts in ``User=``.
_ACCOUNT = re.compile(r"^[a-z_][a-z0-9_-]*[$]?$")

#: A variable name an environment file can hold.
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_account_name(name: str) -> bool:
    """
    Tell whether a name is one systemd accepts as ``User=`` or ``Group=``.

    Args:
        name: The candidate.

    Returns:
        True for an account name; False for anything else, the empty string
        included.
    """
    return bool(_ACCOUNT.fullmatch(name))


Network = Literal["full", "none"]


def _unit_path(path: Path, what: str) -> str:
    """
    Check that a path can be named in a unit property, and return it as text.

    Args:
        path: The path.
        what: What it is, for the error.

    Returns:
        The path as a string.

    Raises:
        ValueError: When it is relative or holds a character systemd would
            interpret.
    """
    text = str(path)
    if not _UNIT_PATH.fullmatch(text):
        raise ValueError(
            f"The sandbox cannot name {what} {text!r}: a path must be absolute and "
            "hold only letters, digits and . _ / @ + = , -"
        )
    return text


@dataclass(frozen=True)
class SandboxSpec:
    """
    How to confine one command: as whom, what it may see and write, and its limits.

    The runner knows nothing about applications; whoever builds the spec (the
    deployer, through :mod:`noust.deployers.helpers.sandbox`) decides what a
    build may touch. Executed with :func:`sandbox_prefix`.

    Attributes:
        user: The account the command runs as.
        group: Its group; the account's name when None.
        writable_paths: The only places it may write (besides a private /tmp).
        read_only_paths: Places it may read even when they sit under a hidden
            path.
        inaccessible_paths: Places that do not open at all, whatever their
            permissions: Noust's configuration, the store, backups. A path
            that does not exist is skipped.
        hidden_paths: Directories replaced by an empty read-only tmpfs, so
            what is beside the command's own paths is not there at all
            (ENOENT): the other applications, the other caches. A writable
            or read-only path under one is bound back in.
        env_allow: Names (``LC_*`` globs allowed) of this process's variables
            the command inherits. Everything else it gets is what the caller
            passes as ``env``.
        env_files: Environment files systemd reads for the command, as root,
            before it drops privileges: how an application's ``.env`` reaches
            its build without the build account being able to open the file.
            A missing file is skipped.
        unset_env: Variables removed from the command's environment after
            every source is read (``UnsetEnvironment=``): what an
            application's ``.env`` sets that an install must not see.
        masked_files: Files that read as empty inside the sandbox (an empty
            file is bound over each): a release's ``.env``, which the build
            account may not open and which dotenv loaders (Vite's among them)
            would otherwise fail on with EACCES. Its values arrive through
            ``env_files``.
        memory_max_mb: ``MemoryMax``; the kernel kills the command beyond it.
        cpu_quota_percent: ``CPUQuota``, in percent of one CPU.
        tasks_max: ``TasksMax``.
        network: ``full``, or ``none`` for a private network with only
            loopback.
        working_dir: Its working directory; the call's ``cwd`` when None.
        timeout: The unit's own ``RuntimeMaxSec`` before the margin; the
            call's deadline when None.
        strict: The whole level-1 confinement. False keeps only what an
            application's own unit has (``NoNewPrivileges``, ``PrivateTmp``):
            the regime of a migration, which runs with the application's
            identity and secrets, not a build's.
        protect_home: Hide /home, /root and /run/user. Turned off by the
            policy when the application itself lives under /home.
        pty: Run on a pseudo-terminal instead of pipes: the compatibility
            mode for a build script that reopens /dev/stderr, which a pipe
            owned by root refuses to another account.
        interactive: A person types into it (:meth:`CommandRunner.pty`): it runs
            on a pseudo-terminal like ``pty``, but keeps the terminal's own
            environment instead of the build mode's ``CI``, ``NO_COLOR`` and
            ``TERM=dumb``.
        name: What the unit is named after (the application), for
            ``systemctl`` and the journal.
    """

    user: str
    group: str | None = None
    writable_paths: tuple[Path, ...] = ()
    read_only_paths: tuple[Path, ...] = ()
    inaccessible_paths: tuple[Path, ...] = ()
    hidden_paths: tuple[Path, ...] = ()
    env_allow: tuple[str, ...] = SANDBOX_ENV_ALLOW
    env_files: tuple[Path, ...] = ()
    unset_env: tuple[str, ...] = ()
    masked_files: tuple[Path, ...] = ()
    memory_max_mb: int | None = None
    cpu_quota_percent: int | None = None
    tasks_max: int | None = None
    network: Network = "full"
    working_dir: Path | None = None
    timeout: int | None = None
    strict: bool = True
    protect_home: bool = True
    pty: bool = False
    interactive: bool = False
    name: str = "command"
    #: Filled in by __post_init__: the slug the unit name is built from.
    slug: str = field(default="", init=False)

    def __post_init__(self) -> None:
        """
        Refuse a spec that cannot be expressed as unit properties.

        Raises:
            ConfigError: An account that is not an account name.
            ValueError: A network mode or a path systemd would misread.
        """
        for account in (self.user, self.group):
            if account is not None and not is_account_name(account):
                # An account comes from the configuration (or a constant), never
                # from code building paths, so it is the operator's to fix.
                raise ConfigError(
                    f"The sandbox cannot run as {account!r}: not an account name",
                    details=(
                        "A command is sandboxed as the account Noust builds with or the one "
                        "applications run as (service_user and service_group in "
                        f"{paths.config_dir() / 'config.yaml'}). Set it to an existing account, "
                        "or set service_group to '' to use the user's primary group."
                    ),
                )
        if self.network not in ("full", "none"):
            raise ValueError(f"Unknown sandbox network {self.network!r}: use 'full' or 'none'")
        for what, group in (
            ("a writable path", self.writable_paths),
            ("a read-only path", self.read_only_paths),
            ("an inaccessible path", self.inaccessible_paths),
            ("a hidden path", self.hidden_paths),
            ("an environment file", self.env_files),
            ("a masked file", self.masked_files),
        ):
            for path in group:
                _unit_path(path, what)
        for name in self.unset_env:
            if not _ENV_NAME.fullmatch(name):
                raise ValueError(f"The sandbox cannot unset {name!r}: not a variable name")
        if self.working_dir is not None:
            _unit_path(self.working_dir, "the working directory")
        for limit in (self.memory_max_mb, self.cpu_quota_percent, self.tasks_max, self.timeout):
            if limit is not None and limit <= 0:
                raise ValueError(f"A sandbox limit must be positive, not {limit}")
        slug = re.sub(r"[^a-z0-9-]+", "-", self.name.lower()).strip("-")[:48] or "command"
        object.__setattr__(self, "slug", slug)


def sandbox_unit_name(spec: SandboxSpec) -> str:
    """
    Name the transient unit one sandboxed command runs in.

    Chosen before the unit exists, so a cancellation or a deadline can stop it
    by name: killing ``systemd-run`` does not stop the unit.

    Args:
        spec: The spec.

    Returns:
        ``noust-build-<name>-<8 hex>``, without ``.service``.
    """
    return f"{SANDBOX_UNIT_PREFIX}{spec.slug}-{_secrets.token_hex(4)}"


def sandbox_runtime_dir() -> Path:
    """
    Return where a sandboxed command's environment file and result live.

    Returns:
        The directory (root only, on tmpfs).
    """
    return paths.SANDBOX_RUNTIME_DIR


def sandbox_empty_file() -> Path:
    """
    Return the empty file bound over a sandbox's masked files.

    Returns:
        Its path, in :func:`sandbox_runtime_dir`; the runner creates it.
    """
    return sandbox_runtime_dir() / "empty"


def escape_systemd_argv(argv: Sequence[str]) -> list[str]:
    """
    Make a command pass through ``systemd-run`` unchanged.

    systemd expands ``$VAR`` and ``${VAR}`` in the command it executes, so an
    argument such as ``--define=a=$HOME`` would reach the program with the
    unit's HOME in it: a shell's behaviour, which rule 1 forbids. ``$$`` is
    systemd's escape for one ``$``, and it works on every version (the
    ``--expand-environment=no`` switch needs 254). ``%`` is left alone: the
    command line is passed as a vector, and specifiers are not expanded in it.

    Args:
        argv: The command.

    Returns:
        The command with every ``$`` doubled.
    """
    return [arg.replace("$", "$$") for arg in argv]


def _allowed(name: str, allow: Iterable[str]) -> bool:
    """
    Tell whether a variable's name is on an allow-list.

    Args:
        name: The variable.
        allow: Names, ``*`` globs allowed.

    Returns:
        True when one entry matches.
    """
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in allow)


def clean_environment(
    parent: Mapping[str, str], allow: Iterable[str] = CLEAN_ENV_ALLOW
) -> dict[str, str]:
    """
    Keep only the allowed variables of an environment.

    Args:
        parent: The environment to filter; usually this process's.
        allow: The names that survive, ``*`` globs allowed.

    Returns:
        The allowed variables.
    """
    names = tuple(allow)
    return {name: value for name, value in parent.items() if _allowed(name, names)}


def sandbox_environment(
    spec: SandboxSpec, env: Mapping[str, str] | None, parent: Mapping[str, str]
) -> dict[str, str]:
    """
    Compose the whole environment a sandboxed command starts with.

    Args:
        spec: The spec, for its allow-list and mode.
        env: What the caller passes, which wins over everything else.
        parent: This process's environment, filtered through the allow-list.

    Returns:
        The variables, as they go into the environment file.
    """
    composed = {"PATH": SANDBOX_PATH, **SANDBOX_COREPACK_ENV}
    composed.update(clean_environment(parent, spec.env_allow))
    if spec.pty and not spec.interactive:
        composed.update(PTY_ENV)
    if env:
        composed.update(env)
    return composed


def environment_file_text(variables: Mapping[str, str]) -> str:
    """
    Serialise variables for systemd's ``EnvironmentFile=``.

    Every value is double-quoted with systemd's four escapes (backslash,
    double quote, backquote and dollar), so it comes back byte for byte,
    newlines included, and is never expanded.

    Args:
        variables: Name to value.

    Returns:
        The file's content.

    Raises:
        ValueError: A name that is not a variable name, or a NUL in a value.
    """
    lines = []
    for name, value in variables.items():
        if not _ENV_NAME.fullmatch(name):
            raise ValueError(f"{name!r} is not a variable name the sandbox can pass")
        if "\x00" in value:
            raise ValueError(f"The value of {name} holds a NUL byte")
        escaped = (
            value.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
        )
        lines.append(f'{name}="{escaped}"')
    return "\n".join(lines) + "\n"


def _under(path: Path, parents: Iterable[Path]) -> bool:
    """
    Tell whether a path is one of some directories or inside one.

    Args:
        path: The path.
        parents: The directories.

    Returns:
        True when it is.
    """
    return any(path == parent or parent in path.parents for parent in parents)


def _touch_program() -> str:
    """
    Return the absolute path of ``touch``, for the unit's result marker.

    Returns:
        Where ``touch`` is, ``/usr/bin/touch`` when it cannot be looked up.
    """
    return shutil.which("touch", path=SANDBOX_PATH) or "/usr/bin/touch"


def sandbox_prefix(
    spec: SandboxSpec,
    *,
    unit: str,
    env_file: Path,
    cwd: Path | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[str]:
    """
    Build the ``systemd-run`` argv that runs a command confined as ``spec`` says.

    The one place it is assembled: the real runner executes it, and
    :class:`FakeRunner` records it, so a test sees what would run. The
    command follows the returned ``--``, escaped by
    :func:`escape_systemd_argv`.

    ``--wait --pipe`` keep ``stream``'s semantics (output line by line, the
    exit code propagated), ``--collect`` unloads the unit even when it failed,
    and ``ExecStopPost=+touch`` leaves a marker named after systemd's own
    ``$SERVICE_RESULT``: ``systemd-run`` exits 1 both for a deadline and for
    the memory limit, and the marker is how the runner tells the operator
    which one it was. ``+`` runs it as root outside the sandbox, into a
    directory only root can write.

    Args:
        spec: The confinement.
        unit: The unit's name, from :func:`sandbox_unit_name`.
        env_file: The environment file the runner writes for this command.
        cwd: The working directory when the spec names none.
        timeout: The caller's deadline, when the spec names none.

    Returns:
        ``systemd-run`` and its options, ending with ``--``.
    """
    argv = [
        "systemd-run",
        f"--unit={unit}",
        f"--description=Noust sandbox for {spec.slug}",
        "--wait",
        "--collect",
        "--quiet",
        "--pty" if spec.pty or spec.interactive else "--pipe",
    ]
    group = spec.group or spec.user
    properties = [f"User={spec.user}", f"Group={group}"]
    workdir = spec.working_dir or cwd
    if workdir is not None:
        properties.append(f"WorkingDirectory={_unit_path(workdir, 'the working directory')}")
    properties += ["NoNewPrivileges=yes", "PrivateTmp=yes", "UMask=0022"]
    if spec.strict:
        properties += list(_STRICT_PROPERTIES)
        if spec.protect_home:
            properties.append("ProtectHome=yes")
        for hidden in spec.hidden_paths:
            properties.append(f"TemporaryFileSystem={_unit_path(hidden, 'a hidden path')}:ro")
        for path in spec.writable_paths:
            text = _unit_path(path, "a writable path")
            # Under a hidden directory the path is bound back in, writable;
            # elsewhere ProtectSystem=strict only needs to be told.
            properties.append(
                f"BindPaths={text}" if _under(path, spec.hidden_paths) else f"ReadWritePaths={text}"
            )
        for path in spec.read_only_paths:
            text = _unit_path(path, "a read-only path")
            properties.append(
                f"BindReadOnlyPaths=-{text}"
                if _under(path, spec.hidden_paths)
                else f"ReadOnlyPaths=-{text}"
            )
        empty = _unit_path(sandbox_empty_file(), "the empty file")
        for path in spec.masked_files:
            properties.append(f"BindReadOnlyPaths=-{empty}:{_unit_path(path, 'a masked file')}")
        for path in spec.inaccessible_paths:
            properties.append(f"InaccessiblePaths=-{_unit_path(path, 'an inaccessible path')}")
        if spec.network == "none":
            properties.append("PrivateNetwork=yes")
    elif spec.network == "none":
        properties.append("PrivateNetwork=yes")
    properties.append(f"RuntimeMaxSec={(spec.timeout or timeout) + SANDBOX_TIMEOUT_MARGIN}")
    if spec.memory_max_mb is not None:
        properties.append(f"MemoryMax={spec.memory_max_mb}M")
    if spec.cpu_quota_percent is not None:
        properties.append(f"CPUQuota={spec.cpu_quota_percent}%")
    if spec.tasks_max is not None:
        properties.append(f"TasksMax={spec.tasks_max}")
    if spec.unset_env:
        # systemd applies it after every EnvironmentFile=, wherever it is listed.
        properties.append(f"UnsetEnvironment={' '.join(spec.unset_env)}")
    for env in spec.env_files:
        properties.append(f"EnvironmentFile=-{_unit_path(env, 'an environment file')}")
    # Last, so what the runner composed wins over an application's .env.
    properties.append(f"EnvironmentFile={_unit_path(env_file, 'the environment file')}")
    marker = _unit_path(sandbox_runtime_dir(), "the runtime directory") + f"/{unit}.result"
    properties.append(
        f"ExecStopPost=+{_touch_program()} "
        f"{marker}.${{SERVICE_RESULT}}.${{EXIT_CODE}}.${{EXIT_STATUS}}"
    )
    argv += [f"--property={prop}" for prop in properties]
    argv.append("--")
    return argv


def read_sandbox_marker(unit: str, directory: Path | None = None) -> tuple[str, str, str] | None:
    """
    Read, and remove, the result marker a sandboxed command's unit left.

    Args:
        unit: The unit's name.
        directory: Where markers are; :func:`sandbox_runtime_dir` by default.

    Returns:
        ``(result, code, status)`` as systemd set ``$SERVICE_RESULT``,
        ``$EXIT_CODE`` and ``$EXIT_STATUS``, or None when the unit never
        stopped through its ``ExecStopPost`` (it never started).
    """
    base = directory or sandbox_runtime_dir()
    found = None
    for marker in sorted(base.glob(f"{unit}.result.*")):
        parts = marker.name[len(unit) + len(".result.") :].split(".")
        if len(parts) == 3 and found is None:
            found = (parts[0], parts[1], parts[2])
        with contextlib.suppress(OSError):
            marker.unlink()
    return found


def interpret_sandbox_result(
    raw: CommandResult,
    marker: tuple[str, str, str] | None,
    spec: SandboxSpec,
    *,
    argv: tuple[str, ...],
    unit: str,
    timeout: int,
) -> CommandResult:
    """
    Turn what ``systemd-run`` returned into what the command did.

    ``systemd-run --wait`` propagates a normal exit code, but reports a kill
    by the deadline or by the memory limit as a plain 1, which would read as
    "the build failed" with nothing in the log to say why. The marker says
    why, and so does the result: a deadline is ``timed_out``, a memory kill is
    exit code 137 (what the OOM killer's SIGKILL gives a shell), and both get
    a sentence in ``stderr`` that names the limit.

    Args:
        raw: The result of running ``systemd-run``.
        marker: What :func:`read_sandbox_marker` found.
        spec: The spec it ran with, for the limits it names.
        argv: The command as the caller gave it, redacted.
        unit: The unit.
        timeout: The caller's deadline.

    Returns:
        The command's result.
    """
    base = replace(raw, argv=argv, sandbox_unit=unit)
    if raw.timed_out:
        return replace(
            base,
            exit_code=EXIT_TIMEOUT,
            sandbox_result="timeout",
            stderr=_joined(
                raw.stderr,
                f"The command ran longer than its {timeout}s deadline; its sandbox "
                f"({unit}) was stopped.",
            ),
        )
    if marker is None:
        if raw.exit_code == 0:
            return base
        return replace(
            base,
            exit_code=raw.exit_code or EXIT_SANDBOX_FAILED,
            stderr=_joined(
                raw.stderr,
                "The sandbox could not be started; systemd-run's own words are above.",
            ),
        )
    result, code, status = marker
    base = replace(base, sandbox_result=result)
    if result == "success":
        return replace(base, exit_code=0)
    if result == "exit-code":
        exit_status = int(status) if status.isdigit() else raw.exit_code
        if exit_status == _SYSTEMD_EXIT_EXEC:
            return replace(
                base,
                exit_code=EXIT_NOT_FOUND,
                stderr=_joined(
                    raw.stderr,
                    f"The sandbox could not execute {argv[0] if argv else 'the command'}: "
                    "it is missing, or it lives somewhere the sandbox hides (/root, /home).",
                ),
            )
        return replace(base, exit_code=exit_status)
    if result == "oom-kill":
        limit = (
            f"its memory limit (MemoryMax={spec.memory_max_mb}M)"
            if spec.memory_max_mb is not None
            else "the memory available"
        )
        return replace(
            base,
            exit_code=128 + signal.SIGKILL,
            stderr=_joined(
                raw.stderr,
                f"The command was killed because it ran out of memory: it reached {limit}.",
            ),
        )
    if result == "timeout":
        return replace(
            base,
            exit_code=EXIT_TIMEOUT,
            timed_out=True,
            stderr=_joined(
                raw.stderr,
                f"systemd stopped the sandbox ({unit}) at its RuntimeMaxSec: the command "
                "outlived the process that started it.",
            ),
        )
    if result in ("signal", "core-dump"):
        try:
            number = int(signal.Signals[f"SIG{status}"])
        except KeyError:
            number = 0
        return replace(
            base,
            exit_code=128 + number if number else raw.exit_code or 1,
            stderr=_joined(raw.stderr, f"The command was killed by SIG{status}."),
        )
    return replace(
        base,
        exit_code=raw.exit_code or 1,
        stderr=_joined(raw.stderr, f"The sandbox failed: systemd reports Result={result}."),
    )


def _joined(first: str, second: str) -> str:
    """
    Append a sentence to some output.

    Args:
        first: What there was.
        second: What to add.

    Returns:
        Both, on separate lines.
    """
    return f"{first.rstrip()}\n{second}" if first.strip() else second


def _validate(argv: Sequence[str]) -> list[str]:
    """
    Reject argument vectors that cannot be executed safely.

    Args:
        argv: Candidate argument vector.

    Returns:
        The vector as a list of strings.

    Raises:
        ValueError: When the vector is empty, is a bare string, or contains a
            NUL byte (which would silently truncate the argument at the execve
            boundary).
    """
    if isinstance(argv, (str, bytes)):
        raise ValueError(
            "argv must be a sequence of arguments, not a string. "
            "Passing a string invites shell-injection through naive splitting."
        )
    args = [str(a) for a in argv]
    if not args:
        raise ValueError("argv must not be empty")
    for arg in args:
        if "\x00" in arg:
            raise ValueError("argv arguments must not contain NUL bytes")
    return args


class CommandRunner(ABC):
    """Executes external processes. The only such thing in the codebase."""

    #: The execution hook, reachable from the class: ``CommandRunner.
    #: add_execution_listener(callable)``. One registry for every runner.
    add_execution_listener = staticmethod(add_execution_listener)
    remove_execution_listener = staticmethod(remove_execution_listener)

    @abstractmethod
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        input: str | None = None,
        stdin_path: Path | None = None,
        user: str | None = None,
        check: bool = False,
        secrets: Sequence[str] = (),
        sandbox: SandboxSpec | None = None,
        clean_env: bool = False,
    ) -> CommandResult:
        """
        Execute a command and wait for it to finish.

        Args:
            argv: Program and arguments. Never a shell string.
            cwd: Working directory.
            env: Extra environment variables, merged over the current one.
            timeout: Deadline in seconds.
            input: Data written to the process stdin, then closed. This is how
                secrets are passed to programs that accept them on stdin.
            stdin_path: A file given to the process as its stdin, byte for byte.
                How a dump reaches a client without being named in a command
                the client would parse as a script. Exclusive with ``input``.
                With neither, the process reads ``/dev/null``: no command
                Noust runs may wait on the caller's terminal.
            user: Run as this account instead of the current one.
            check: Raise CommandError instead of returning a failed result.
            secrets: Literal values to redact from the recorded command line.
            sandbox: Run confined as the spec says, in a transient systemd
                unit (see :func:`sandbox_prefix`). The command then inherits
                nothing of this process's environment but the spec's
                allow-list; ``env`` is what it gets. Exclusive with ``user``:
                the spec names the account.
            clean_env: Without a sandbox, start from the variables in
                :data:`CLEAN_ENV_ALLOW` instead of this whole process's
                environment. For builds and hooks, which run code Noust did
                not write and must not see its secrets.

        Returns:
            The command outcome.

        Raises:
            CommandError: When check is True and the command failed.
        """

    @abstractmethod
    def stream(
        self,
        argv: Sequence[str],
        *,
        on_line: Callable[[str], None],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        user: str | None = None,
        secrets: Sequence[str] = (),
        sandbox: SandboxSpec | None = None,
        clean_env: bool = False,
    ) -> CommandResult:
        """
        Execute a command, delivering merged output line by line as it appears.

        Long builds must not look frozen. ``on_line`` is called for each line of
        combined stdout and stderr while the process runs.

        Args:
            argv: Program and arguments.
            on_line: Called once per output line, without the trailing newline.
            cwd: Working directory.
            env: Extra environment variables, merged over the current one.
            timeout: Deadline in seconds for the whole command.
            user: Run as this account instead of the current one.
            secrets: Literal values to redact from the recorded command line.
            sandbox: Run confined, as for :meth:`run`.
            clean_env: Start from a clean environment, as for :meth:`run`.

        Returns:
            The command outcome. ``stdout`` holds the accumulated output.
        """

    @abstractmethod
    def capture_to_file(
        self,
        argv: Sequence[str],
        destination: Path,
        *,
        compress: bool = False,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        user: str | None = None,
        secrets: Sequence[str] = (),
    ) -> CommandResult:
        """
        Execute a command, writing its stdout straight to a file.

        This exists so that database dumps never pass through a shell or through
        Python memory. The previous implementation rendered a dump into
        ``bash -c "echo '...' > file"``, which corrupted binary dumps and let
        the contents of a database escape into a command line.

        Args:
            argv: Program and arguments producing the dump on stdout.
            destination: File to write. Created with mode 0600.
            compress: Pipe the output through gzip before writing.
            cwd: Working directory.
            env: Extra environment variables, merged over the current one.
            timeout: Deadline in seconds.
            user: Run as this account instead of the current one.
            secrets: Literal values to redact from the recorded command line.

        Returns:
            The command outcome. ``stdout`` is empty; the bytes went to disk.
        """

    def start(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        user: str | None = None,
        secrets: Sequence[str] = (),
    ) -> ProcessHandle:
        """
        Start a long-lived process and return without waiting for it.

        The one exception to "timeouts are mandatory": a tunnel has no natural
        end. What replaces the deadline is ownership: the process runs in its
        own session, so the handle's ``terminate`` stops it and everything it
        started, and every process still running is stopped when the
        interpreter exits. Its stdin and stdout are ``/dev/null``; the last
        lines of its stderr are kept for the caller to show verbatim.

        Args:
            argv: Program and arguments. Never a shell string.
            cwd: Working directory.
            env: Extra environment variables, merged over the current one.
            user: Run as this account instead of the current one.
            secrets: Literal values to redact from the recorded command line.

        Returns:
            The handle. A program that cannot be found yields a handle that
            has already ended with :data:`EXIT_NOT_FOUND`, like :meth:`run`.

        Raises:
            CommandError: When this runner cannot start long-lived processes.
        """
        raise CommandError(
            f"{type(self).__name__} cannot start long-lived processes",
            details="Use SubprocessRunner, DryRunRunner or FakeRunner.",
        )

    def pty(
        self,
        argv: Sequence[str],
        *,
        max_seconds: int,
        idle_seconds: int,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        user: str | None = None,
        secrets: Sequence[str] = (),
        sandbox: SandboxSpec | None = None,
        rows: int = PTY_DEFAULT_ROWS,
        cols: int = PTY_DEFAULT_COLS,
    ) -> PtyHandle:
        """
        Run a program on a pseudo-terminal a person types into.

        The mandatory deadline of every other command is two limits here, both
        required: the session may last ``max_seconds`` and may go
        ``idle_seconds`` with nothing typed. The runner enforces them, so a
        terminal nobody closed ends anyway, with the program and everything it
        started. The program runs in a session of its own, with the terminal as
        its controlling one, and with nothing of this process's environment
        beyond :data:`PTY_ENV_ALLOW`: what it gets is ``env``.

        Args:
            argv: Program and arguments. Never a shell string.
            max_seconds: The longest the session may last.
            idle_seconds: The longest it may go with nothing typed.
            cwd: Working directory.
            env: The program's environment, over the allowed variables.
            user: Run as this account instead of the current one.
            secrets: Literal values to redact from the recorded command line.
            sandbox: Run confined, as for :meth:`run`; a spec with
                ``interactive`` set. Exclusive with ``user``.
            rows: The terminal's height.
            cols: The terminal's width.

        Returns:
            The session. A program that cannot be found yields one that has
            already ended with :data:`EXIT_NOT_FOUND`.

        Raises:
            ValueError: A limit that is not positive, or an argv that cannot
                be executed safely.
            CommandError: When this runner cannot open terminals.
        """
        raise CommandError(
            f"{type(self).__name__} cannot open terminals",
            details="Use SubprocessRunner, DryRunRunner or FakeRunner.",
        )

    def exists(self, program: str) -> bool:
        """
        Report whether a program is present on PATH.

        Args:
            program: Executable name.

        Returns:
            True when the program can be executed.
        """
        return shutil.which(program) is not None


def _require_limits(max_seconds: int, idle_seconds: int) -> None:
    """
    Refuse a terminal without both of its limits.

    Args:
        max_seconds: The deadline of the session.
        idle_seconds: The idle limit.

    Raises:
        ValueError: When either is not a positive number of seconds.
    """
    for name, value in (("max_seconds", max_seconds), ("idle_seconds", idle_seconds)):
        if value <= 0:
            raise ValueError(
                f"A terminal needs a positive {name}, not {value}: a session without its "
                "limits would outlive whoever opened it"
            )


def _refuse_user_with_sandbox(user: str | None, sandbox: SandboxSpec | None) -> None:
    """
    Refuse a call that names an account twice.

    Args:
        user: The ``user=`` argument.
        sandbox: The ``sandbox=`` argument.

    Raises:
        ValueError: When both are given.
    """
    if user is not None and sandbox is not None:
        raise ValueError("user= and sandbox= are exclusive: the sandbox spec names the account")


@dataclass(frozen=True)
class _SandboxedCommand:
    """
    A sandboxed command, prepared and not yet started.

    Attributes:
        unit: The transient unit it will run in, without ``.service``.
        runtime: The directory its environment file and result marker live in.
        env_file: Its environment file, which the caller removes when done.
        wrapped: The ``systemd-run`` argv that runs it.
        redacted: That argv with secrets hidden, for logs.
        redacted_inner: The command alone, with secrets hidden.
    """

    unit: str
    runtime: Path
    env_file: Path
    wrapped: list[str]
    redacted: tuple[str, ...]
    redacted_inner: tuple[str, ...]


class SubprocessRunner(CommandRunner):
    """The real runner. Executes processes with :mod:`subprocess`."""

    def __init__(self, *, on_command: Callable[[tuple[str, ...]], None] | None = None):
        """
        Args:
            on_command: Optional hook called with each redacted argv before it
                runs. Used to feed the verbose log and the audit trail.
        """
        self._on_command = on_command

    def _prepare(
        self,
        argv: Sequence[str],
        env: Mapping[str, str] | None,
        user: str | None,
        secrets: Sequence[str],
        *,
        clean_env: bool = False,
    ) -> tuple[list[str], dict[str, str], tuple[str, ...]]:
        """
        Validate and decorate a command before execution.

        Args:
            argv: Candidate argument vector.
            env: Extra environment variables.
            user: Account to switch to, if any.
            secrets: Values to redact.
            clean_env: Start from :data:`CLEAN_ENV_ALLOW` instead of the
                whole environment of this process.

        Returns:
            The final argv, the merged environment, and the redacted argv used
            for logging and for the result.
        """
        args = _validate(argv)
        redacted = _redact(args, secrets)
        if user is not None:
            prefix = runuser_prefix(user)
            args = [*prefix, *args]
            redacted = (*prefix, *redacted)
        run_env = clean_environment(os.environ) if clean_env else dict(os.environ)
        if env:
            run_env.update(env)
        if self._on_command is not None:
            self._on_command(redacted)
        return args, run_env, redacted

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        input: str | None = None,
        stdin_path: Path | None = None,
        user: str | None = None,
        check: bool = False,
        secrets: Sequence[str] = (),
        sandbox: SandboxSpec | None = None,
        clean_env: bool = False,
    ) -> CommandResult:
        if input is not None and stdin_path is not None:
            raise ValueError("input and stdin_path are exclusive: a process has one stdin")
        _refuse_user_with_sandbox(user, sandbox)
        if sandbox is not None:
            result = self._run_sandboxed(
                argv,
                sandbox,
                cwd=cwd,
                env=env,
                timeout=timeout,
                secrets=secrets,
                input=input,
                stdin_path=stdin_path,
                on_line=None,
            )
        else:
            args, run_env, redacted = self._prepare(argv, env, user, secrets, clean_env=clean_env)
            result = self._execute(
                args,
                run_env,
                redacted,
                cwd=cwd,
                timeout=timeout,
                input=input,
                stdin_path=stdin_path,
            )
            _notify_execution(_redact(_validate(argv), secrets), cwd=cwd, result=result, user=user)
        return result.check() if check else result

    def _execute(
        self,
        args: list[str],
        run_env: dict[str, str],
        redacted: tuple[str, ...],
        *,
        cwd: Path | None,
        timeout: int,
        input: str | None,
        stdin_path: Path | None,
    ) -> CommandResult:
        """
        Run a prepared command to completion and describe how it ended.

        Args:
            args: Final argv.
            run_env: Complete environment.
            redacted: Argv for the result and for messages.
            cwd: Working directory.
            timeout: Deadline in seconds.
            input: Text for stdin, or None.
            stdin_path: A file for stdin, or None.

        Returns:
            The outcome.

        Raises:
            CommandCancelled: When the command's operation is cancelled.
        """
        cancel = _cancel_scope(redacted)
        started = time.monotonic()
        try:
            with contextlib.ExitStack() as stack:
                # Never the caller's stdin: a child that inherits a terminal
                # can sit reading it until the deadline (git asking for a
                # username did exactly that). /dev/null makes it fail instead.
                stdin: IO[bytes] | int | None
                if stdin_path is not None:
                    stdin = stack.enter_context(open(stdin_path, "rb"))
                else:
                    stdin = None if input is not None else subprocess.DEVNULL
                if cancel is not None:
                    completed = self._run_cancellable(
                        args, cwd, run_env, input, stdin, timeout, cancel, redacted
                    )
                else:
                    completed = subprocess.run(
                        args,
                        cwd=str(cwd) if cwd else None,
                        env=run_env,
                        input=input,
                        stdin=stdin,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        check=False,
                    )
        except subprocess.TimeoutExpired:
            return CommandResult(
                argv=redacted,
                exit_code=EXIT_TIMEOUT,
                stderr=f"Command timed out after {timeout}s",
                duration=time.monotonic() - started,
                timed_out=True,
            )
        except FileNotFoundError:
            return CommandResult(
                argv=redacted,
                exit_code=EXIT_NOT_FOUND,
                stderr=f"Command not found: {args[0]}",
                duration=time.monotonic() - started,
            )
        except PermissionError as exc:
            return CommandResult(
                argv=redacted,
                exit_code=EXIT_NOT_FOUND,
                stderr=str(exc),
                duration=time.monotonic() - started,
            )
        return CommandResult(
            argv=redacted,
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            duration=time.monotonic() - started,
        )

    @staticmethod
    def _run_cancellable(
        args: list[str],
        cwd: Path | None,
        run_env: dict[str, str],
        input: str | None,
        stdin: IO[bytes] | int | None,
        timeout: int,
        cancel: threading.Event,
        redacted: tuple[str, ...],
    ) -> subprocess.CompletedProcess[str]:
        """
        Run a command to completion unless its cancel scope is set first.

        The process gets its own session, so cancelling or timing out kills
        the whole tree it started, not only the direct child.

        Args:
            args: Final argv.
            cwd: Working directory.
            run_env: Complete environment.
            input: Text for stdin, or None.
            stdin: The stdin to give the process when ``input`` is None.
            timeout: Deadline in seconds.
            cancel: The scope's event.
            redacted: Argv for messages.

        Returns:
            The finished process, as :func:`subprocess.run` would return it.

        Raises:
            CommandCancelled: When the event is set while the command runs.
            subprocess.TimeoutExpired: When the deadline passes first; the
                process tree has been killed.
        """
        process = subprocess.Popen(
            args,
            cwd=str(cwd) if cwd else None,
            env=run_env,
            stdin=subprocess.PIPE if input is not None else stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        pending = input
        while True:
            remaining = deadline - time.monotonic()
            try:
                # communicate() may be called again after it times out; the
                # input is handed over on the first call only.
                stdout, stderr = process.communicate(
                    input=pending, timeout=max(0.0, min(CANCEL_POLL_INTERVAL, remaining))
                )
            except subprocess.TimeoutExpired:
                pending = None
                if cancel.is_set():
                    _kill_session(process)
                    raise CommandCancelled(f"Cancelled: {' '.join(redacted)}") from None
                if time.monotonic() >= deadline:
                    _kill_session(process)
                    raise
                continue
            return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)

    def stream(
        self,
        argv: Sequence[str],
        *,
        on_line: Callable[[str], None],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        user: str | None = None,
        secrets: Sequence[str] = (),
        sandbox: SandboxSpec | None = None,
        clean_env: bool = False,
    ) -> CommandResult:
        _refuse_user_with_sandbox(user, sandbox)
        if sandbox is not None:
            return self._run_sandboxed(
                argv,
                sandbox,
                cwd=cwd,
                env=env,
                timeout=timeout,
                secrets=secrets,
                input=None,
                stdin_path=None,
                on_line=on_line,
            )
        args, run_env, redacted = self._prepare(argv, env, user, secrets, clean_env=clean_env)
        result = self._execute_stream(
            args, run_env, redacted, cwd=cwd, timeout=timeout, on_line=on_line
        )
        _notify_execution(_redact(_validate(argv), secrets), cwd=cwd, result=result, user=user)
        return result

    def _execute_stream(
        self,
        args: list[str],
        run_env: dict[str, str],
        redacted: tuple[str, ...],
        *,
        cwd: Path | None,
        timeout: int,
        on_line: Callable[[str], None],
        strip_cr: bool = False,
    ) -> CommandResult:
        """
        Run a prepared command, delivering its merged output line by line.

        Args:
            args: Final argv.
            run_env: Complete environment.
            redacted: Argv for the result and for messages.
            cwd: Working directory.
            timeout: Deadline in seconds.
            on_line: Called once per line.
            strip_cr: Drop a trailing carriage return from each line, for
                output that came through a pseudo-terminal.

        Returns:
            The outcome, with the accumulated output in ``stdout``.

        Raises:
            CommandCancelled: When the command's operation is cancelled.
        """
        cancel = _cancel_scope(redacted)
        started = time.monotonic()
        collected: list[str] = []
        try:
            process = subprocess.Popen(
                args,
                cwd=str(cwd) if cwd else None,
                env=run_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                # Its own session only inside a cancel scope, where the whole
                # tree must be killable; outside one, a Ctrl+C at the
                # terminal has to keep reaching the child.
                start_new_session=cancel is not None,
            )
        except FileNotFoundError:
            return CommandResult(
                argv=redacted,
                exit_code=EXIT_NOT_FOUND,
                stderr=f"Command not found: {args[0]}",
                duration=time.monotonic() - started,
            )

        # Reading the pipe directly would block past the deadline whenever the
        # child goes quiet, which is exactly what a hung build does. A reader
        # thread keeps the deadline enforceable no matter what the child emits.
        assert process.stdout is not None  # noqa: S101 - narrows the type for mypy
        lines: queue.Queue[str | None] = queue.Queue()

        def _pump(stream: IO[str]) -> None:
            try:
                for raw in stream:
                    line = raw.rstrip("\n")
                    lines.put(line.rstrip("\r") if strip_cr else line)
            finally:
                lines.put(None)

        reader = threading.Thread(target=_pump, args=(process.stdout,), daemon=True)
        reader.start()

        deadline = started + timeout
        timed_out = False
        while True:
            if cancel is not None and cancel.is_set():
                _kill_session(process, drain=False)
                reader.join(timeout=1)
                raise CommandCancelled(f"Cancelled: {' '.join(redacted)}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                line = lines.get(timeout=min(remaining, CANCEL_POLL_INTERVAL))
            except queue.Empty:
                continue
            if line is None:
                break
            collected.append(line)
            on_line(line)

        if timed_out and cancel is not None:
            _kill_session(process, drain=False)
        elif timed_out:
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            timed_out = True
        reader.join(timeout=1)

        return CommandResult(
            argv=redacted,
            exit_code=EXIT_TIMEOUT if timed_out else process.returncode,
            stdout="\n".join(collected),
            stderr=f"Command timed out after {timeout}s" if timed_out else "",
            duration=time.monotonic() - started,
            timed_out=timed_out,
        )

    def _prepare_sandbox(
        self,
        argv: Sequence[str],
        spec: SandboxSpec,
        *,
        cwd: Path | None,
        env: Mapping[str, str] | None,
        timeout: int,
        secrets: Sequence[str],
    ) -> _SandboxedCommand | CommandResult:
        """
        Write a sandboxed command's environment and build the ``systemd-run`` that runs it.

        The one place a sandboxed command is prepared, for ``run``, ``stream``
        and ``pty`` alike. The environment goes into a 0600 file on tmpfs that
        systemd reads as root, never into ``-E`` (which ``systemctl show``
        prints to any local account); the caller removes it when the command is
        over.

        Args:
            argv: The command.
            spec: The confinement.
            cwd: Its working directory, unless the spec names one.
            env: Its environment, over the allow-list.
            timeout: Deadline in seconds.
            secrets: Values to redact.

        Returns:
            The prepared command; or the result that says it cannot run (the
            program is not in the sandbox's PATH, the runtime directory is not
            writable), already announced to the execution listeners.

        Raises:
            CommandCancelled: When the operation is cancelled.
        """
        inner = _validate(argv)
        redacted_inner = _redact(inner, secrets)
        _cancel_scope(redacted_inner)
        if shutil.which(inner[0], path=SANDBOX_PATH) is None:
            result = CommandResult(
                argv=redacted_inner,
                exit_code=EXIT_NOT_FOUND,
                stderr=f"Command not found in the sandbox's PATH ({SANDBOX_PATH}): {inner[0]}",
            )
            _notify_execution(redacted_inner, cwd=cwd, result=result, user=spec.user)
            return result

        unit = sandbox_unit_name(spec)
        runtime = sandbox_runtime_dir()
        env_file = runtime / f"{unit}.env"
        try:
            text = environment_file_text(sandbox_environment(spec, env, os.environ))
            runtime.mkdir(parents=True, exist_ok=True)
            os.chmod(runtime, 0o700)
            empty = sandbox_empty_file()
            if spec.masked_files and not empty.is_file():
                fd = os.open(empty, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
                os.close(fd)
            # Created 0600 and exclusively: the file holds the build's
            # secrets, and a name that already exists is not ours to write.
            fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
        except (OSError, ValueError) as exc:
            result = CommandResult(
                argv=redacted_inner,
                exit_code=EXIT_SANDBOX_FAILED,
                stderr=f"The sandbox could not be prepared: {exc}",
            )
            _notify_execution(redacted_inner, cwd=cwd, result=result, user=spec.user)
            return result

        prefix = sandbox_prefix(spec, unit=unit, env_file=env_file, cwd=cwd, timeout=timeout)
        wrapped = [*prefix, *escape_systemd_argv(inner)]
        redacted = (*prefix, *escape_systemd_argv(redacted_inner))
        if self._on_command is not None:
            self._on_command(redacted)
        return _SandboxedCommand(
            unit=unit,
            runtime=runtime,
            env_file=env_file,
            wrapped=wrapped,
            redacted=redacted,
            redacted_inner=redacted_inner,
        )

    def _run_sandboxed(
        self,
        argv: Sequence[str],
        spec: SandboxSpec,
        *,
        cwd: Path | None,
        env: Mapping[str, str] | None,
        timeout: int,
        secrets: Sequence[str],
        input: str | None,
        stdin_path: Path | None,
        on_line: Callable[[str], None] | None,
    ) -> CommandResult:
        """
        Run a command in a transient unit, confined as ``spec`` says.

        The environment goes into a 0600 file on tmpfs that systemd reads as
        root, never into ``-E`` (which ``systemctl show`` prints to any local
        account). A cancellation or the deadline stops the unit by name,
        because killing ``systemd-run`` leaves the unit running; the unit's
        ``RuntimeMaxSec`` stops it if this process dies first.

        Args:
            argv: The command.
            spec: The confinement.
            cwd: Its working directory, unless the spec names one.
            env: Its environment, over the allow-list.
            timeout: Deadline in seconds.
            secrets: Values to redact.
            input: Text for stdin, or None.
            stdin_path: A file for stdin, or None.
            on_line: Called per line of merged output, to stream; None to
                capture both streams.

        Returns:
            The outcome, with ``sandbox_unit`` and ``sandbox_result`` set.

        Raises:
            CommandCancelled: When the operation is cancelled; the unit has
                been stopped.
        """
        prepared = self._prepare_sandbox(
            argv, spec, cwd=cwd, env=env, timeout=timeout, secrets=secrets
        )
        if isinstance(prepared, CommandResult):
            return prepared
        unit, runtime, env_file = prepared.unit, prepared.runtime, prepared.env_file
        wrapped, redacted, redacted_inner = (
            prepared.wrapped,
            prepared.redacted,
            prepared.redacted_inner,
        )
        # systemd-run itself only talks to the manager; it gets a fixed PATH
        # so it resolves the program where the sandbox will find it.
        client_env = {"PATH": SANDBOX_PATH, "LANG": "C.UTF-8"}
        try:
            try:
                if on_line is None:
                    raw = self._execute(
                        wrapped,
                        client_env,
                        redacted,
                        cwd=None,
                        timeout=timeout,
                        input=input,
                        stdin_path=stdin_path,
                    )
                else:
                    raw = self._execute_stream(
                        wrapped,
                        client_env,
                        redacted,
                        cwd=None,
                        timeout=timeout,
                        on_line=on_line,
                        strip_cr=spec.pty,
                    )
            except CommandCancelled:
                self._stop_unit(unit)
                raise
            if raw.timed_out:
                self._stop_unit(unit)
            if spec.pty:
                raw = replace(raw, stdout=raw.stdout.replace("\r\n", "\n"))
            result = interpret_sandbox_result(
                raw,
                read_sandbox_marker(unit, runtime),
                spec,
                argv=redacted_inner,
                unit=unit,
                timeout=timeout,
            )
        finally:
            with contextlib.suppress(OSError):
                env_file.unlink()
            read_sandbox_marker(unit, runtime)
        _notify_execution(redacted_inner, cwd=cwd, result=result, user=spec.user, sandbox_unit=unit)
        return result

    def _stop_unit(self, unit: str) -> None:
        """
        Stop a sandbox's unit, and everything in it.

        Not through :meth:`run`: this is what a cancellation does, and the
        cancelled scope would refuse to start it.

        Args:
            unit: The unit, without ``.service``.
        """
        argv = ["systemctl", "stop", f"{unit}.service"]
        started = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=SANDBOX_STOP_TIMEOUT,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            _log.warning("Could not stop the sandbox %s: %s", unit, exc)
            return
        result = CommandResult(
            argv=tuple(argv),
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            duration=time.monotonic() - started,
        )
        if not result.success:
            _log.warning("Could not stop the sandbox %s: %s", unit, result.stderr.strip())
        _notify_execution(argv, cwd=None, result=result)

    def capture_to_file(
        self,
        argv: Sequence[str],
        destination: Path,
        *,
        compress: bool = False,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        user: str | None = None,
        secrets: Sequence[str] = (),
    ) -> CommandResult:
        args, run_env, redacted = self._prepare(argv, env, user, secrets)
        # Checked before the destination is created; a dump already running
        # is not interrupted, because a cancel that leaves half a backup
        # needs its own undo, which no caller has asked for yet.
        _cancel_scope(redacted)
        destination.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()

        # The file is opened before the process starts and created 0600, so a
        # dump is never briefly world-readable.
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as sink:
                producer = subprocess.Popen(
                    args,
                    cwd=str(cwd) if cwd else None,
                    env=run_env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE if compress else sink,
                    stderr=subprocess.PIPE,
                )
                gzip_proc = None
                if compress:
                    assert producer.stdout is not None  # noqa: S101 - narrows the type
                    gzip_proc = subprocess.Popen(
                        ["gzip", "-c"],
                        stdin=producer.stdout,
                        stdout=sink,
                        stderr=subprocess.PIPE,
                    )
                    # Let the producer receive SIGPIPE if gzip dies.
                    producer.stdout.close()

                try:
                    _, err = producer.communicate(timeout=timeout)
                    gz_err = b""
                    if gzip_proc is not None:
                        _, gz_err = gzip_proc.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    producer.kill()
                    if gzip_proc is not None:
                        gzip_proc.kill()
                    # A dump that ran out of time has written a prefix of the
                    # data. Leaving it behind would put a truncated file with a
                    # valid name in the backup directory, where it would be
                    # listed as a backup and eventually fed to psql on restore.
                    destination.unlink(missing_ok=True)
                    timed_out = CommandResult(
                        argv=redacted,
                        exit_code=EXIT_TIMEOUT,
                        stderr=f"Command timed out after {timeout}s",
                        duration=time.monotonic() - started,
                        timed_out=True,
                    )
                    _notify_execution(
                        _redact(_validate(argv), secrets), cwd=cwd, result=timed_out, user=user
                    )
                    return timed_out
        except FileNotFoundError as exc:
            return CommandResult(
                argv=redacted,
                exit_code=EXIT_NOT_FOUND,
                stderr=str(exc),
                duration=time.monotonic() - started,
            )

        exit_code = producer.returncode
        stderr = (err or b"").decode(errors="replace")
        if gzip_proc is not None and gzip_proc.returncode != 0:
            exit_code = exit_code or gzip_proc.returncode
            stderr += (gz_err or b"").decode(errors="replace")

        # A failed dump must not leave a plausible-looking backup behind.
        if exit_code != 0:
            destination.unlink(missing_ok=True)

        result = CommandResult(
            argv=redacted,
            exit_code=exit_code,
            stderr=stderr,
            duration=time.monotonic() - started,
        )
        _notify_execution(_redact(_validate(argv), secrets), cwd=cwd, result=result, user=user)
        return result

    def start(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        user: str | None = None,
        secrets: Sequence[str] = (),
    ) -> ProcessHandle:
        args, run_env, redacted = self._prepare(argv, env, user, secrets)
        _cancel_scope(redacted)
        try:
            process = subprocess.Popen(
                args,
                cwd=str(cwd) if cwd else None,
                env=run_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                # Its own session: terminate() kills the whole group, and a
                # Ctrl+C meant for the CLI in front of it does not reach it
                # half-way through a request the owner is still making.
                start_new_session=True,
                # Nothing of the parent's but the three pipes, whatever a
                # descriptor's inheritable flag says: a tunnel that kept the
                # console's listening socket would hold its port after the
                # console exited. Spelled out because it is load-bearing.
                close_fds=True,
            )
        except FileNotFoundError:
            return _EndedProcess(redacted, EXIT_NOT_FOUND, f"Command not found: {args[0]}")
        except PermissionError as exc:
            return _EndedProcess(redacted, EXIT_NOT_FOUND, str(exc))
        _notify_execution(_redact(_validate(argv), secrets), cwd=cwd, result=None, user=user)
        return _SubprocessHandle(process, redacted)

    def pty(
        self,
        argv: Sequence[str],
        *,
        max_seconds: int,
        idle_seconds: int,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        user: str | None = None,
        secrets: Sequence[str] = (),
        sandbox: SandboxSpec | None = None,
        rows: int = PTY_DEFAULT_ROWS,
        cols: int = PTY_DEFAULT_COLS,
    ) -> PtyHandle:
        _require_limits(max_seconds, idle_seconds)
        _refuse_user_with_sandbox(user, sandbox)
        stop_unit: Callable[[], None] | None = None
        cleanup: list[Callable[[], None]] = []
        account: str | None
        if sandbox is not None:
            prepared = self._prepare_sandbox(
                argv, sandbox, cwd=cwd, env=env, timeout=max_seconds, secrets=secrets
            )
            if isinstance(prepared, CommandResult):
                return _EndedPty(prepared.argv, prepared.exit_code, prepared.stderr)
            args, redacted = prepared.wrapped, prepared.redacted
            # systemd-run itself only talks to the manager; the program's own
            # environment is the file it reads.
            run_env = {"PATH": SANDBOX_PATH, "LANG": "C.UTF-8"}
            if env and "TERM" in env:
                run_env["TERM"] = env["TERM"]
            account = sandbox.user
            unit, runtime, env_file = prepared.unit, prepared.runtime, prepared.env_file

            def stop_unit() -> None:
                self._stop_unit(unit)

            def remove_environment() -> None:
                env_file.unlink(missing_ok=True)

            def drop_marker() -> None:
                # Reading the result marker is what removes it.
                read_sandbox_marker(unit, runtime)

            cleanup = [remove_environment, drop_marker]
        else:
            inner = _validate(argv)
            args = inner if user is None else [*runuser_prefix(user), *inner]
            redacted = _redact(args, secrets)
            _cancel_scope(redacted)
            run_env = clean_environment(os.environ, PTY_ENV_ALLOW)
            if env:
                run_env.update(env)
            account = user
            if self._on_command is not None:
                self._on_command(redacted)
            if shutil.which(args[0], path=run_env.get("PATH") or SANDBOX_PATH) is None:
                return _EndedPty(redacted, EXIT_NOT_FOUND, f"Command not found: {args[0]}")

        try:
            master, slave = os.openpty()
        except OSError as exc:
            for step in cleanup:
                step()
            raise CommandError(
                "No terminal could be opened",
                details=f"The system refused a pseudo-terminal: {exc}. "
                "Close some sessions, or raise the limit of ptys (kernel.pty.max).",
            ) from exc
        process: subprocess.Popen[bytes] | None = None
        try:
            fcntl.ioctl(
                master,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", _clamp(rows, PTY_MAX_ROWS), _clamp(cols, PTY_MAX_COLS), 0, 0),
            )
            process = subprocess.Popen(
                [*_SESSION_LEADER, *args],
                cwd=str(cwd) if cwd and sandbox is None else None,
                env=run_env,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                # Nothing of the parent's but the terminal, whatever a
                # descriptor's inheritable flag says: a shell must not hold the
                # console's sockets.
                close_fds=True,
            )
        except (FileNotFoundError, PermissionError) as exc:
            os.close(master)
            for step in cleanup:
                step()
            return _EndedPty(
                redacted, EXIT_NOT_FOUND, f"Command not found: {exc.filename or args[0]}"
            )
        except OSError:
            os.close(master)
            for step in cleanup:
                step()
            raise
        finally:
            os.close(slave)
        _notify_execution(_redact(_validate(argv), secrets), cwd=cwd, result=None, user=account)
        return _SubprocessPty(
            process,
            master,
            redacted,
            max_seconds=max_seconds,
            idle_seconds=idle_seconds,
            rows=rows,
            cols=cols,
            stop_unit=stop_unit,
            cleanup=cleanup,
        )


# -- What only looks ------------------------------------------------------------


def _git_probes() -> tuple[tuple[object, ...], ...]:
    """
    The git commands Noust runs to look, with every prefix it runs them with.

    Returns:
        The shapes.
    """
    prefixes: tuple[tuple[str, ...], ...] = (
        (),
        # SourceManager's: no ext:: or file:: transport, whatever a URL says.
        ("-c", "protocol.ext.allow=never", "-c", "protocol.file.allow=never"),
        # SourceManager's reading a checkout another account owns, as that
        # account (get_repo_info): the exact checkout, never every directory.
        (
            "-c",
            "protocol.ext.allow=never",
            "-c",
            "protocol.file.allow=never",
            "-c",
            "safe.directory=/*",
        ),
        # SourceManager's root git in a checkout another account owns.
        (
            "-c",
            "protocol.ext.allow=never",
            "-c",
            "protocol.file.allow=never",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "log.showSignature=false",
        ),
        # migrate.py's look at a tree, as the account that owns it.
        ("-c", "safe.directory=/*", "--no-optional-locks"),
    )
    looks: tuple[tuple[object, ...], ...] = (
        ("rev-parse", ...),
        ("status", ...),
        ("describe", ...),
        # One value read by name: where a checkout fetches from (get_repo_info). Without it
        # `noust --dry-run app adopt` found no origin and refused every checkout.
        ("config", "--get", "*"),
        # The tags a commit contains, which is what an update by tag compares.
        ("tag", "--merged", "*"),
        # One file as a commit has it (the compose file that served, for its guard).
        ("cat-file", "blob", "*"),
        ("log", "-1", "--format=%s"),
        ("log", "-1", "--format=%s", "*"),
        ("ls-remote", "--exit-code", "*"),
        ("ls-remote", "--exit-code", "--", "*", "*"),
        ("ls-remote", "--symref", "--", "*", "*"),
    )
    return tuple(("git", *prefix, *look) for prefix in prefixes for look in looks)


def _compose_probes() -> tuple[tuple[object, ...], ...]:
    """
    The ``docker compose`` commands Noust runs to look, with the flags it pins.

    Returns:
        The shapes, for up to two profiles.
    """
    heads: tuple[tuple[str, ...], ...] = (
        ("docker", "compose"),
        ("docker", "compose", "-f", "*"),
        ("docker", "compose", "-p", "*", "-f", "*"),
    )
    profiles: tuple[tuple[str, ...], ...] = (
        (),
        ("--profile", "*"),
        ("--profile", "*", "--profile", "*"),
    )
    looks: tuple[tuple[object, ...], ...] = (("ps", ...), ("logs", ...), ("images", ...))
    shapes: list[tuple[object, ...]] = [("docker", "compose", "version")]
    for head in heads:
        for profile in profiles:
            shapes += [(*head, *profile, *look) for look in looks]
    return tuple(shapes)


#: Every argv shape Noust's core, managers and deployers run only to look.
#: A dry run executes these, because a rehearsal that cannot look at the
#: machine reports fiction; anything not declared counts as a change. The
#: language is described in the module docstring.
READ_ONLY_PROBES: tuple[tuple[object, ...], ...] = (
    # Programs that only ever report, whatever they are given.
    *(
        (program, ...)
        for program in (
            "cat",
            "df",
            "dpkg-query",
            "du",
            "getent",
            "grep",
            "head",
            "id",
            "ls",
            "lsb_release",
            "ps",
            "readlink",
            "stat",
            "tail",
            "uname",
            "which",
            "whoami",
        )
    ),
    # A version, and nothing else: "--version" among other arguments is an
    # option of whatever the command does.
    ("*", "--version"),
    ("hostname",),
    ("hostname", "-f"),
    ("hostname", "--fqdn"),
    ("hostname", "-s"),
    ("hostname", "-I"),
    # systemd: states and listings, never a verb that acts.
    ("systemctl", "status", ...),
    ("systemctl", "is-active", ...),
    ("systemctl", "is-enabled", ...),
    ("systemctl", "is-failed", ...),
    ("systemctl", "is-system-running"),
    ("systemctl", "show", ...),
    ("systemctl", "cat", ...),
    ("systemctl", "list-units", ...),
    ("systemctl", "list-timers", ...),
    ("systemctl", "list-unit-files", ...),
    ("systemctl", "--failed", "--no-legend", "--plain", "--no-pager"),
    # The journal, minus the options that rotate or delete it (vetoed below).
    ("journalctl", ...),
    # Web servers: test a configuration, print a version.
    ("nginx", "-t"),
    ("nginx", "-T"),
    ("nginx", "-v"),
    ("nginx", "-V"),
    ("nginx", "-t", "-c", "*"),
    *(
        (ctl, *args)
        for ctl in ("apache2ctl", "apachectl")
        for args in (
            ("configtest",),
            ("-t",),
            ("-t", "-f", "*"),
            ("-v",),
            ("-V",),
            ("-S",),
        )
    ),
    ("certbot", "certificates", ...),
    ("certbot", "plugins", ...),
    *_git_probes(),
    ("docker", "ps", ...),
    ("docker", "images", ...),
    ("docker", "info", ...),
    ("docker", "version", ...),
    ("docker", "inspect", ...),
    ("docker", "logs", ...),
    *_compose_probes(),
    # Packages: the update checker's cache-only probes (package_index).
    ("apt-cache", "policy", ...),
    ("apt-cache", "show", ...),
    ("apt-cache", "madison", ...),
    ("dpkg", "--print-architecture"),
    ("rpm", "-q", ...),
    ("rpm", "--query", ...),
    ("dnf", "--cacheonly", "info", "--available", "*"),
    ("yum", "--cacheonly", "info", "available", "*"),
    ("zypper", "--no-refresh", "--non-interactive", "info", "*"),
    # sshd's effective configuration and its syntax check ('noust fleet
    # authorize', the server's SSH checks), and what the client offers.
    ("sshd", "-T"),
    ("sshd", "-T", "-C", "*"),
    ("sshd", "-t"),
    ("ssh", "-Q", "*"),
    # Sealed secrets are decrypted stdin to stdout (noust.core.sealing); a
    # rehearsal has to read the secrets it reports on. -out is vetoed.
    ("openssl", "enc", ...),
    ("openssl", "x509", "-in", "*", "-noout", ...),
    ("openssl", "x509", "-noout", ...),
    # Sockets and firewalls, for the server's security checks.
    ("ss", "-Hltnup"),
    ("ss", "-ltnpH"),
    ("ss", "-lunpH"),
    ("ss", "-Htnp", "state", "established"),
    ("ufw", "status"),
    ("ufw", "status", "numbered"),
    ("ufw", "status", "verbose"),
    ("ufw", "show", "*"),
    ("firewall-cmd", "--state"),
    ("firewall-cmd", "--get-default-zone"),
    ("firewall-cmd", "--get-active-zones"),
    ("firewall-cmd", "--list-all"),
    ("firewall-cmd", "--zone=*", "--list-all"),
    ("firewall-cmd", "--permanent", "--list-all"),
    ("firewall-cmd", "--permanent", "--zone=*", "--list-all"),
    ("fail2ban-client", "ping"),
    ("fail2ban-client", "status"),
    ("fail2ban-client", "status", "*"),
    ("fail2ban-client", "-t"),
    ("fail2ban-client", "--test"),
    ("nft", "list", "ruleset"),
    ("nft", "-j", "list", "ruleset"),
    ("getenforce",),
    ("ip", "-4", "-o", "addr", "show", "scope", "global"),
)

#: Modules that declare read-only probes of their own, each in a module-level
#: ``READ_ONLY_PROBES`` tuple written in the same language. Imported the first
#: time a command is classified, so this module stays importable on its own.
PROBE_MODULES: tuple[str, ...] = (
    "noust.managers.server.probes",
    "noust.managers.stack_databases",
    "noust.deployers.compose_adopt",
    "noust.managers.siteconf.regex",
    "noust.managers.database.settings",
    "noust.managers.database.rpm_flavours",
    "noust.managers.unit_hardening",
    "noust.managers.cron_manager",
    "noust.managers.app_disk",
    "noust.managers.kernel_quota",
    "noust.managers.private_networks",
    "noust.managers.stack_networks",
    "noust.deployers.importers.dokku",
)

#: Options that turn any declared probe of a program into a change. Checked
#: before the shapes, so an open-ended declaration (``journalctl ...``) cannot
#: let ``journalctl --vacuum-size=100M`` run under ``--dry-run``.
MUTATING_OPTIONS: dict[str, tuple[str, ...]] = {
    "journalctl": (
        "--vacuum-size*",
        "--vacuum-time*",
        "--vacuum-files*",
        "--rotate",
        "--flush",
        "--sync",
        "--relinquish-var",
        "--smart-relinquish-var",
        "--setup-keys",
        "--update-catalog",
    ),
    "openssl": ("-out", "-out=*", "-keyout", "-CAcreateserial", "-CAserial"),
    "git": ("--output", "--output=*", "--upload-pack", "--upload-pack=*", "--exec", "--exec=*"),
}

_declared_lock = threading.Lock()
_declared: tuple[tuple[object, ...], ...] | None = None


def declared_probes() -> tuple[tuple[object, ...], ...]:
    """
    Return every declared probe: the core's and those of :data:`PROBE_MODULES`.

    A declaring module that cannot be imported contributes nothing, which
    only makes a dry run execute less.

    Returns:
        The shapes.
    """
    global _declared
    with _declared_lock:
        if _declared is None:
            shapes = list(READ_ONLY_PROBES)
            for name in PROBE_MODULES:
                try:
                    module = importlib.import_module(name)
                except ImportError as exc:
                    _log.debug("Probe declarations of %s not loaded: %s", name, exc)
                    continue
                shapes.extend(getattr(module, "READ_ONLY_PROBES", ()))
            _declared = tuple(shapes)
        return _declared


def forget_declared_probes() -> None:
    """Load the declarations again on the next classification; for tests."""
    global _declared
    with _declared_lock:
        _declared = None


def _element_matches(actual: str, expected: str) -> bool:
    """
    Compare one argument with one element of a shape.

    Args:
        actual: The argument.
        expected: The shape's element.

    Returns:
        True when it matches: literally, or as a glob when the element has a
        ``*``. A bare ``*`` is an operand and never matches an option.
    """
    if expected == "*":
        return not actual.startswith("-")
    if "*" in expected:
        return fnmatch.fnmatchcase(actual, expected)
    return actual == expected


def matches_shape(argv: Sequence[str], shape: Sequence[object]) -> bool:
    """
    Tell whether a command has a declared shape.

    Args:
        argv: The command. Its program is compared by name, so
            ``/usr/bin/whoami`` is ``whoami``.
        shape: A declaration, program first; a trailing ``...`` accepts any
            further arguments.

    Returns:
        True when every element matches and, without ``...``, nothing is left.
    """
    if not argv or not shape:
        return False
    open_ended = shape[-1] is Ellipsis
    fixed = shape[:-1] if open_ended else shape
    if len(argv) < len(fixed) or (not open_ended and len(argv) != len(fixed)):
        return False
    program = Path(str(argv[0])).name
    if not _element_matches(program, str(fixed[0])):
        return False
    return all(
        _element_matches(str(actual), str(expected))
        for actual, expected in zip(argv[1:], fixed[1:], strict=False)
    )


def is_read_only(argv: Sequence[str]) -> bool:
    """
    Report whether a command only observes the system.

    Only a command whose exact shape is declared counts (see the module
    docstring); anything else is assumed to change something, because a dry
    run that quietly performs a real action is worse than one that refuses
    to guess.

    Args:
        argv: The argument vector to classify.

    Returns:
        True when the command is known not to change anything.
    """
    if not argv:
        return False
    args = [str(arg) for arg in argv]
    program = Path(args[0]).name
    vetoes = MUTATING_OPTIONS.get(program, ())
    if any(fnmatch.fnmatchcase(arg, veto) for arg in args[1:] for veto in vetoes):
        return False
    return any(matches_shape(args, shape) for shape in declared_probes())


def _only_if_clean(clean_env: bool) -> dict[str, Any]:
    """
    Pass ``clean_env`` on only when it asks for something.

    An inner runner written before the argument existed (a test double that
    spells out its parameters) keeps working for every call that does not
    use it.

    Args:
        clean_env: The caller's argument.

    Returns:
        The keyword to add, or nothing.
    """
    return {"clean_env": True} if clean_env else {}


class DryRunRunner(CommandRunner):
    """
    Rehearses instead of acting. This is what ``--dry-run`` actually means.

    The flag used to be wired per command, which meant it was honoured in three
    code paths and silently ignored in the ninety others, including every
    destructive one. Enforcing it here makes it true for the whole program by
    construction: a command that would change the machine cannot reach the
    machine, whatever the calling code believes.

    Read-only probes still run, because a rehearsal that cannot look at the
    system reports fiction. A sandboxed command is a build, and never does.
    """

    def __init__(
        self, inner: CommandRunner, *, on_skip: Callable[[tuple[str, ...]], None] | None = None
    ):
        """
        Args:
            inner: Runner used for the commands that only observe.
            on_skip: Called with each argv that was not executed, so the CLI
                can show the user what a real run would have done.
        """
        self._inner = inner
        self._on_skip = on_skip
        self.skipped: list[tuple[str, ...]] = []

    def _skip(self, argv: Sequence[str], secrets: Sequence[str]) -> CommandResult:
        """
        Record a command that a real run would have executed.

        Args:
            argv: The argument vector that was not run.
            secrets: Values to keep out of the record.

        Returns:
            A successful result, so callers proceed through the rehearsal.
        """
        redacted = _redact(_validate(argv), secrets)
        self.skipped.append(redacted)
        if self._on_skip is not None:
            self._on_skip(redacted)
        return CommandResult(argv=redacted, exit_code=0, stdout="")

    def exists(self, program: str) -> bool:
        return self._inner.exists(program)

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        input: str | None = None,
        stdin_path: Path | None = None,
        user: str | None = None,
        check: bool = False,
        secrets: Sequence[str] = (),
        sandbox: SandboxSpec | None = None,
        clean_env: bool = False,
    ) -> CommandResult:
        _refuse_user_with_sandbox(user, sandbox)
        if sandbox is None and is_read_only(argv):
            return self._inner.run(
                argv,
                cwd=cwd,
                env=env,
                timeout=timeout,
                input=input,
                stdin_path=stdin_path,
                user=user,
                check=check,
                secrets=secrets,
                **_only_if_clean(clean_env),
            )
        return self._skip(argv, secrets)

    def stream(
        self,
        argv: Sequence[str],
        *,
        on_line: Callable[[str], None],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        user: str | None = None,
        secrets: Sequence[str] = (),
        sandbox: SandboxSpec | None = None,
        clean_env: bool = False,
    ) -> CommandResult:
        _refuse_user_with_sandbox(user, sandbox)
        if sandbox is None and is_read_only(argv):
            return self._inner.stream(
                argv,
                on_line=on_line,
                cwd=cwd,
                env=env,
                timeout=timeout,
                user=user,
                secrets=secrets,
                **_only_if_clean(clean_env),
            )
        return self._skip(argv, secrets)

    def capture_to_file(
        self,
        argv: Sequence[str],
        destination: Path,
        *,
        compress: bool = False,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        user: str | None = None,
        secrets: Sequence[str] = (),
    ) -> CommandResult:
        # Never write the destination: a rehearsal that leaves a file behind is
        # not a rehearsal.
        return self._skip(argv, secrets)

    def start(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        user: str | None = None,
        secrets: Sequence[str] = (),
    ) -> ProcessHandle:
        if is_read_only(argv):
            return self._inner.start(argv, cwd=cwd, env=env, user=user, secrets=secrets)
        skipped = self._skip(argv, secrets)
        # Reported as a process that ended at once, with the reason as its
        # last words, so an owner waiting for it explains the rehearsal
        # instead of inventing a failure.
        return _EndedProcess(skipped.argv, 0, "Not started: this is a dry run")

    def pty(
        self,
        argv: Sequence[str],
        *,
        max_seconds: int,
        idle_seconds: int,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        user: str | None = None,
        secrets: Sequence[str] = (),
        sandbox: SandboxSpec | None = None,
        rows: int = PTY_DEFAULT_ROWS,
        cols: int = PTY_DEFAULT_COLS,
    ) -> PtyHandle:
        _require_limits(max_seconds, idle_seconds)
        _refuse_user_with_sandbox(user, sandbox)
        # A terminal is somebody typing: there is nothing to rehearse and
        # nothing that only looks.
        skipped = self._skip(argv, secrets)
        return _EndedPty(skipped.argv, 0, "Not started: this is a dry run")


@dataclass
class FakeCommand:
    """A scripted response used by :class:`FakeRunner`."""

    match: tuple[str, ...]
    result: CommandResult


class FakeRunner(CommandRunner):
    """
    A runner for tests. Records what was asked and replays scripted answers.

    Tests assert on the exact argv a manager builds, which is the part that
    matters and the part that used to be untestable.

    A sandboxed command is recorded as the ``systemd-run`` argv the real
    runner would execute, and its ``env`` as the complete environment its
    file would hold. A script matches either that argv or the command inside
    it, so ``script(["npm", "ci"], exit_code=1)`` fails a sandboxed install
    too.
    """

    def __init__(self, *, default_exit_code: int = 0):
        """
        Args:
            default_exit_code: Exit code returned for commands with no scripted
                response.
        """
        self.calls: list[tuple[str, ...]] = []
        #: The ``env`` each call was given, index for index with ``calls``.
        self.envs: list[Mapping[str, str] | None] = []
        self.inputs: list[str | None] = []
        self.stdin_paths: list[Path] = []
        self.written: dict[Path, tuple[str, ...]] = {}
        #: Every long-lived process :meth:`start` returned, in order.
        self.processes: list[FakeProcess] = []
        #: Every terminal :meth:`pty` opened, in order.
        self.ptys: list[FakePty] = []
        #: Whether a terminal answers like a tiny shell instead of staying silent.
        self.pty_echo = False
        #: The clock a fake terminal measures its limits with; a test replaces it.
        self.pty_clock: Callable[[], float] = time.monotonic
        #: The sandbox each run or stream was given, in order, None for none.
        self.sandboxes: list[SandboxSpec | None] = []
        #: The command inside each sandboxed call, with its spec.
        self.sandboxed: list[tuple[tuple[str, ...], SandboxSpec]] = []
        #: Whether each run or stream asked for a clean environment, in order.
        self.clean_envs: list[bool] = []
        self._scripted: list[FakeCommand] = []
        self._default_exit_code = default_exit_code
        self._known_programs: set[str] | None = None
        # Per thread: a sandboxed call sets the command inside it for the length of its lookup, and
        # with one runner shared by a job's worker, the API's threads and the monitor, a single
        # attribute let one call clear another's mid-lookup (a one-off command answered nothing).
        self._inner = threading.local()

    @property
    def _inner_argv(self) -> tuple[str, ...] | None:
        """The command inside the sandboxed call this thread is making, None when it is making none."""
        value: tuple[str, ...] | None = getattr(self._inner, "argv", None)
        return value

    @_inner_argv.setter
    def _inner_argv(self, value: tuple[str, ...] | None) -> None:
        self._inner.argv = value

    def script(
        self,
        argv_prefix: Sequence[str],
        *,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
    ) -> FakeRunner:
        """
        Register a canned response for commands starting with a prefix.

        Later registrations win, so a test can override a fixture's default.

        Args:
            argv_prefix: Leading arguments that identify the command.
            stdout: Standard output to return.
            stderr: Standard error to return.
            exit_code: Exit status to return.

        Returns:
            This runner, so calls can be chained.
        """
        self._scripted.append(
            FakeCommand(
                match=tuple(str(a) for a in argv_prefix),
                result=CommandResult(
                    argv=tuple(str(a) for a in argv_prefix),
                    exit_code=exit_code,
                    stdout=stdout,
                    stderr=stderr,
                ),
            )
        )
        return self

    def only_knows(self, *programs: str) -> FakeRunner:
        """
        Restrict which programs :meth:`exists` reports as installed.

        Args:
            programs: Executable names that should be considered present.

        Returns:
            This runner, so calls can be chained.
        """
        self._known_programs = set(programs)
        return self

    def exists(self, program: str) -> bool:
        if self._known_programs is None:
            return True
        return program in self._known_programs

    def _lookup(
        self,
        argv: Sequence[str],
        user: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """
        Find the scripted response for a call, recording the call first.

        Args:
            argv: The argument vector the code under test built.
            user: Account the real runner would switch to, if any. Folded into
                the recorded and matched argv the same way
                :meth:`SubprocessRunner._prepare` folds it in, so a test's
                ``.script(...)`` and ``.calls`` assertions see exactly what
                would execute for real.
            env: Extra environment the call was given, recorded in
                :attr:`envs` next to the argv.

        Returns:
            The scripted result, or a default success.
        """
        args = _validate(argv)
        if user is not None:
            args = [*runuser_prefix(user), *args]
        recorded = tuple(args)
        inner = self._inner_argv
        # Like the real runner: a command of a cancelled operation never runs,
        # so it is not recorded as having run either.
        _cancel_scope(inner or recorded)
        self.calls.append(recorded)
        self.envs.append(dict(env) if env is not None else None)
        for scripted in reversed(self._scripted):
            size = len(scripted.match)
            if recorded[:size] == scripted.match or (
                inner is not None and inner[:size] == scripted.match
            ):
                return replace(scripted.result, argv=recorded)
        return CommandResult(argv=recorded, exit_code=self._default_exit_code)

    def _call(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None,
        env: Mapping[str, str] | None,
        timeout: int,
        user: str | None,
        sandbox: SandboxSpec | None,
        clean_env: bool,
        secrets: Sequence[str],
    ) -> CommandResult:
        """
        Record one run or stream and return its scripted result.

        Args:
            argv: The command.
            cwd: Its working directory.
            env: Its environment.
            timeout: Its deadline.
            user: The account, when not sandboxed.
            sandbox: The confinement, if any.
            clean_env: Whether it asked for a clean environment.
            secrets: Values to redact from what listeners hear.

        Returns:
            The scripted result.
        """
        _refuse_user_with_sandbox(user, sandbox)
        if sandbox is None:
            result = self._lookup(argv, user, env)
            self.sandboxes.append(None)
            self.clean_envs.append(clean_env)
            _notify_execution(_redact(_validate(argv), secrets), cwd=cwd, result=result, user=user)
            return result
        inner = tuple(_validate(argv))
        unit = sandbox_unit_name(sandbox)
        env_file = sandbox_runtime_dir() / f"{unit}.env"
        wrapped = [
            *sandbox_prefix(sandbox, unit=unit, env_file=env_file, cwd=cwd, timeout=timeout),
            *escape_systemd_argv(inner),
        ]
        self._inner_argv = inner
        try:
            result = self._lookup(wrapped, None, sandbox_environment(sandbox, env, os.environ))
        finally:
            self._inner_argv = None
        self.sandboxes.append(sandbox)
        self.sandboxed.append((inner, sandbox))
        self.clean_envs.append(True)
        result = replace(
            result,
            argv=_redact(inner, secrets),
            sandbox_unit=unit,
            sandbox_result=result.sandbox_result or ("success" if result.success else "exit-code"),
        )
        _notify_execution(
            _redact(inner, secrets), cwd=cwd, result=result, user=sandbox.user, sandbox_unit=unit
        )
        return result

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        input: str | None = None,
        stdin_path: Path | None = None,
        user: str | None = None,
        check: bool = False,
        secrets: Sequence[str] = (),
        sandbox: SandboxSpec | None = None,
        clean_env: bool = False,
    ) -> CommandResult:
        self.inputs.append(input)
        if stdin_path is not None:
            self.stdin_paths.append(stdin_path)
        result = self._call(
            argv,
            cwd=cwd,
            env=env,
            timeout=timeout,
            user=user,
            sandbox=sandbox,
            clean_env=clean_env,
            secrets=secrets,
        )
        return result.check() if check else result

    def stream(
        self,
        argv: Sequence[str],
        *,
        on_line: Callable[[str], None],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        user: str | None = None,
        secrets: Sequence[str] = (),
        sandbox: SandboxSpec | None = None,
        clean_env: bool = False,
    ) -> CommandResult:
        result = self._call(
            argv,
            cwd=cwd,
            env=env,
            timeout=timeout,
            user=user,
            sandbox=sandbox,
            clean_env=clean_env,
            secrets=secrets,
        )
        for line in result.stdout.splitlines():
            on_line(line)
        return result

    def capture_to_file(
        self,
        argv: Sequence[str],
        destination: Path,
        *,
        compress: bool = False,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        user: str | None = None,
        secrets: Sequence[str] = (),
    ) -> CommandResult:
        scripted = self._scripted_for(tuple(str(part) for part in argv))
        result = self._lookup(argv, user, env)
        self.written[destination] = result.argv
        if result.success:
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = Path(str(argv[-1]))
            if not scripted and tuple(argv[:2]) == ("gzip", "-dc") and source.is_file():
                # An unscripted decompression really decompresses, so what
                # the code under test reads next is the dump, not nothing.
                destination.write_bytes(gzip.decompress(source.read_bytes()))
            else:
                # A dump nobody scripted still looks like one: an engine that
                # exits 0 and writes nothing is refused by the managers, so a
                # fake that wrote nothing would make every test about something
                # else fail on that. A test that wants the empty case scripts it.
                if scripted or result.stdout:
                    destination.write_text(result.stdout)
                elif compress:
                    destination.write_bytes(gzip.compress(b"-- dump\n"))
                else:
                    destination.write_text("-- dump\n")
        _notify_execution(_redact(_validate(argv), secrets), cwd=cwd, result=result, user=user)
        return result

    def _scripted_for(self, argv: tuple[str, ...]) -> bool:
        """
        Say whether a test scripted an answer for a command.

        Args:
            argv: The command, as given.

        Returns:
            True when a registered prefix matches it.
        """
        return any(argv[: len(item.match)] == item.match for item in self._scripted)

    def start(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        user: str | None = None,
        secrets: Sequence[str] = (),
    ) -> ProcessHandle:
        """
        Start a scripted long-lived process.

        A command scripted with exit code 0 starts running and keeps running
        until the test calls :meth:`FakeProcess.die` or the code under test
        terminates it; any other exit code starts it already dead, with the
        scripted stderr as its last words. Every process is kept in
        :attr:`processes`, in order.

        Args:
            argv: Program and arguments.
            cwd: Ignored.
            env: Recorded in :attr:`envs`.
            user: Folded into the recorded argv, as for :meth:`run`.
            secrets: Ignored.

        Returns:
            The fake process.
        """
        result = self._lookup(argv, user, env)
        process = FakeProcess(
            result.argv,
            alive=result.exit_code == 0,
            exit_code=result.exit_code,
            stderr=result.stderr,
        )
        self.processes.append(process)
        if process.is_alive():
            _notify_execution(_redact(_validate(argv), secrets), cwd=cwd, result=None, user=user)
        return process

    def pty(
        self,
        argv: Sequence[str],
        *,
        max_seconds: int,
        idle_seconds: int,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        user: str | None = None,
        secrets: Sequence[str] = (),
        sandbox: SandboxSpec | None = None,
        rows: int = PTY_DEFAULT_ROWS,
        cols: int = PTY_DEFAULT_COLS,
    ) -> PtyHandle:
        """
        Open a scripted terminal.

        The call is recorded exactly as :meth:`run` records one (a sandboxed
        one as the ``systemd-run`` argv the real runner would execute). A
        command scripted with exit code 0 opens a terminal the test drives
        (:meth:`FakePty.feed`); any other exit code opens one already ended,
        with the scripted stderr as its last words. With :attr:`pty_echo` set it
        answers like a tiny shell, which is what the console's development
        server uses. Every terminal is kept in :attr:`ptys`, in order.

        Args:
            argv: Program and arguments.
            max_seconds: The longest the session may last.
            idle_seconds: The longest it may go without anything typed.
            cwd: Ignored.
            env: Recorded in :attr:`envs`.
            user: Folded into the recorded argv, as for :meth:`run`.
            secrets: Redacted from what listeners hear.
            sandbox: Recorded in :attr:`sandboxed`.
            rows: The terminal's height.
            cols: The terminal's width.

        Returns:
            The fake terminal.

        Raises:
            ValueError: A limit that is not positive.
        """
        _require_limits(max_seconds, idle_seconds)
        result = self._call(
            argv,
            cwd=cwd,
            env=env,
            timeout=max_seconds,
            user=user,
            sandbox=sandbox,
            clean_env=True,
            secrets=secrets,
        )
        alive = result.exit_code == 0
        account = sandbox.user if sandbox is not None else (user or "root")
        handle = FakePty(
            result.argv,
            max_seconds=max_seconds,
            idle_seconds=idle_seconds,
            rows=rows,
            cols=cols,
            alive=alive,
            exit_code=result.exit_code,
            output=result.stderr if not alive else "",
            shell=_EchoShell(account) if self.pty_echo and alive else None,
            clock=self.pty_clock,
        )
        self.ptys.append(handle)
        return handle

    # Assertions -----------------------------------------------------------

    def ran(self, *argv_prefix: str) -> bool:
        """
        Report whether a command starting with the given arguments ran.

        Args:
            argv_prefix: Leading arguments to look for.

        Returns:
            True when at least one recorded call matches.
        """
        prefix = tuple(argv_prefix)
        return any(call[: len(prefix)] == prefix for call in self.calls)

    def calls_to(self, program: str) -> list[tuple[str, ...]]:
        """
        Return every recorded call to a given program.

        Args:
            program: Executable name to filter by.

        Returns:
            The matching argument vectors, in order.
        """
        return [c for c in self.calls if c and c[0] == program]


_default_runner: CommandRunner | None = None


def get_runner() -> CommandRunner:
    """
    Return the process-wide runner, creating the real one on first use.

    Returns:
        The active command runner.
    """
    global _default_runner
    if _default_runner is None:
        _default_runner = SubprocessRunner()
    return _default_runner


def set_runner(runner: CommandRunner | None) -> None:
    """
    Replace the process-wide runner.

    Args:
        runner: The runner to install, or None to reset to the real one.
    """
    global _default_runner
    _default_runner = runner
