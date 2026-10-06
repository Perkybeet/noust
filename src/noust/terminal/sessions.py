# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The open terminals: who has one, how long it lasts, and what is on record about it.

:class:`SessionRegistry` is the chokepoint (rule 4) every terminal passes: a
WebSocket handler that forgot a check could not open a session around it,
because only :meth:`SessionRegistry.open` starts one. It holds the limits
(how many in all, how many for one person, whether the server's shell is
allowed at all), writes the two audit events that bracket every session
(``terminal.start`` and ``terminal.end``, the second with the SHA-256 of the
recording), and refuses to open a session it could not record.

A :class:`TerminalSession` is one program on one terminal. A thread of its own
reads what the program prints, because the runner's read blocks and a console
must not: it hands the text to whoever is attached (the WebSocket), keeps it in
a bounded backlog while nobody is, and records it. The limits themselves are
the runner's: the session asks the terminal how long is left and says so, it
does not count.

A connection that drops does not end the session at once. The program keeps
running for :data:`DETACH_GRACE_SECONDS`, its output waiting, so a browser that
lost its network for a moment comes back to the same shell instead of a dead
one; after that the session ends as ``disconnected``, as an SSH session does.
"""

from __future__ import annotations

import codecs
import collections
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from noust.core import audit
from noust.core.audit import Actor
from noust.core.exceptions import DeploymentError, NoustError, ValidationError
from noust.core.runner import (
    PTY_END_CLOSED,
    PTY_END_EXITED,
    CommandRunner,
    PtyHandle,
    get_runner,
)
from noust.terminal import recording as recordings
from noust.terminal.settings import TerminalSettings, load_settings
from noust.terminal.targets import KIND_SERVER, SessionPlan
from noust.terminal.tickets import KIND_APP, TerminalGrant

logger = logging.getLogger(__name__)

#: How long a session whose connection dropped is kept for it to come back.
DETACH_GRACE_SECONDS = 60

#: How much output a detached session keeps for the connection that returns.
#: Past it the oldest is dropped, and the one that returns is told.
BACKLOG_BYTES = 256 * 1024

#: How long before a limit the person is told it is coming.
WARN_SECONDS = 60

#: Longest a single piece of typing may be: a paste, not a flood.
MAX_INPUT_CHARS = 64 * 1024

#: Why a session ended, besides what the terminal itself says
#: (``exited``, ``idle``, ``limit``, ``closed``).
END_KILLED = "killed"
END_REVOKED = "revoked"
END_DISCONNECTED = "disconnected"
END_SHUTDOWN = "shutdown"

#: How often the reader looks at the clock when the program is silent.
_READ_SLICE = 0.5


class TerminalLimitError(NoustError):
    """Too many terminals are open: a limit of this server is reached."""


class TerminalDisabledError(NoustError):
    """This kind of terminal is switched off here, or the profile keeps it off."""


class TerminalGone(NoustError):
    """The session a ticket names is not there, or is not its owner's."""


Sink = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class SessionInfo:
    """
    A session, as the console and the list show it.

    Attributes:
        id: The session's identifier.
        kind: ``app`` or ``server``.
        domain: The application, when there is one.
        service: The Compose service, when there is one.
        label: What a header shows.
        account: Who the shell runs as.
        actor: Who opened it.
        started_at: ``time.time()`` when it started.
        ends_at: When it ends however busy it is.
        idle_seconds: How long it may go with nothing typed.
        idle_remaining: How long it has left before that.
        rows: Its height.
        cols: Its width.
        attached: Whether a connection is on it right now.
        recording: The id of its recording.
    """

    id: str
    kind: str
    domain: str | None
    service: str | None
    label: str
    account: str
    actor: str
    started_at: float
    ends_at: float
    idle_seconds: int
    idle_remaining: float
    rows: int
    cols: int
    attached: bool
    recording: str | None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The session as JSON-ready data.
        """
        return {
            "id": self.id,
            "kind": self.kind,
            "domain": self.domain,
            "service": self.service,
            "label": self.label,
            "account": self.account,
            "actor": self.actor,
            "started_at": self.started_at,
            "ends_at": self.ends_at,
            "idle_seconds": self.idle_seconds,
            "idle_remaining": round(self.idle_remaining, 1),
            "rows": self.rows,
            "cols": self.cols,
            "attached": self.attached,
            "recording": self.recording,
        }


class TerminalSession:
    """One program on one terminal, with whoever is attached to it."""

    def __init__(
        self,
        grant: TerminalGrant,
        plan: SessionPlan,
        handle: PtyHandle,
        recorder: recordings.SessionRecorder,
        *,
        actor: Actor,
        on_end: Callable[[TerminalSession], None],
    ) -> None:
        """
        Args:
            grant: What the ticket authorised.
            plan: What it runs.
            handle: The terminal.
            recorder: Where the session is kept.
            actor: Who opened it, for the audit trail.
            on_end: Told once when the session is over, to forget it.
        """
        self.id = grant.session_id
        self.grant = grant
        self.plan = plan
        self.handle = handle
        self.recorder = recorder
        self.actor = actor
        self._on_end = on_end
        self._lock = threading.Lock()
        # Held while a message is handed to the connection, so what is sent and
        # what is replayed after a takeover keep their order.
        self._delivery = threading.Lock()
        self._sink: Sink | None = None
        self._backlog: collections.deque[dict[str, Any]] = collections.deque()
        self._backlog_bytes = 0
        self._lost = False
        self._detached_at: float | None = None
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._reason: str | None = None
        self._finishing = False
        self._ended = threading.Event()
        self._thread: threading.Thread | None = None
        self._warned: set[str] = set()
        self.started_at = time.time()
        self.rows = handle.rows
        self.cols = handle.cols

    # -- what it is -----------------------------------------------------------

    @property
    def attached(self) -> bool:
        """Whether a connection is on the session right now."""
        return self._sink is not None

    @property
    def ended(self) -> bool:
        """Whether the session is over, or being ended: nothing more is accepted."""
        return self._finishing

    def info(self) -> SessionInfo:
        """
        Returns:
            The session as it is now.
        """
        return SessionInfo(
            id=self.id,
            kind=self.grant.kind,
            domain=self.plan.target.domain,
            service=self.plan.target.service,
            label=self.plan.target.label,
            account=self.plan.target.account,
            actor=self.grant.actor,
            started_at=self.started_at,
            ends_at=self.started_at + self.handle.max_seconds,
            idle_seconds=self.handle.idle_seconds,
            idle_remaining=self.handle.idle_remaining(),
            rows=self.rows,
            cols=self.cols,
            attached=self.attached,
            recording=self.recorder.id,
        )

    # -- the connection -------------------------------------------------------

    def start(self, sink: Sink) -> None:
        """
        Begin reading the program, delivering to a first connection.

        Args:
            sink: Called, from the reader's thread, with each message for the
                person: ``output``, ``notice``, ``ended``.
        """
        with self._lock:
            self._sink = sink
        self._thread = threading.Thread(
            target=self._pump, name=f"noust-terminal-{self.id}", daemon=True
        )
        self._thread.start()

    def attach(self, sink: Sink) -> bool:
        """
        Put a connection on a session that is running, replacing the old one.

        Args:
            sink: The new connection's receiver.

        Returns:
            True when output was lost while nobody was attached (the backlog
            overflowed), so the screen the person has may be missing some.
        """
        with self._delivery, self._lock:
            previous = self._sink
            self._sink = None
            self._detached_at = None
            lost, self._lost = self._lost, False
            backlog = list(self._backlog)
            self._backlog.clear()
            self._backlog_bytes = 0
            if previous is not None:
                self._say(previous, {"type": "error", "message": "Taken over by another window"})
            for message in backlog:
                if not self._say(sink, message):
                    self._keep(message)
                    continue
            self._sink = sink
        return lost

    def detach(self, sink: Sink | None = None) -> None:
        """
        Take the connection off: the program keeps running for a while.

        Args:
            sink: The connection that left; ignored when another has taken
                over since.
        """
        with self._lock:
            if sink is not None and self._sink is not sink:
                return
            if self._sink is not None:
                self._sink = None
                self._detached_at = time.monotonic()

    @staticmethod
    def _say(sink: Sink, message: dict[str, Any]) -> bool:
        """
        Deliver a message, whatever the receiver's state.

        Args:
            sink: The receiver. It raises ``RuntimeError`` when its connection
                is gone and ``TimeoutError`` when it did not take the message
                in time.
            message: What to say.

        Returns:
            Whether it was taken.
        """
        try:
            sink(message)
        except (RuntimeError, TimeoutError) as exc:
            logger.debug("A terminal's connection did not take a message: %s", exc)
            return False
        return True

    def _keep(self, message: dict[str, Any]) -> None:
        """
        Keep a message for the connection that comes back. The caller holds the lock.

        Args:
            message: What to keep; the oldest output goes first when there is
                too much.
        """
        size = len(message.get("data", "")) if message["type"] == "output" else 0
        self._backlog.append(message)
        self._backlog_bytes += size
        while self._backlog_bytes > BACKLOG_BYTES and self._backlog:
            dropped = self._backlog.popleft()
            self._backlog_bytes -= len(dropped.get("data", ""))
            self._lost = True

    def _deliver(self, message: dict[str, Any]) -> None:
        """
        Send to the connection, or keep it for the one that comes back.

        The connection may take its time (a browser that cannot keep up makes
        this wait, and the program with it, which is the flow control a
        terminal has): so it is called without the lock the keyboard needs.

        Args:
            message: What to say.
        """
        with self._delivery:
            with self._lock:
                sink = self._sink
            if sink is not None and self._say(sink, message):
                return
            with self._lock:
                if sink is not None and self._sink is sink:
                    # It did not answer: from here on, nobody is attached.
                    self._sink = None
                    self._detached_at = time.monotonic()
                self._keep(message)

    # -- what the person does ---------------------------------------------------

    def write(self, text: str) -> None:
        """
        Type something.

        Args:
            text: What was typed or pasted.

        Raises:
            ValidationError: More than :data:`MAX_INPUT_CHARS` at once.
        """
        if len(text) > MAX_INPUT_CHARS:
            raise ValidationError(
                "That is too much to type at once", details="Paste it in smaller pieces."
            )
        if self.ended or not text:
            return
        hidden = self.handle.input_is_hidden()
        self.recorder.input(text, hidden=hidden)
        self.handle.write(text.encode("utf-8"))
        # Somebody is there: the idle warning can come again.
        with self._lock:
            self._warned.discard("idle")

    def resize(self, rows: int, cols: int) -> None:
        """
        Change the terminal's size.

        Args:
            rows: Its height.
            cols: Its width.
        """
        if self.ended:
            return
        # A window that changed size is not somebody at the keyboard: only
        # typing restarts the idle limit.
        self.handle.resize(rows, cols)
        self.rows, self.cols = self.handle.rows, self.handle.cols
        self.recorder.resize(self.rows, self.cols)

    def close(self, reason: str = PTY_END_CLOSED) -> None:
        """
        End the session: the program is stopped and the recording closed.

        Blocks while the program is stopped (seconds, and longer for a unit
        that is slow to stop): call it from a thread, not an event loop.

        Args:
            reason: Why: ``closed`` (the person ended it), ``killed``,
                ``shutdown``.
        """
        with self._lock:
            if self._reason is None:
                self._reason = reason
        self.handle.terminate()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=10)
        if not self._ended.is_set():
            # No reader ever started (or it is stuck): finish here.
            self._finish()

    def wait(self, timeout: float | None = None) -> bool:
        """
        Wait for the session to end.

        Args:
            timeout: Seconds to wait at most.

        Returns:
            True when it ended.
        """
        return self._ended.wait(timeout)

    # -- the reader -------------------------------------------------------------

    def _notices(self) -> None:
        """Say once, a little before, that a limit is coming."""
        for name, remaining in (
            ("idle", self.handle.idle_remaining()),
            ("limit", self.handle.limit_remaining()),
        ):
            with self._lock:
                if remaining > WARN_SECONDS:
                    self._warned.discard(name)
                    continue
                if name in self._warned or remaining <= 0:
                    continue
                self._warned.add(name)
            self._deliver({"type": "notice", "kind": name, "seconds": int(remaining)})

    def _pump(self) -> None:
        """Read the program until it ends, or the connection stays away too long."""
        try:
            while True:
                data = self.handle.read(_READ_SLICE)
                if data is None:
                    break
                if data:
                    text = self._decoder.decode(data)
                    if text:
                        self.recorder.output(text)
                        self._deliver({"type": "output", "data": text})
                self._notices()
                with self._lock:
                    away = self._detached_at
                if away is not None and time.monotonic() - away > DETACH_GRACE_SECONDS:
                    with self._lock:
                        if self._reason is None:
                            self._reason = END_DISCONNECTED
                    self.handle.terminate()
                    # The terminal now says it ended; the next read is None.
        finally:
            self._finish()

    def _finish(self) -> None:
        """Record how it ended, forget the session, and tell whoever is there. Once."""
        with self._lock:
            if self._finishing:
                return
            self._finishing = True
            reason = self._reason or self.handle.end_reason or PTY_END_EXITED
            sink = self._sink
        tail = self._decoder.decode(b"", final=True)
        if tail:
            self.recorder.output(tail)
        exit_code = self.handle.exit_code
        interrupted = reason == END_SHUTDOWN
        summary = self.recorder.close(reason=reason, exit_code=exit_code, interrupted=interrupted)
        ended = time.time()
        try:
            self.handle.close()
        except OSError as exc:
            logger.warning("Closing the terminal of session %s failed: %s", self.id, exc)
        audit.record(
            "terminal.end",
            actor=self.actor,
            target=self.audit_target(),
            outcome="ok",
            details={
                "session": self.id,
                "reason": reason,
                "exit_code": exit_code,
                "duration": round(ended - self.started_at, 1),
                "recording": summary.id,
                "recording_sha256": summary.sha256,
                "recording_bytes": summary.bytes,
                "recording_truncated": summary.truncated_output or summary.truncated_input,
                "recording_failed": summary.failed,
            },
        )
        self._on_end(self)
        if sink is not None:
            self._say(sink, {"type": "ended", "reason": reason, "exit_code": exit_code})
        # Last, so whoever waited for the end finds all of it done: the trail
        # written, the recording closed, the session forgotten.
        self._ended.set()

    def audit_target(self) -> str:
        """
        Returns:
            What the audit trail calls the session's target.
        """
        target = self.plan.target
        if target.kind == KIND_SERVER:
            return "server"
        return f"app:{target.domain}"


class SessionRegistry:
    """The open terminals of this console, and the one way to open another."""

    def __init__(
        self,
        *,
        settings: Callable[[], TerminalSettings] = load_settings,
        runner: Callable[[], CommandRunner] = get_runner,
    ) -> None:
        """
        Args:
            settings: Reads the limits; every call, so a change applies at once.
            runner: The runner terminals are opened through.
        """
        self._settings = settings
        self._runner = runner
        self._lock = threading.Lock()
        self._sessions: dict[str, TerminalSession] = {}

    def check_room(self, actor_label: str, *, kind: str) -> TerminalSettings:
        """
        Refuse, before a ticket is minted, what could not be opened anyway.

        Args:
            actor_label: Who asks, as the audit trail names them.
            kind: ``app`` or ``server``.

        Returns:
            The settings the answer was read from.

        Raises:
            TerminalDisabledError: Terminals are off, or the server's shell is
                not allowed.
            TerminalLimitError: The limits are reached.
        """
        settings = self._settings()
        if kind == KIND_SERVER:
            refusal = settings.server_refusal()
            if refusal is not None:
                raise TerminalDisabledError(refusal[0], details=refusal[1])
        elif not settings.enabled:
            raise TerminalDisabledError(
                "Terminals are turned off on this server",
                details="An administrator turns them on with: noust config set terminal.enabled true",
            )
        with self._lock:
            live = list(self._sessions.values())
        if len(live) >= settings.max_sessions:
            raise TerminalLimitError(
                f"{len(live)} terminals are open, the most this server allows",
                details="Close one, or raise terminal.max_sessions.",
            )
        mine = sum(1 for session in live if session.grant.actor == actor_label)
        if mine >= settings.max_sessions_per_user:
            raise TerminalLimitError(
                f"You already have {mine} terminals open, the most one person may",
                details="Close one, or raise terminal.max_sessions_per_user.",
            )
        return settings

    def open(self, grant: TerminalGrant, plan: SessionPlan, *, actor: Actor) -> TerminalSession:
        """
        Open the terminal a ticket authorised.

        Everything is checked again here, at the one place a session starts:
        the limits, the server's switch, and that the terminal can be recorded.

        Args:
            grant: What the ticket authorised.
            plan: What it runs, from :mod:`noust.terminal.targets`.
            actor: Who is opening it.

        Returns:
            The session, not yet started: call :meth:`TerminalSession.start`.

        Raises:
            TerminalDisabledError: This kind of terminal is not allowed here.
            TerminalLimitError: A limit is reached.
            DeploymentError: The session could not be recorded; no session
                that could not be audited is opened.
        """
        settings = self.check_room(grant.actor, kind=grant.kind)
        runner = self._runner()
        scrubber = plan.scrubber
        try:
            recorder = recordings.SessionRecorder(
                recordings.recordings_dir(),
                session_id=grant.session_id,
                meta={
                    "kind": grant.kind,
                    "domain": plan.target.domain,
                    "service": plan.target.service,
                    "label": plan.target.label,
                    "account": plan.target.account,
                    "actor": grant.actor,
                    "client_ip": grant.client_ip,
                },
                rows=grant.rows,
                cols=grant.cols,
                max_bytes=settings.recording_max_bytes,
                scrubber=scrubber,
            )
        except OSError as exc:
            raise DeploymentError(
                "The terminal was not opened because the session could not be recorded",
                details=f"The recording could not be created: {exc}. "
                "Every terminal is kept in the audit trail, so none opens without one.",
            ) from exc
        opened = False
        try:
            handle = runner.pty(
                list(plan.argv),
                cwd=plan.cwd,
                env=plan.env,
                sandbox=plan.sandbox,
                rows=grant.rows,
                cols=grant.cols,
                max_seconds=settings.max_seconds,
                idle_seconds=settings.idle_seconds,
            )
            opened = True
        finally:
            if not opened:
                # Whatever stopped it, the recording made for it is closed and says so.
                recorder.close(reason="not opened", exit_code=None, interrupted=True)
        session = TerminalSession(grant, plan, handle, recorder, actor=actor, on_end=self._forget)
        with self._lock:
            self._sessions[session.id] = session
        audit.record(
            "terminal.start",
            actor=actor,
            target=session.audit_target(),
            details={
                "session": session.id,
                "account": plan.target.account,
                "service": plan.target.service,
                "client_ip": grant.client_ip,
                "rows": grant.rows,
                "cols": grant.cols,
                "idle_seconds": settings.idle_seconds,
                "max_seconds": settings.max_seconds,
                "recording": recorder.id,
            },
        )
        return session

    def _forget(self, session: TerminalSession) -> None:
        """
        Forget a session that ended.

        Args:
            session: The session.
        """
        with self._lock:
            self._sessions.pop(session.id, None)

    def get(self, session_id: str) -> TerminalSession | None:
        """
        Args:
            session_id: A session's identifier.

        Returns:
            The session, or None when it is not open here.
        """
        with self._lock:
            return self._sessions.get(session_id)

    def resumable(self, session_id: str, *, sid: str, actor: str) -> TerminalSession:
        """
        Find the session a returning connection belongs to.

        Args:
            session_id: The session the ticket names.
            sid: The credential presenting it.
            actor: Who that is.

        Returns:
            The session, still running and owned by that credential.

        Raises:
            TerminalGone: It is not open, or belongs to another sign-in.
        """
        session = self.get(session_id)
        if (
            session is None
            or session.ended
            or session.grant.sid != sid
            or session.grant.actor != actor
        ):
            raise TerminalGone(
                "That terminal is no longer open",
                details="It ended while you were away. Open a new one.",
            )
        return session

    def list(self) -> list[SessionInfo]:
        """
        Returns:
            Every open session, oldest first.
        """
        with self._lock:
            live = list(self._sessions.values())
        return sorted((session.info() for session in live), key=lambda info: info.started_at)

    def active_recordings(self) -> frozenset[str]:
        """
        Returns:
            The recordings of the sessions that are open.
        """
        return frozenset(info.recording for info in self.list() if info.recording)

    def kill(self, session_id: str, *, by: Actor) -> bool:
        """
        End somebody's session.

        Blocks while the program is stopped; call it from a thread.

        Args:
            session_id: The session.
            by: Who ends it, for the audit trail.

        Returns:
            False when there is no such session.
        """
        session = self.get(session_id)
        if session is None:
            return False
        audit.record(
            "terminal.kill",
            actor=by,
            target=session.audit_target(),
            details={"session": session.id, "opened_by": session.grant.actor},
        )
        session.close(END_KILLED)
        return True

    def shutdown(self) -> int:
        """
        End every session: the console is stopping.

        Returns:
            How many were open.
        """
        with self._lock:
            live = list(self._sessions.values())
        for session in live:
            session.close(END_SHUTDOWN)
        return len(live)


_registry: SessionRegistry | None = None
_registry_lock = threading.Lock()


def get_registry() -> SessionRegistry:
    """
    Returns:
        The process's registry, created on first use.
    """
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = SessionRegistry()
        return _registry


def reset_registry(registry: SessionRegistry | None = None) -> None:
    """
    Replace the process's registry; for tests.

    Args:
        registry: The registry to install, or None for a fresh one on next use.
    """
    global _registry
    with _registry_lock:
        old = _registry
        _registry = registry
    if old is not None and old is not registry:
        old.shutdown()


def shutdown_sessions() -> int:
    """
    End every open session. Called when the console stops.

    Returns:
        How many were open.
    """
    with _registry_lock:
        registry = _registry
    return registry.shutdown() if registry is not None else 0


__all__ = [
    "BACKLOG_BYTES",
    "DETACH_GRACE_SECONDS",
    "END_DISCONNECTED",
    "END_KILLED",
    "END_REVOKED",
    "END_SHUTDOWN",
    "KIND_APP",
    "KIND_SERVER",
    "SessionInfo",
    "SessionRegistry",
    "TerminalDisabledError",
    "TerminalGone",
    "TerminalLimitError",
    "TerminalSession",
    "get_registry",
    "reset_registry",
    "shutdown_sessions",
]
