# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one-time ticket a terminal is opened with.

A WebSocket handshake cannot carry a header, and a terminal is the one stream
where "the credential that opened it is the person at the keyboard" must hold.
So opening one is two steps. First an authenticated call, inside sudo mode,
that names the target and is held to its permission and its team
(``POST /api/apps/{domain}/terminal``); it mints a ticket. Then the socket,
which presents the ticket and its own credential, and gets a shell only if
both are the same person's.

What makes a ticket worth little to anyone who reads it out of a log:

- it is **single use**: the first presentation spends it, whatever the outcome,
  so a stolen ticket that is tried first by the wrong person burns for the
  right one, which is told to ask again;
- it lives **seconds** (:data:`TICKET_TTL_SECONDS`), long enough for a browser
  to open a socket and no longer;
- it is **bound** to the credential that asked for it, and to the address it
  asked from when the console is configured to bind sessions to addresses: a
  ticket opens a shell only for the session that minted it;
- it names **one target**: the terminal it opens is the one that was
  authorised, however the socket's URL is edited;
- only a **hash** is kept, in memory: a console restart forgets every pending
  ticket, which costs a person one reload.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from noust.core.exceptions import SecurityError

#: How long a minted ticket may wait for its socket.
TICKET_TTL_SECONDS = 20

#: Tickets minted and not yet used, at most. A client that mints without
#: connecting is refused rather than allowed to grow this without bound.
MAX_PENDING = 256

#: What a ticket can open.
KIND_APP = "app"
KIND_SERVER = "server"
KINDS = (KIND_APP, KIND_SERVER)


class TicketRefused(SecurityError):
    """
    A ticket that cannot open a terminal.

    Attributes:
        code: ``unknown``, ``expired``, ``other_credential`` or ``other_address``.
    """

    def __init__(self, code: str, message: str, details: str = "") -> None:
        """
        Args:
            code: What went wrong, for the audit trail.
            message: The sentence for the person.
            details: What to do.
        """
        super().__init__(message, details=details)
        self.code = code


@dataclass(frozen=True)
class TerminalGrant:
    """
    What one ticket authorises.

    Attributes:
        session_id: The identifier the session will have, chosen at minting so
            the audit trail links the ticket to the session it opened.
        kind: :data:`KIND_APP` or :data:`KIND_SERVER`.
        domain: The application, for an application's terminal.
        service: The Compose service, for a stack's; None for the only or the
            default one.
        shell: The shell asked for, by name; None for the default.
        rows: The terminal's height when it opens.
        cols: The terminal's width when it opens.
        sid: The ``sid`` of the credential that minted it.
        actor: Who that is, as the audit trail names them.
        client_ip: Where the minting call came from.
        expires_at: ``time.time()`` after which it is worth nothing.
        resume: The session to go back to, when the ticket is for a connection
            that dropped and not for a new terminal.
    """

    session_id: str
    kind: str
    domain: str | None
    service: str | None
    shell: str | None
    rows: int
    cols: int
    sid: str
    actor: str
    client_ip: str
    expires_at: float
    resume: str | None = None


#: What a session's identifier looks like: sixteen hexadecimal digits.
_SESSION_ID = re.compile(r"[0-9a-f]{16}")


def _digest(ticket: str) -> str:
    """
    Args:
        ticket: A ticket value.

    Returns:
        What is kept of it.
    """
    return hashlib.sha256(ticket.encode()).hexdigest()


class TicketStore:
    """Tickets minted and not yet spent, in memory."""

    def __init__(
        self, *, ttl: int = TICKET_TTL_SECONDS, clock: Callable[[], float] = time.time
    ) -> None:
        """
        Args:
            ttl: Seconds a ticket lives.
            clock: ``time.time``; a test passes its own.
        """
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: dict[str, TerminalGrant] = {}

    def _forget_expired(self, now: float) -> None:
        """
        Drop what has waited too long. The caller holds the lock.

        Args:
            now: The time.
        """
        for key in [key for key, grant in self._pending.items() if grant.expires_at <= now]:
            del self._pending[key]

    def mint(
        self,
        *,
        kind: str,
        domain: str | None,
        service: str | None,
        shell_name: str | None,
        rows: int,
        cols: int,
        sid: str,
        actor: str,
        client_ip: str,
        resume: str | None = None,
    ) -> tuple[str, TerminalGrant]:
        """
        Issue a ticket for one terminal.

        Args:
            kind: :data:`KIND_APP` or :data:`KIND_SERVER`.
            domain: The application, for an application's terminal.
            service: The Compose service, if one was named.
            shell_name: The shell asked for, if one was.
            rows: The terminal's height.
            cols: The terminal's width.
            sid: The minting credential's ``sid``.
            actor: Who it is, for the audit trail.
            client_ip: Where the call came from.
            resume: The session to go back to, for a connection that dropped.

        Returns:
            The ticket value, which is shown once, and what it authorises.

        Raises:
            ValueError: An unknown kind.
            TicketRefused: Too many tickets are waiting.
        """
        if kind not in KINDS:
            raise ValueError(f"Unknown terminal kind {kind!r}")
        if resume is not None and not _SESSION_ID.fullmatch(resume):
            raise ValueError(f"Not a session id: {resume!r}")
        now = self._clock()
        ticket = secrets.token_urlsafe(32)
        grant = TerminalGrant(
            session_id=resume or secrets.token_hex(8),
            kind=kind,
            domain=domain,
            service=service,
            shell=shell_name,
            rows=rows,
            cols=cols,
            sid=sid,
            actor=actor,
            client_ip=client_ip,
            expires_at=now + self._ttl,
            resume=resume,
        )
        with self._lock:
            self._forget_expired(now)
            if len(self._pending) >= MAX_PENDING:
                raise TicketRefused(
                    "busy",
                    "Too many terminals are waiting to open",
                    "Wait a few seconds and try again.",
                )
            self._pending[_digest(ticket)] = grant
        return ticket, grant

    def redeem(
        self, ticket: str, *, sid: str, actor: str, client_ip: str, bind_address: bool
    ) -> TerminalGrant:
        """
        Spend a ticket.

        It is spent by the first presentation, whatever the outcome below: a
        ticket that was tried by the wrong credential is not kept for the
        right one.

        Args:
            ticket: The value presented.
            sid: The ``sid`` of the credential the socket authenticated with.
            actor: Who that is.
            client_ip: Where the handshake came from.
            bind_address: Whether the ticket must be spent from the address it
                was minted for (``web`` setting ``bind_session_to_ip``).

        Returns:
            What the ticket authorises.

        Raises:
            TicketRefused: It is unknown, spent, expired, or minted for
                another credential or address.
        """
        key = _digest(ticket or "")
        now = self._clock()
        with self._lock:
            grant = self._pending.pop(key, None)
            self._forget_expired(now)
        if grant is None:
            raise TicketRefused(
                "unknown",
                "This terminal ticket is not valid or was already used",
                "Open the terminal again from the console.",
            )
        if grant.expires_at <= now:
            raise TicketRefused(
                "expired",
                "This terminal ticket expired",
                "Open the terminal again from the console.",
            )
        if not (hmac.compare_digest(grant.sid, sid) and hmac.compare_digest(grant.actor, actor)):
            raise TicketRefused(
                "other_credential",
                "This terminal ticket was issued to another sign-in",
                "Open the terminal again from the console.",
            )
        if bind_address and grant.client_ip != client_ip:
            raise TicketRefused(
                "other_address",
                "This terminal ticket was issued to another address",
                "Open the terminal again from the console.",
            )
        return grant

    def revoke(self, sid: str) -> int:
        """
        Spend every ticket a credential minted: it signed out, or was revoked.

        Args:
            sid: The credential's ``sid``.

        Returns:
            How many were spent.
        """
        with self._lock:
            mine = [key for key, grant in self._pending.items() if grant.sid == sid]
            for key in mine:
                del self._pending[key]
        return len(mine)

    def pending(self) -> int:
        """
        Returns:
            How many tickets are waiting, not counting the expired.
        """
        with self._lock:
            self._forget_expired(self._clock())
            return len(self._pending)


_store: TicketStore | None = None
_store_lock = threading.Lock()


def get_tickets() -> TicketStore:
    """
    Returns:
        The process's ticket store, created on first use.
    """
    global _store
    with _store_lock:
        if _store is None:
            _store = TicketStore()
        return _store


def reset_tickets() -> None:
    """Forget every pending ticket and the store itself; for tests."""
    global _store
    with _store_lock:
        _store = None
