# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Confirm or revert: a change to how this server is reached undoes itself unless confirmed.

This is the ENS's ``op.exp.4.r2.1`` ("before applying configurations ... a
mechanism to revert them") made literal, in the style of a router's
``commit confirmed``. Every sshd or firewall change Noust makes is recorded
here before it takes effect, together with how to undo it, and a transient
systemd timer is armed to undo it once its window closes, :data:`CONFIRM_WINDOW`
seconds (300) after it was applied:

    systemd-run --unit=noust-security-revert-<id> --on-active=<window in seconds> ...
        -- <python> -m noust.managers.server.security_pending revert <id> --directory <dir>

The timer belongs to systemd, not to the console: if the change cut the
console off, or the console died, or the operator closed the tab, the change
still goes back. The command it runs is this module, not the ``noust`` CLI,
so undoing a change depends on nothing but Python and this file.

Confirming stops the timer, and it needs a proof that the change did not lock
anyone out: a *new* SSH login recorded after the change was applied (see
:meth:`ChangeLedger.confirm`). A session that was already open proves nothing,
because an sshd reload and a firewall change both leave established
connections alone - which is precisely why an operator can believe a broken
change works.

What undoing does is data, validated both when it is written and when it is
read back: files to put back (only Noust's own files, :data:`RESTORABLE_FILES`)
and commands to run (only :data:`UNDO_PROGRAMS`). The record lives beside the
store, root's and 0600; it is still not allowed to become a way to write
anywhere or run anything.

A transient timer does not survive a reboot. A change still pending past its
deadline - the machine rebooted inside the window - is reverted the next time
anything reads the ledger (:meth:`ChangeLedger.revert_overdue`).
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import logging
import os
import secrets
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from noust.core import audit as audit_trail
from noust.core import paths
from noust.core.exceptions import SecurityError
from noust.core.fs import SECRET_DIR_MODE, SECRET_MODE, FileSystem, get_fs, is_rehearsal
from noust.core.runner import CommandRunner, get_runner
from noust.managers.server.host import HostPaths
from noust.managers.server.security_sshd import DROPIN, read_no_follow

log = logging.getLogger(__name__)

#: Seconds a change stays applied without a confirmation. It has to cover
#: opening a terminal, logging in from it and coming back to press Keep; two
#: minutes was too short for operators who started from the console.
CONFIRM_WINDOW = 300

#: Seconds past the deadline after which a change still pending is overdue:
#: its timer was lost (a reboot), and whoever reads the ledger reverts it.
OVERDUE_GRACE = 60

#: Prefix of the transient unit that reverts a change.
UNIT_PREFIX = f"{paths.UNIT_PREFIX}security-revert-"

#: How long ``systemd-run`` and ``systemctl stop`` may take.
TIMER_TIMEOUT = 20

#: How long a change command (ufw, firewall-cmd, a reload) may take.
UNDO_TIMEOUT = 60

#: How long to wait for another change of the ledger to finish.
LOCK_WAIT = 90

#: The only files a record may put back.
FAIL2BAN_JAIL = "/etc/fail2ban/jail.d/noust.local"
RESTORABLE_FILES = frozenset({DROPIN, FAIL2BAN_JAIL})

#: The only programs a record may run to undo or commit a change.
UNDO_PROGRAMS = frozenset({"systemctl", "ufw", "firewall-cmd", "sshd", "fail2ban-client"})

#: The states a change goes through. Only ``pending`` has a timer.
STATUSES = ("pending", "confirmed", "reverted", "expired")


@dataclass(frozen=True)
class FileRestore:
    """
    A file to put back when a change is undone.

    Attributes:
        path: The file on the server; one of :data:`RESTORABLE_FILES`.
        previous: Its content before the change, or None when it did not exist.
        mode: Its mode when written back.
    """

    path: str
    previous: str | None
    mode: int = SECRET_MODE


@dataclass
class PendingChange:
    """
    A change to how this server is reached, waiting for a confirmation.

    Attributes:
        id: Short random identifier, also in the timer's name.
        kind: ``sshd`` or ``firewall``.
        title: What was changed, in a sentence.
        actor: Who applied it.
        applied_at: When, epoch seconds.
        expires_at: When the timer undoes it.
        status: ``pending``, ``confirmed``, ``reverted`` or ``expired``
            (reverted by the timer).
        files: Files to put back when undoing.
        validate: Command run after the files are back, before ``undo``
            (``sshd -t``), its failure reported but not stopping the undo.
        undo: Commands that undo the change, in order.
        commit: Commands that make it permanent on confirmation (firewalld's
            ``--permanent`` twin of a runtime change).
        before: The effective values before, for the operator.
        after: The effective values after.
        proof: ``operator`` (a new login by anyone but a central's tunnel) or
            ``any`` (a new login by anyone, the tunnel included).
        resolved_at: When it was confirmed or undone.
        resolved_by: Who did it: an actor, or ``timer``.
        resolution: The proof that confirmed it, or what undoing printed.
    """

    id: str
    kind: str
    title: str
    actor: str
    applied_at: float
    expires_at: float
    status: str = "pending"
    files: list[FileRestore] = field(default_factory=list)
    validate: list[str] = field(default_factory=list)
    undo: list[list[str]] = field(default_factory=list)
    commit: list[list[str]] = field(default_factory=list)
    before: dict[str, str] = field(default_factory=dict)
    after: dict[str, str] = field(default_factory=dict)
    proof: str = "operator"
    resolved_at: float | None = None
    resolved_by: str | None = None
    resolution: str = ""

    @property
    def unit(self) -> str:
        """The transient unit that undoes it."""
        return f"{UNIT_PREFIX}{self.id}"

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the change for the API and ``--json``.

        Returns:
            Every field but the saved file contents, which only undoing reads.
        """
        data = asdict(self)
        data["files"] = [restore.path for restore in self.files]
        data["unit"] = self.unit
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PendingChange:
        """
        Read a change back, checking what undoing it would touch.

        Args:
            data: What :func:`asdict` wrote.

        Returns:
            The change.

        Raises:
            SecurityError: The record names a file or a program Noust never
                records, or is not a record at all.
        """
        try:
            change = cls(
                id=str(data["id"]),
                kind=str(data["kind"]),
                title=str(data["title"]),
                actor=str(data["actor"]),
                applied_at=float(data["applied_at"]),
                expires_at=float(data["expires_at"]),
                status=str(data.get("status", "pending")),
                files=[FileRestore(**restore) for restore in data.get("files", [])],
                validate=[str(arg) for arg in data.get("validate", [])],
                undo=[[str(arg) for arg in argv] for argv in data.get("undo", [])],
                commit=[[str(arg) for arg in argv] for argv in data.get("commit", [])],
                before={str(k): str(v) for k, v in data.get("before", {}).items()},
                after={str(k): str(v) for k, v in data.get("after", {}).items()},
                proof=str(data.get("proof", "operator")),
                resolved_at=data.get("resolved_at"),
                resolved_by=data.get("resolved_by"),
                resolution=str(data.get("resolution", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SecurityError("A pending change record is damaged", details=str(exc)) from exc
        check_record(change)
        return change


def check_record(change: PendingChange) -> None:
    """
    Refuse a record that would undo anything but Noust's own changes.

    Args:
        change: The record.

    Raises:
        SecurityError: It names a file or a program outside the allow-lists,
            or an identifier that is not one this module mints.
    """
    if not change.id.isalnum() or len(change.id) > 32:
        raise SecurityError(f"Refusing the pending change id {change.id!r}")
    if change.status not in STATUSES:
        raise SecurityError(f"Refusing the pending change status {change.status!r}")
    for restore in change.files:
        if restore.path not in RESTORABLE_FILES:
            raise SecurityError(f"Refusing to restore {restore.path}: Noust never writes it")
    for argv in [change.validate, *change.undo, *change.commit]:
        if argv and Path(argv[0]).name not in UNDO_PROGRAMS:
            raise SecurityError(f"Refusing to run {argv[0]} to undo a change")


#: Ledgers this thread holds, and how deep: ``flock`` belongs to an open file
#: description, so a second acquisition by the same thread would wait on itself.
_local = threading.local()


def _held_ledgers() -> dict[str, int]:
    """
    The ledgers the calling thread holds.

    Returns:
        Ledger directory to nesting depth.
    """
    held: dict[str, int] | None = getattr(_local, "held", None)
    if held is None:
        held = {}
        _local.held = held
    return held


#: The audit event of each kind of change.
AUDIT_EVENTS = {"sshd": "server.ssh", "firewall": "server.firewall"}


def audit(event: str, target: str, *, outcome: str = "ok", **details: Any) -> None:
    """
    Record a change to how the server is reached, in the one audit trail.

    Called by the managers themselves rather than by the endpoints, so a
    change made from the console, from the command line or by the revert
    timer is on record the same way.

    Args:
        event: A catalog event: ``server.ssh``, ``server.firewall``,
            ``server.fail2ban``, ``security.risk.accept``...
        target: What it was done to: ``sshd``, ``user:root``, ``change:<id>``.
        outcome: ``ok``, ``denied`` (a guard refused) or ``failure``.
        **details: Context; empty values are left out. Never a secret.
    """
    audit_trail.record(
        event,
        target=target,
        outcome=outcome,
        details={key: value for key, value in details.items() if value not in (None, "", [], {})},
    )


def new_change_id() -> str:
    """
    Mint a change identifier.

    Returns:
        Eight hexadecimal characters.
    """
    return secrets.token_hex(4)


def default_directory() -> Path:
    """
    Where the ledger lives: beside the store, like the application locks.

    Returns:
        ``<store directory>/security/changes``.
    """
    from noust.core.store import get_store

    return get_store().db_path.parent / "security" / "changes"


class ChangeLedger:
    """
    The record of access changes and the timers that undo them.

    Args:
        directory: Where records are kept.
        runner: The command runner.
        fs: The filesystem seam.
        host: Where the restored files are.
        clock: The current time.
        python: The interpreter the timer runs this module with.
    """

    def __init__(
        self,
        directory: Path | None = None,
        *,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        host: HostPaths | None = None,
        clock: Callable[[], float] = time.time,
        python: str | None = None,
    ) -> None:
        self._directory = directory
        self._runner = runner
        self._fs = fs
        self.host = host or HostPaths()
        self.clock = clock
        self.python = python or sys.executable

    @property
    def directory(self) -> Path:
        """Where records are kept."""
        if self._directory is None:
            self._directory = default_directory()
        return self._directory

    @property
    def runner(self) -> CommandRunner:
        """The command runner."""
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """The filesystem seam."""
        return self._fs or get_fs()

    # Records ------------------------------------------------------------------

    def _path(self, change_id: str) -> Path:
        """
        The record file of a change.

        Args:
            change_id: Its identifier.

        Returns:
            The path.

        Raises:
            SecurityError: The identifier is not one this module mints.
        """
        if not change_id.isalnum() or len(change_id) > 32:
            raise SecurityError(f"There is no pending change {change_id!r}")
        return self.directory / f"{change_id}.json"

    def save(self, change: PendingChange) -> None:
        """
        Write a record.

        Args:
            change: The change.
        """
        check_record(change)
        self.fs.make_dir(self.directory, mode=SECRET_DIR_MODE, parents=True)
        self.fs.write_text(
            self._path(change.id), json.dumps(asdict(change), indent=2) + "\n", mode=SECRET_MODE
        )

    def load(self, change_id: str) -> PendingChange:
        """
        Read a record.

        Args:
            change_id: Its identifier.

        Returns:
            The change.

        Raises:
            SecurityError: There is no such change, or its record is damaged.
        """
        text = read_no_follow(self._path(change_id))
        if text is None:
            raise SecurityError(
                f"There is no pending change {change_id}",
                details="List them with 'noust server security pending'.",
            )
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SecurityError(
                f"The record of change {change_id} is damaged", details=str(exc)
            ) from exc
        return PendingChange.from_dict(data)

    def changes(self) -> list[PendingChange]:
        """
        Every recorded change, newest first.

        Returns:
            The changes; a damaged record is skipped and logged.
        """
        found: list[PendingChange] = []
        try:
            names = sorted(self.directory.glob("*.json"))
        except OSError:
            return []
        for path in names:
            try:
                found.append(self.load(path.stem))
            except SecurityError as exc:
                log.warning("Skipping %s: %s", path, exc)
        found.sort(key=lambda change: change.applied_at, reverse=True)
        return found

    def pending(self) -> list[PendingChange]:
        """
        The changes still waiting for a confirmation.

        Returns:
            The changes, newest first.
        """
        return [change for change in self.changes() if change.status == "pending"]

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        """
        Hold the ledger while a change is applied, confirmed or undone.

        Waits for another holder instead of failing: the timer's revert must
        not give up because a confirmation holds the ledger for a second, or
        the change would end up neither confirmed nor undone.

        Yields:
            Nothing; the ledger is held until the block exits.

        Raises:
            SecurityError: Another holder kept it past :data:`LOCK_WAIT`.
        """
        key = str(self.directory)
        held = _held_ledgers()
        if key in held:
            # Applying a change undoes it on the spot when its verification
            # fails; that inner revert already holds the ledger.
            held[key] += 1
            try:
                yield
            finally:
                held[key] -= 1
            return
        if is_rehearsal():
            # A rehearsal writes no record and no lock file.
            yield
            return
        self.fs.make_dir(self.directory, mode=SECRET_DIR_MODE, parents=True)
        descriptor = os.open(
            self.directory / ".lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
            SECRET_MODE,
        )
        try:
            deadline = time.monotonic() + LOCK_WAIT
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() > deadline:
                        raise SecurityError(
                            "Another change to SSH or the firewall is still being applied",
                            details="Wait for it to finish, then try again.",
                        ) from exc
                    time.sleep(0.2)
            held[key] = 1
            try:
                yield
            finally:
                del held[key]
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    # The timer ----------------------------------------------------------------

    def timer_argv(self, change: PendingChange) -> list[str]:
        """
        The ``systemd-run`` command that arms a change's revert.

        Args:
            change: The change.

        Returns:
            The argv.
        """
        seconds = max(1, int(change.expires_at - change.applied_at))
        argv = [
            "systemd-run",
            f"--unit={change.unit}",
            f"--on-active={seconds}",
            "--timer-property=AccuracySec=1s",
            "--collect",
            "--quiet",
            # systemd expands %-specifiers in a description; a title is text.
            "--description=Noust: undo '{}' unless it is confirmed".format(
                change.title.replace("%", "%%")
            ),
        ]
        data_dir = paths.getenv(paths.DATA_DIR_ENV)
        if data_dir:
            argv.append(f"--setenv={paths.DATA_DIR_ENV}={data_dir}")
        return [
            *argv,
            "--",
            self.python,
            "-m",
            __name__,
            "revert",
            change.id,
            "--directory",
            str(self.directory),
        ]

    def open(self, change: PendingChange) -> None:
        """
        Record a change and arm the timer that undoes it.

        Called before the change takes effect: a change that cannot be undone
        on its own is not made.

        Args:
            change: The change, ``pending``.

        Raises:
            SecurityError: The timer could not be armed; nothing was changed.
        """
        self.save(change)
        result = self.runner.run(self.timer_argv(change), timeout=TIMER_TIMEOUT)
        if not result.success:
            self.fs.remove(self._path(change.id), missing_ok=True)
            raise SecurityError(
                "Noust could not arm the timer that would undo this change, so it did not make it",
                details="A change to SSH or the firewall is only made when it can undo itself. "
                "Check that systemd is running (systemctl is-system-running).",
                output=(result.stderr or result.stdout).strip() or None,
            )

    def _stop_timer(self, change: PendingChange) -> tuple[bool, str]:
        """
        Stop a change's timer.

        Args:
            change: The change.

        Returns:
            Whether the timer is now stopped with its revert not started, and
            what systemctl said.
        """
        stop = self.runner.run(["systemctl", "stop", f"{change.unit}.timer"], timeout=TIMER_TIMEOUT)
        state = self.runner.run(
            ["systemctl", "is-active", f"{change.unit}.service"], timeout=TIMER_TIMEOUT
        )
        running = state.stdout.strip() in ("active", "activating")
        said = (stop.stderr or stop.stdout).strip()
        return not running, said

    # Confirm and revert -------------------------------------------------------

    def confirm(self, change_id: str, *, proof: str, by: str) -> PendingChange:
        """
        Keep a change: stop its timer and make it permanent.

        The caller has already found the proof (a login after the change); it
        is recorded as the reason the change was kept.

        Args:
            change_id: The change.
            proof: The evidence that a new session got in, verbatim.
            by: Who confirmed.

        Returns:
            The confirmed change.

        Raises:
            SecurityError: It is not pending, or its revert already started.
        """
        with self.locked():
            change = self.load(change_id)
            if change.status != "pending":
                raise SecurityError(
                    f"Change {change_id} is already {change.status}",
                    details="Only a pending change can be confirmed.",
                )
            stopped, said = self._stop_timer(change)
            if not stopped:
                raise SecurityError(
                    f"Too late: change {change_id} is being undone right now",
                    details="Its confirmation window ended. Apply it again if you still want it.",
                    output=said or None,
                )
            failures = self._run_all(change.commit, None)
            if failures:
                # The timer is stopped, so nothing else will undo it: undo it now.
                self._undo(change, None)
                change.status = "reverted"
                change.resolved_at = self.clock()
                change.resolved_by = by
                change.resolution = "Making it permanent failed:\n" + "\n".join(failures)
                self.save(change)
                raise SecurityError(
                    f"Change {change_id} could not be made permanent and was undone",
                    output="\n".join(failures),
                )
            change.status = "confirmed"
            change.resolved_at = self.clock()
            change.resolved_by = by
            change.resolution = proof
            self.save(change)
            audit(
                AUDIT_EVENTS.get(change.kind, "server.ssh"),
                f"change:{change.id}",
                action="confirm",
                title=change.title,
                proof=proof,
            )
            return change

    def revert(
        self,
        change_id: str,
        *,
        by: str,
        expired: bool = False,
        on_output: Callable[[str], None] | None = None,
    ) -> PendingChange:
        """
        Undo a pending change.

        Args:
            change_id: The change.
            by: Who asked: an actor, or ``timer``.
            expired: The timer is undoing it because nobody confirmed.
            on_output: Receives every command and its output, verbatim.

        Returns:
            The change, ``reverted`` or ``expired``; unchanged when it was not
            pending any more (the timer firing after a confirmation).
        """
        with self.locked():
            change = self.load(change_id)
            if change.status != "pending":
                return change
            if not expired:
                self._stop_timer(change)
            output = self._undo(change, on_output)
            change.status = "expired" if expired else "reverted"
            change.resolved_at = self.clock()
            change.resolved_by = by
            change.resolution = "\n".join(output)
            self.save(change)
            log.warning("Undid %s change %s (%s): %s", change.kind, change.id, by, change.title)
            audit(
                AUDIT_EVENTS.get(change.kind, "server.ssh"),
                f"change:{change.id}",
                action="expire" if expired else "revert",
                title=change.title,
                by=by,
            )
            return change

    def revert_overdue(self, on_output: Callable[[str], None] | None = None) -> list[PendingChange]:
        """
        Undo every change whose timer should have fired and did not.

        Returns:
            The changes undone now.
        """
        now = self.clock()
        undone: list[PendingChange] = []
        for change in self.pending():
            if now <= change.expires_at + OVERDUE_GRACE:
                continue
            try:
                undone.append(
                    self.revert(change.id, by="overdue", expired=True, on_output=on_output)
                )
            except (OSError, SecurityError) as exc:
                # Reading the ledger must not fail for it: the next reader
                # with the rights to undo it (the console, root) will.
                log.error("Could not undo overdue change %s: %s", change.id, exc)
        return undone

    def _undo(self, change: PendingChange, on_output: Callable[[str], None] | None) -> list[str]:
        """
        Put a change's files back and run its undo commands.

        Every step runs even when an earlier one failed: half an undo is
        better than none, and every failure is reported verbatim.

        Args:
            change: The change.
            on_output: Receives every step, verbatim.

        Returns:
            What happened, line by line.
        """
        lines: list[str] = []

        def say(line: str) -> None:
            lines.append(line)
            if on_output:
                on_output(line)

        for restore in change.files:
            target = self.host.at(restore.path)
            if restore.previous is None:
                self.fs.remove(target, missing_ok=True)
                say(f"Removed {restore.path}")
            else:
                self.fs.write_text(target, restore.previous, mode=restore.mode)
                say(f"Restored {restore.path}")
        if change.validate:
            result = self.runner.run(change.validate, timeout=UNDO_TIMEOUT)
            say("$ " + " ".join(change.validate))
            for line in (result.stdout + result.stderr).splitlines():
                say(line)
        for failure in self._run_all(change.undo, say):
            say(failure)
        return lines

    def _run_all(self, commands: list[list[str]], say: Callable[[str], None] | None) -> list[str]:
        """
        Run commands in order, all of them.

        Args:
            commands: The argvs.
            say: Receives each command and its output.

        Returns:
            One line per command that failed.
        """
        failures: list[str] = []
        for argv in commands:
            result = self.runner.run(argv, timeout=UNDO_TIMEOUT, env={"LC_ALL": "C"})
            if say:
                say("$ " + " ".join(argv))
                for line in (result.stdout + result.stderr).splitlines():
                    say(line)
            if not result.success:
                failures.append(
                    f"{' '.join(argv)} exited {result.exit_code}: "
                    + (result.stderr or result.stdout).strip()
                )
        return failures


def main(argv: list[str] | None = None) -> int:
    """
    Undo a change: what the transient timer runs.

    Args:
        argv: ``revert <id> --directory <dir>``.

    Returns:
        0 when the change is no longer applied (undone now, or confirmed or
        undone before), 1 when the record could not be read.
    """
    parser = argparse.ArgumentParser(prog="python -m noust.managers.server.security_pending")
    commands = parser.add_subparsers(dest="command", required=True)
    revert = commands.add_parser("revert", help="Undo a pending change that was not confirmed.")
    revert.add_argument("change_id")
    revert.add_argument("--directory", required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    ledger = ChangeLedger(Path(args.directory))
    try:
        change = ledger.revert(
            args.change_id, by="timer", expired=True, on_output=lambda line: log.info("%s", line)
        )
    except SecurityError as exc:
        log.error("%s", exc)
        return 1
    log.info("Change %s is %s", change.id, change.status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
