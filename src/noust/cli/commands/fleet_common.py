# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the ``fleet`` and ``node`` commands share: the root check, the steps they print, and the
console's placement on loopback.

It is under the commands that use it (``fleet``, ``node link``) and imports none of them, so a
command module can import it without the group's entry point importing that module back.
"""

from __future__ import annotations

import getpass
import itertools
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, TextIO

from noust.core.exceptions import NodeError, NoustError
from noust.core.utils import check_root

if TYPE_CHECKING:
    from noust.cli.commands.web import DaemonConsole
    from noust.fleet.policy import FleetAccess


def _require_root(action: str) -> None:
    """
    Refuse an enrollment change that cannot work without root.

    Args:
        action: The command, for the message.

    Raises:
        NodeError: When not running as root.
    """
    if not check_root():
        raise NodeError(
            f"'noust fleet {action}' needs root",
            details="It edits sshd's configuration, key files and this server's tokens: use sudo.",
        )


def _operator() -> str:
    """
    Name the operator at this terminal, for the audit log and the ceiling's record.

    Returns:
        ``cli:<user>``.
    """
    try:
        return f"cli:{getpass.getuser()}"
    except (KeyError, OSError):
        return "cli:unknown"


def _flag_value(argv: list[str], flag: str) -> str | None:
    """
    Read the value after a flag in an argv.

    Args:
        argv: The argv.
        flag: Such as ``--port``.

    Returns:
        The value, or None when the flag is absent or last.
    """
    if flag in argv and argv.index(flag) + 1 < len(argv):
        return argv[argv.index(flag) + 1]
    return None


class Steps:
    """
    Show ``noust fleet authorize``'s steps as they run.

    On a terminal the step being run has a spinner, replaced by ``done`` or
    ``failed`` when it ends; anywhere else (a pipe, ``--json``) each step is a
    plain line. Always on standard error, so standard output carries only the
    result - the join code, or the JSON.

    Args:
        stream: Where to write; standard error by default.
        animate: Spin; by default when the stream is a terminal.
    """

    FRAMES = "|/-\\"
    INTERVAL = 0.1

    def __init__(self, stream: TextIO | None = None, *, animate: bool | None = None) -> None:
        self.stream = stream or sys.stderr
        isatty = getattr(self.stream, "isatty", None)
        self.animate = bool(isatty and isatty()) if animate is None else animate
        self._label: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def _write(self, text: str) -> None:
        with self._lock:
            self.stream.write(text)
            self.stream.flush()

    def _spin(self) -> None:
        for frame in itertools.cycle(self.FRAMES):
            if self._stop.wait(self.INTERVAL):
                return
            self._write(f"\r\x1b[K{self._label} {frame}")

    def _start_spinner(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def _stop_spinner(self) -> None:
        if self._thread is not None:
            self._stop.set()
            self._thread.join()
            self._thread = None

    def _end(self, outcome: str) -> None:
        if self._label is None:
            return
        if self.animate:
            self._stop_spinner()
            self._write(f"\r\x1b[K{self._label} {outcome}\n")
        self._label = None

    def __call__(self, step: int, total: int, what: str) -> None:
        """
        Start a step, ending the one before.

        Args:
            step: Its number.
            total: How many there are.
            what: What it does.
        """
        self._end("done")
        self._label = f"[{step}/{total}] {what}..."
        if self.animate:
            self._write(self._label)
            self._start_spinner()
        else:
            self._write(self._label + "\n")

    def note(self, text: str) -> None:
        """
        Say what the current step is doing now.

        Args:
            text: One short sentence.
        """
        if self.animate and self._label is not None:
            with self.paused():
                self._write(f"      {text}\n")
        else:
            self._write(f"      {text}\n")

    @contextmanager
    def paused(self) -> Iterator[None]:
        """
        Stop the spinner while something else writes (a question, a note).

        Yields:
            Nothing; the spinner resumes afterwards.
        """
        if not (self.animate and self._thread is not None):
            yield
            return
        self._stop_spinner()
        self._write("\r\x1b[K")
        try:
            yield
        finally:
            self._write(self._label or "")
            self._start_spinner()

    def finish(self, ok: bool = True) -> None:
        """
        End the last step.

        Args:
            ok: Whether it succeeded.
        """
        self._end("done" if ok else "failed")


@dataclass
class ConsoleOutcome:
    """
    How the console was made to run on loopback for the fleet.

    Attributes:
        port: Its port.
        token: An access token issued now, because none ever was; shown once.
        adopted: The PID of the background console turned into the service.
        started: Whether the service was (re)started.
    """

    port: int
    token: str | None = None
    adopted: int | None = None
    started: bool = False


def _never_adopt(daemon: DaemonConsole, changes: list[str]) -> bool:
    """
    Decline to adopt a background console: the default for callers that do not ask.

    Args:
        daemon: The console.
        changes: What the service would do differently.

    Returns:
        False.
    """
    return False


def ensure_console_on_loopback(
    verbose: bool,
    dry_run: bool,
    *,
    confirm_adopt: Callable[[DaemonConsole, list[str]], bool] = _never_adopt,
    note: Callable[[str], None] = lambda text: None,
) -> ConsoleOutcome:
    """
    Make sure the console runs as ``noust-web.service`` on loopback only.

    The central reaches a node's console through its tunnel, which ends on
    127.0.0.1; nothing else should reach it. A console already running as a
    service on loopback is left as it is. One bound to anything else, or
    serving TLS, is refused with the command that fixes it rather than
    changed behind the operator's back. A console running in the background
    (``noust web start -d``) is stopped and replaced by the service on the
    same port once ``confirm_adopt`` agrees; if the service does not come up,
    the background console is started again as it was. Otherwise the service
    is enabled on 127.0.0.1.

    The access token the operator holds is never retired: the service serves
    the one on disk, and a token is issued (and returned) only when none ever
    was. No banner is printed.

    Args:
        verbose: Log each step.
        dry_run: Rehearse.
        confirm_adopt: Asked with a background console and what the service
            would do differently; False leaves everything as it is.
        note: Told what is happening, for the progress display.

    Returns:
        The console's port, and a token when one was issued.

    Raises:
        NodeError: When the console is exposed beyond loopback, serves TLS,
            the operator declines to adopt the background console, or it could
            not be started.
    """
    from noust.cli.commands import web
    from noust.core.config import Config
    from noust.core.net import is_loopback_host
    from noust.managers.cron_manager import decode_exec_start

    unit = web._service_unit_path()
    host: str | None = None
    port: int | None = None
    tls = False
    if unit.exists():
        for line in unit.read_text(encoding="utf-8").splitlines():
            if not line.startswith("ExecStart="):
                continue
            argv = decode_exec_start(line.removeprefix("ExecStart=")) or []
            host = _flag_value(argv, "--host") or host
            port_text = _flag_value(argv, "--port")
            port = int(port_text) if port_text and port_text.isdigit() else port
            tls = tls or any(a in ("--self-signed", "--tls-cert", "--require-https") for a in argv)
        status = web._service_status(verbose)
        port = port or web.StartOptions().port
        fix = f"noust web enable --host 127.0.0.1 --port {port}"
        if host is not None and not is_loopback_host(host):
            raise NodeError(
                f"The console listens on {host}, not only on this machine",
                details=(
                    "A fleet node's console listens on 127.0.0.1 only: the central reaches it "
                    f"through its SSH tunnel, and nothing else should. Run '{fix}', then "
                    "authorize again."
                ),
            )
        if tls:
            raise NodeError(
                "The console serves TLS, which the central's tunnel does not expect",
                details=(
                    "On loopback the SSH tunnel already encrypts the connection. Run "
                    f"'{fix}', then authorize again."
                ),
            )
        if status is not None and status["active"]:
            return ConsoleOutcome(port=port)

    daemon = web.running_daemon()
    if daemon is not None:
        return _adopt(
            daemon,
            verbose,
            dry_run,
            confirm_adopt=confirm_adopt,
            note=note,
            had_unit=unit.exists(),
        )

    configured = Config().get("web.port")
    port = port or (int(configured) if configured else web.StartOptions().port)
    note(f"Starting {web.WEB_UNIT_FILE} on 127.0.0.1:{port}")
    try:
        service = web.enable_loopback_service(port, verbose, dry_run=dry_run)
    except NoustError as exc:
        raise NodeError(
            "The console could not be started on loopback",
            details=f"{exc.message}. {exc.details}".strip(),
            output=exc.output,
        ) from exc
    return ConsoleOutcome(port=port, token=service.token if service else None, started=True)


def _adopt(
    daemon: DaemonConsole,
    verbose: bool,
    dry_run: bool,
    *,
    confirm_adopt: Callable[[DaemonConsole, list[str]], bool],
    note: Callable[[str], None],
    had_unit: bool,
) -> ConsoleOutcome:
    """
    Replace a background console with the loopback service, on its port.

    Args:
        daemon: The background console.
        verbose: Log each step.
        dry_run: Rehearse.
        confirm_adopt: The operator's say.
        note: Progress.
        had_unit: Whether the service's unit was installed (stopped) before;
            one this wrote is removed again when the service fails.

    Returns:
        The service's port.

    Raises:
        NodeError: When declined, or when the service does not come up (the
            background console is started again first).
    """
    from noust.cli.commands import web

    port = daemon.options.port
    if not confirm_adopt(daemon, daemon.exposure_changes()):
        raise NodeError(
            "Cancelled: nothing was changed",
            details=(
                f"A console runs in the background (PID {daemon.pid}), and a fleet node's "
                "console runs as a service on 127.0.0.1. Authorize again and accept (or "
                "pass --yes), or stop it first: noust web stop"
            ),
        )
    if dry_run:
        note(f"Would stop the background console (PID {daemon.pid}) and start the service")
        return ConsoleOutcome(port=port, adopted=daemon.pid)
    note(f"Stopping the background console (PID {daemon.pid})")
    try:
        web.stop_daemon(daemon.pid)
    except NoustError as exc:
        raise NodeError(exc.message, details=exc.details) from exc
    note(f"Starting {web.WEB_UNIT_FILE} on 127.0.0.1:{port}")
    try:
        service = web.enable_loopback_service(port, verbose, port_just_freed=True)
    except NoustError as exc:
        problem = f"{exc.message}. {exc.details}".strip()
        try:
            if not had_unit:
                web._disable(verbose)
            web.restart_daemon(daemon.options)
        except NoustError as restore:
            raise NodeError(
                "The console could not be moved to a service, nor put back in the background",
                details=f"{problem}\nPutting it back failed too: {restore.message}. "
                f"{restore.details}".strip(),
                output=restore.output or exc.output,
            ) from exc
        raise NodeError(
            "The console could not be moved to a service; the background console was "
            "started again as it was, with the same access token",
            details=problem,
            output=exc.output,
        ) from exc
    return ConsoleOutcome(
        port=port, token=service.token if service else None, adopted=daemon.pid, started=True
    )


def _parse_access(level: str | None, host_access: bool | None) -> FleetAccess | None:
    """
    Turn ``--access`` and ``--allow-host-access`` into a ceiling to set, if either was given.

    Args:
        level: ``--access``.
        host_access: ``--allow-host-access`` or ``--no-host-access``.

    Returns:
        The new ceiling, or None to keep the one in force.
    """
    from noust.fleet.authorize import ceiling_in_force
    from noust.fleet.policy import FleetAccess, validate_access_level

    if level is None and host_access is None:
        return None
    in_force = ceiling_in_force()
    return FleetAccess(
        level=in_force.level if level is None else validate_access_level(level),
        host_access=in_force.host_access if host_access is None else host_access,
    )
