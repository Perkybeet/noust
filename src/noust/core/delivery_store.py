# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the store remembers about cancelling a deployment.

A request to cancel is a row addressed to the operation that holds an
application's lock, by the pid and start time that operation recorded
(:class:`noust.core.applock.LockHolder`). The process that asks (``noust
deploy cancel`` in a terminal, the console's API) is rarely the one that
runs the deployment, so the row is the only thing they share: the running
operation reads it, acts on it and writes back what it did, and the requester
reads that.

Addressing the holder rather than the application is what makes a stale
request harmless. A request left behind by an operation that ended, or by a
process that was killed, names a holder that no longer exists, so the next
deployment of the application ignores it and drops it.

Persistence only: the decisions (is this the moment to stop, what is left
undone) are :mod:`noust.core.cancellation`'s.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from noust.core.store import NoustStore, get_store

#: The deployment stopped: its build was interrupted and its release removed.
CANCELLED = "cancelled"
#: The request came after the release started to be activated; the deployment
#: finished (or rolled itself back) as if nothing had been asked.
TOO_LATE = "too_late"
#: The operation finished before it looked at the request.
FINISHED = "finished"
#: What ran cannot be interrupted safely (an in-place build, a Compose stack).
UNSUPPORTED = "unsupported"

#: Every outcome a request can end with.
OUTCOMES = (CANCELLED, TOO_LATE, FINISHED, UNSUPPORTED)

#: Longest name of who asked that is kept.
MAX_REQUESTER = 100


@dataclass(frozen=True)
class CancelRequest:
    """
    One request to cancel what runs on an application.

    Attributes:
        domain: The application.
        holder_pid: The pid of the operation the request is addressed to.
        holder_started_at: When that operation took the lock, as it recorded it.
        requested_by: Who asked, as the audit trail names them.
        requested_at: When, ISO 8601 in UTC.
        outcome: How the operation answered (one of :data:`OUTCOMES`); None
            while it has not looked at the request.
        decided_at: When it answered.
    """

    domain: str
    holder_pid: int
    holder_started_at: str
    requested_by: str | None
    requested_at: str
    outcome: str | None = None
    decided_at: str | None = None


def _now() -> str:
    """
    Returns:
        The current time in UTC, ISO 8601 with its offset.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _request_from(row: sqlite3.Row) -> CancelRequest:
    """
    Read a request from its row.

    Args:
        row: A ``delivery_cancel_requests`` row.

    Returns:
        The request.
    """
    data = dict(row)
    return CancelRequest(
        domain=str(data["domain"]),
        holder_pid=int(data["holder_pid"]),
        holder_started_at=str(data["holder_started_at"]),
        requested_by=data["requested_by"],
        requested_at=str(data["requested_at"]),
        outcome=data["outcome"],
        decided_at=data["decided_at"],
    )


def put_request(
    domain: str,
    *,
    holder_pid: int,
    holder_started_at: str,
    requested_by: str | None,
    store: NoustStore | None = None,
) -> CancelRequest:
    """
    Record a request to cancel the operation that holds an application's lock.

    One row per application: asking again for the same operation keeps the
    first request (and who made it), asking for another replaces the old one,
    which was addressed to an operation that is gone.

    Args:
        domain: The application.
        holder_pid: The pid the operation recorded in the lock.
        holder_started_at: When the operation recorded taking it.
        requested_by: Who asks.
        store: The store; defaults to the process-wide one.

    Returns:
        The request as recorded.
    """
    target = store or get_store()
    who = requested_by[:MAX_REQUESTER] if requested_by else None
    with target._transaction() as cursor:
        cursor.execute("SELECT * FROM delivery_cancel_requests WHERE domain = ?", (domain,))
        row = cursor.fetchone()
        if row is not None:
            existing = _request_from(row)
            if (existing.holder_pid, existing.holder_started_at) == (holder_pid, holder_started_at):
                return existing
        request = CancelRequest(
            domain=domain,
            holder_pid=holder_pid,
            holder_started_at=holder_started_at,
            requested_by=who,
            requested_at=_now(),
        )
        cursor.execute(
            """INSERT OR REPLACE INTO delivery_cancel_requests
               (domain, holder_pid, holder_started_at, requested_by, requested_at, outcome,
                decided_at)
               VALUES (?, ?, ?, ?, ?, NULL, NULL)""",
            (domain, holder_pid, holder_started_at, who, request.requested_at),
        )
    return request


def get_request(domain: str, store: NoustStore | None = None) -> CancelRequest | None:
    """
    Read the request recorded for an application, whatever operation it names.

    Args:
        domain: The application.
        store: The store; defaults to the process-wide one.

    Returns:
        The request, or None.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute("SELECT * FROM delivery_cancel_requests WHERE domain = ?", (domain,))
        row = cursor.fetchone()
    return _request_from(row) if row is not None else None


def request_for(
    domain: str, holder_pid: int, holder_started_at: str, store: NoustStore | None = None
) -> CancelRequest | None:
    """
    Read the request addressed to one operation.

    Args:
        domain: The application.
        holder_pid: The operation's pid.
        holder_started_at: When it took the lock.
        store: The store; defaults to the process-wide one.

    Returns:
        The request, or None when nobody asked to cancel this operation.
    """
    request = get_request(domain, store)
    if request is None:
        return None
    if (request.holder_pid, request.holder_started_at) != (holder_pid, holder_started_at):
        return None
    return request


def decide(
    domain: str,
    holder_pid: int,
    holder_started_at: str,
    outcome: str,
    store: NoustStore | None = None,
) -> bool:
    """
    Record how the operation answered a request.

    Args:
        domain: The application.
        holder_pid: The operation's pid.
        holder_started_at: When it took the lock.
        outcome: One of :data:`OUTCOMES`.
        store: The store; defaults to the process-wide one.

    Returns:
        True when a request addressed to this operation was waiting for an
        answer.

    Raises:
        ValueError: ``outcome`` is not one of :data:`OUTCOMES`.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"not an outcome of a cancel request: {outcome!r}")
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            """UPDATE delivery_cancel_requests SET outcome = ?, decided_at = ?
               WHERE domain = ? AND holder_pid = ? AND holder_started_at = ?
                 AND outcome IS NULL""",
            (outcome, _now(), domain, holder_pid, holder_started_at),
        )
        return cursor.rowcount > 0


def drop_stale(
    domain: str, holder_pid: int, holder_started_at: str, store: NoustStore | None = None
) -> int:
    """
    Remove the requests addressed to operations other than this one.

    Called as an operation starts: whatever is recorded for the application
    was addressed to an earlier holder, which is gone.

    Args:
        domain: The application.
        holder_pid: The starting operation's pid.
        holder_started_at: When it took the lock.
        store: The store; defaults to the process-wide one.

    Returns:
        How many were removed.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            """DELETE FROM delivery_cancel_requests
               WHERE domain = ? AND NOT (holder_pid = ? AND holder_started_at = ?)""",
            (domain, holder_pid, holder_started_at),
        )
        return cursor.rowcount


def mark_deployment_cancelled(
    deployment_id: int, by: str | None, store: NoustStore | None = None
) -> bool:
    """
    Say on a finished deployment's row that it was cancelled.

    The row's status stays ``failed`` (its CHECK cannot be altered); these two
    columns are what tell a cancellation from a failure.

    Args:
        deployment_id: The deployment.
        by: Who cancelled it.
        store: The store; defaults to the process-wide one.

    Returns:
        True when the row exists and was updated.
    """
    who = by[:MAX_REQUESTER] if by else None
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "UPDATE deployments SET cancelled_at = ?, cancelled_by = ? WHERE id = ?",
            (_now(), who, deployment_id),
        )
        return cursor.rowcount > 0
