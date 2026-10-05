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

The history is read since the change: it is a short window, and the login that
matters is the one that happened a moment ago. The console reads every pending
change again every few seconds, and each reading runs ``journalctl``, so what
has been found is remembered for this process, with different rules for what it
found:

- a login that counts stays on record until the change leaves ``pending``: the
  history after a moment only grows, so a login that was seen is still there;
- "no login yet" and "cannot be read" are kept for :data:`NEGATIVE_TTL` seconds
  only, so a login that arrives is shown on the next reading after that;
- Keep never goes by a remembered "no": :meth:`SshSecurity.confirm` asks with
  ``fresh=True``, which reads the history now unless a login was already found.
"""

from __future__ import annotations

import threading
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


#: Seconds a "no new login yet" or "cannot be read" is remembered. Just under the
#: console's three-second reading of a pending change, so a tab reading alone
#: always sees a fresh answer while several readers at once share one.
NEGATIVE_TTL = 2.5


@dataclass(frozen=True)
class _Remembered:
    """
    A proof found earlier in this process.

    Attributes:
        runner: The command runner it was read through: a test's fake machine
            never answers another's.
        proof: What was found.
        at: When, on the probe's clock.
    """

    runner: object
    proof: ChangeProof
    at: float


#: Per change (its id, what counts for it and when it started), guarded by
#: :data:`_lock`, which is also held while the history is read so that readers
#: arriving together wait for one reading instead of each making their own.
_remembered: dict[tuple[str, str, float], _Remembered] = {}
_lock = threading.Lock()


def forget_proofs(keep: set[str] | None = None) -> None:
    """
    Drop what was found about changes.

    Args:
        keep: The ids of the changes still pending, whose proofs stay; every
            other proof is dropped. None drops them all.
    """
    with _lock:
        for key in [key for key in _remembered if keep is None or key[0] not in keep]:
            del _remembered[key]


def _recall(
    key: tuple[str, str, float], probe: SecurityProbe, *, fresh: bool
) -> ChangeProof | None:
    """
    Args:
        key: The change.
        probe: Gives the runner it must have been read through, and the time.
        fresh: Whether a remembered "no" is not good enough.

    Returns:
        What was found before, or None when it has to be looked for again. The
        caller holds :data:`_lock`.
    """
    found = _remembered.get(key)
    if found is None or found.runner is not probe.runner:
        return None
    if found.proof.seen:
        return found.proof
    age = probe.now() - found.at
    # A clock that went back is not a reading that is still young.
    return found.proof if not fresh and 0 <= age < NEGATIVE_TTL else None


def find_proof(probe: SecurityProbe, change: PendingChange, *, fresh: bool = False) -> ChangeProof:
    """
    Look for the login that proves a change kept a way in.

    Args:
        probe: What reads the login history and the central's keys.
        change: The change; its ``applied_at`` starts the window and its
            ``proof`` says which logins count.
        fresh: Read the history now even when a "no new login yet" was found a
            moment ago. A login already found is still returned: it cannot go
            away. What Keep asks with, because it must never refuse on an old
            answer.

    Returns:
        The proof: the newest counting login, or why none can be shown.
    """
    key = (change.id, change.proof, change.applied_at)
    with _lock:
        recalled = _recall(key, probe, fresh=fresh)
        if recalled is not None:
            return recalled
        proof = _look(probe, change)
        # A change that is not waiting any more has nothing to be remembered for.
        if change.status == "pending":
            _remembered[key] = _Remembered(probe.runner, proof, probe.now())
            _drop_stale(probe)
        return proof


def _drop_stale(probe: SecurityProbe) -> None:
    """
    Forget the "no" answers that have aged out, so they do not pile up.

    Args:
        probe: Gives the time. The caller holds :data:`_lock`.
    """
    now = probe.now()
    for key in [
        key
        for key, found in _remembered.items()
        if not found.proof.seen and not 0 <= now - found.at < NEGATIVE_TTL
    ]:
        del _remembered[key]


def _look(probe: SecurityProbe, change: PendingChange) -> ChangeProof:
    """
    Read the login history for a change.

    Args:
        probe: What reads the login history and the central's keys.
        change: The change.

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
