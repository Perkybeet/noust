# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which SSH login proves that a pending change did not lock anyone out.

A change to sshd or the firewall is kept only after a *new* login: a session
that was already open proves nothing, because reloading sshd or changing the
firewall leaves open connections alone. :func:`find_proof` is the one answer to
"which logins count for this change" (rule 3). :meth:`SshSecurity.confirm`
refuses on it, and the listings of the console and the CLI show it, so what an
operator is told before pressing Keep is exactly what Keep will check.

What counts depends on :attr:`PendingChange.proof`:

- ``operator`` (every sshd change): a login by anyone but a central's tunnel,
  because the tunnel reconnecting says nothing about whether an operator can
  still get in.
- ``any`` (firewall changes): any login, the tunnel included, because the
  question is only whether port 22 is still open.

The history is read uncached since the change: it is a short window, and the
login that matters is the one that happened a moment ago.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from noust.managers.server.security_accounts import TUNNEL_ACCOUNT
from noust.managers.server.security_logins import LoginEvent
from noust.managers.server.security_pending import PendingChange
from noust.managers.server.security_probe import SecurityProbe


@dataclass(frozen=True)
class ChangeProof:
    """
    Whether the proof a change needs is on record.

    Attributes:
        readable: Whether sshd's login history could be read at all.
        error: Why it could not, verbatim; empty when it could.
        login: The newest login that counts for the change, or None while
            none has been seen (or the history cannot be read).
    """

    readable: bool = True
    error: str = ""
    login: LoginEvent | None = None

    @property
    def seen(self) -> bool:
        """Whether a login that counts is on record."""
        return self.login is not None

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the proof for the API and ``--json``.

        Returns:
            ``proof_seen``, ``proof_login`` (``user``, ``source``, ``at``, or
            None), ``proof_readable`` and ``proof_error``.
        """
        login = self.login
        return {
            "proof_seen": self.seen,
            "proof_login": (
                {"user": login.user, "source": login.source, "at": login.at}
                if login is not None
                else None
            ),
            "proof_readable": self.readable,
            "proof_error": self.error,
        }


def find_proof(probe: SecurityProbe, change: PendingChange) -> ChangeProof:
    """
    Look for the login that proves a change kept a way in.

    Args:
        probe: What reads the login history and the central's keys.
        change: The change; its ``applied_at`` starts the window and its
            ``proof`` says which logins count.

    Returns:
        The proof: the newest counting login, or why none can be shown.
    """
    history = probe.logins_since(change.applied_at)
    if not history.readable:
        return ChangeProof(readable=False, error=history.error)
    events = history.after(change.applied_at)
    if change.proof != "any":
        # Read only when there is a login to tell from the central's own.
        centrals = probe.central_fingerprints() if events else set()
        events = [
            event
            for event in events
            if event.user != TUNNEL_ACCOUNT and event.fingerprint not in centrals
        ]
    return ChangeProof(login=events[-1] if events else None)
