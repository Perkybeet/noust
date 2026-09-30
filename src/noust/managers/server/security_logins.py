# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Who logged in over SSH, how, and with which key: sshd's own record of it.

This is the evidence the access-changing fixes stand on. A key in
``authorized_keys`` proves somebody once pasted it; a line such as::

    Accepted publickey for root from 203.0.113.9 port 51234 ssh2: ED25519 SHA256:Vd...

proves that whoever holds the private half used it to get in, recently. sshd
writes it at its default ``LogLevel INFO``, fingerprint included. It is read
from the journal (``_COMM`` is ``sshd``, or ``sshd-session`` since OpenSSH
9.8) with the ``short-unix`` format, whose first field is the time in epoch
seconds, and filtered here in Python: ``journalctl --grep`` needs PCRE2, which
not every distribution builds it with. Where there is no journal, the syslog
files (``auth.log``, ``secure``) are read instead.

What it cannot prove, and the console says so: that the person pressing the
button holds that key today.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from noust.core.runner import EXIT_NOT_FOUND, CommandRunner, get_runner
from noust.managers.server.host import HostPaths, read_text

#: How far back a login counts as evidence that a key works.
EVIDENCE_DAYS = 30

#: Most journal lines read in one pass. A server under a brute-force attack
#: logs thousands of failures a day; the accepted logins are among them.
JOURNAL_LINES = 20000

#: Deadline of the journal read.
JOURNAL_TIMEOUT = 60

#: The syslog files sshd writes to without a journal, Debian's first.
AUTH_LOGS = ("/var/log/auth.log", "/var/log/secure")

#: ``Accepted <method> for <user> from <ip> port <port> ssh2[: <TYPE> <fingerprint>]``.
_ACCEPTED = re.compile(
    r"Accepted (?P<method>\S+) for (?P<user>\S+) from (?P<source>\S+) port (?P<port>\d+)"
    r"(?: ssh2)?(?:: (?P<type>[A-Z0-9-]+) (?P<fp>SHA256:[A-Za-z0-9+/=]+))?"
)

#: A syslog timestamp: ``Sep 29 10:00:01``.
_SYSLOG_TIME = re.compile(r"^(?P<stamp>[A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d) ")


@dataclass(frozen=True)
class LoginEvent:
    """
    One successful SSH login.

    Attributes:
        at: When, in epoch seconds.
        method: ``publickey``, ``password``, ``keyboard-interactive/pam``...
        user: The account.
        source: The client's address.
        port: The client's port, which is how a login is matched to a
            connection that is still open.
        key_type: ``ED25519``, ``RSA``... for a key login.
        fingerprint: ``SHA256:...`` for a key login.
        line: The line as sshd wrote it, for evidence.
    """

    at: float
    method: str
    user: str
    source: str
    port: int
    key_type: str | None = None
    fingerprint: str | None = None
    line: str = ""

    @property
    def with_key(self) -> bool:
        """Whether the login used a public key."""
        return self.method == "publickey" and self.fingerprint is not None


@dataclass(frozen=True)
class LoginHistory:
    """
    The SSH logins found, and where they were read from.

    Attributes:
        events: The logins, oldest first.
        source: ``journal``, a syslog file, or ``""`` when nothing could be read.
        error: Why nothing could be read, verbatim; empty otherwise.
        since: The start of the window read, epoch seconds.
    """

    events: tuple[LoginEvent, ...] = ()
    source: str = ""
    error: str = ""
    since: float = 0.0

    @property
    def readable(self) -> bool:
        """Whether the history could be read at all."""
        return bool(self.source)

    def key_logins(self, user: str | None = None) -> list[LoginEvent]:
        """
        Logins with a key, newest first.

        Args:
            user: Only this account's, when given.

        Returns:
            The events.
        """
        return [
            event
            for event in reversed(self.events)
            if event.with_key and (user is None or event.user == user)
        ]

    def last_use(self, user: str, fingerprint: str) -> LoginEvent | None:
        """
        The most recent login of an account with one key.

        Args:
            user: The account.
            fingerprint: ``SHA256:...``.

        Returns:
            The event, or None when the key was not used in the window.
        """
        return next(
            (event for event in self.key_logins(user) if event.fingerprint == fingerprint), None
        )

    def after(self, moment: float) -> list[LoginEvent]:
        """
        Logins strictly after a moment, oldest first.

        Args:
            moment: Epoch seconds.

        Returns:
            The events.
        """
        return [event for event in self.events if event.at > moment]


def parse_message(message: str, at: float) -> LoginEvent | None:
    """
    Read one sshd message.

    Args:
        message: The line, with or without its syslog prefix.
        at: When it was written.

    Returns:
        The login, or None when the line is not an accepted login.
    """
    match = _ACCEPTED.search(message)
    if match is None:
        return None
    return LoginEvent(
        at=at,
        method=match.group("method"),
        user=match.group("user"),
        source=match.group("source"),
        port=int(match.group("port")),
        key_type=match.group("type"),
        fingerprint=match.group("fp"),
        line=message.strip(),
    )


def parse_short_unix(text: str) -> list[LoginEvent]:
    """
    Read ``journalctl -o short-unix`` output.

    Args:
        text: Lines of ``<epoch> <host> <ident>[<pid>]: <message>``.

    Returns:
        The accepted logins in it, in order.
    """
    events: list[LoginEvent] = []
    for raw in text.splitlines():
        stamp, _, rest = raw.partition(" ")
        try:
            at = float(stamp)
        except ValueError:
            continue
        event = parse_message(rest, at)
        if event is not None:
            events.append(event)
    return events


def _syslog_time(line: str, now: float) -> float | None:
    """
    Read the time at the start of a syslog line.

    Args:
        line: ``Sep 29 10:00:01 host sshd[1]: ...`` or an RFC 3339 stamp, which
            Debian 12's rsyslog writes.
        now: The current time; a stamp without a year that would fall in the
            future is last year's.

    Returns:
        Epoch seconds, or None when the line has no stamp this understands.
    """
    first = line.split(" ", 1)[0]
    try:
        return datetime.fromisoformat(first.replace("Z", "+00:00")).timestamp()
    except ValueError:
        pass
    match = _SYSLOG_TIME.match(line)
    if match is None:
        return None
    year = datetime.fromtimestamp(now).year
    try:
        stamp = datetime.strptime(f"{year} {match.group('stamp')}", "%Y %b %d %H:%M:%S")
    except ValueError:
        return None
    if stamp.timestamp() > now + 86400:
        stamp = stamp.replace(year=year - 1)
    return stamp.timestamp()


def parse_syslog(text: str, now: float) -> list[LoginEvent]:
    """
    Read a syslog file sshd writes to.

    Args:
        text: The file.
        now: The current time, for stamps without a year.

    Returns:
        The accepted logins in it, in order.
    """
    events: list[LoginEvent] = []
    for raw in text.splitlines():
        if "Accepted " not in raw:
            continue
        at = _syslog_time(raw, now)
        if at is None:
            continue
        event = parse_message(raw, at)
        if event is not None:
            events.append(event)
    return events


#: How long one reading of the evidence window serves every question. The
#: window is 30 days of sshd's journal, which on a server the Internet probes
#: all day (240,000 lines on the owner's central) took 2.4 s to read, and every
#: view of Server > Security read it again - twice for the SSH keys alone. A
#: minute-old "last used" is as true as a fresh one; what must be fresh (the
#: login that proves a change kept access) reads with ``since``, uncached.
SHARED_TTL = 60.0

_shared_lock = threading.Lock()
#: Held while the window is read: the views of a tab ask at the same moment,
#: and they wait for one reading instead of each making their own.
_reading_lock = threading.Lock()
_shared: tuple[float, object, LoginHistory] | None = None


def forget_shared() -> None:
    """Drop the shared reading, so the next one asks the journal again."""
    global _shared
    with _shared_lock:
        _shared = None


@dataclass
class LoginReader:
    """
    Read sshd's record of logins.

    Attributes:
        runner: The command runner journalctl goes through.
        host: Where the syslog files are.
        clock: The current time, replaceable in a test.
    """

    runner: CommandRunner | None = None
    host: HostPaths = field(default_factory=HostPaths)
    clock: Callable[[], float] = time.time

    def _now(self) -> float:
        """The current time, in epoch seconds."""
        return float(self.clock())

    def read(self, since: float | None = None) -> LoginHistory:
        """
        Read the logins since a moment.

        Args:
            since: Epoch seconds; :data:`EVIDENCE_DAYS` ago by default.

        Returns:
            The history, and where it came from.
        """
        if since is None:
            return self._shared_window()
        return self._read(since)

    def _shared_window(self) -> LoginHistory:
        """
        The evidence window, read once a minute for every caller in this process.

        Returns:
            The history since :data:`EVIDENCE_DAYS` ago.
        """
        global _shared
        start = self._now() - EVIDENCE_DAYS * 86400
        with _reading_lock:
            with _shared_lock:
                # Keyed on the runner: a test's fake machine never answers another's.
                shared = _shared
            if shared is not None and shared[1] is self.runner and time.monotonic() < shared[0]:
                cached = shared[2]
                return LoginHistory(
                    events=tuple(event for event in cached.events if event.at >= start),
                    source=cached.source,
                    error=cached.error,
                    since=start,
                )
            history = self._read(start)
            if history.readable:
                with _shared_lock:
                    _shared = (time.monotonic() + SHARED_TTL, self.runner, history)
            return history

    def _read(self, start: float) -> LoginHistory:
        """
        Read the logins since a moment, from the journal or a syslog file.

        Args:
            start: Epoch seconds.

        Returns:
            The history, and where it came from.
        """
        result = (self.runner or get_runner()).run(
            [
                "journalctl",
                "_COMM=sshd",
                "_COMM=sshd-session",
                f"--since=@{int(start)}",
                "-o",
                "short-unix",
                "--no-pager",
                "-q",
                "-n",
                str(JOURNAL_LINES),
            ],
            timeout=JOURNAL_TIMEOUT,
            env={"LC_ALL": "C", "TERM": "dumb"},
        )
        if result.success:
            events = [event for event in parse_short_unix(result.stdout) if event.at >= start]
            return LoginHistory(events=tuple(events), source="journal", since=start)
        journal_error = (result.stderr or result.stdout).strip()
        for name in AUTH_LOGS:
            text = read_text(self.host.at(name))
            if text is None:
                continue
            events = [event for event in parse_syslog(text, self._now()) if event.at >= start]
            return LoginHistory(events=tuple(events), source=name, since=start)
        reason = (
            "journalctl is not installed"
            if result.exit_code == EXIT_NOT_FOUND
            else journal_error or f"journalctl exited {result.exit_code}"
        )
        return LoginHistory(
            error=f"{reason}; and neither {' nor '.join(AUTH_LOGS)} can be read",
            since=start,
        )
