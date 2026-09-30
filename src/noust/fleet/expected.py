# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Outages a central was told to expect: a node rebooting because it was asked to.

A node asked to reboot through the central (``POST /api/server/power/reboot``
forwarded by :mod:`noust.web.api.node_proxy`) goes quiet on purpose. Without
this, the fleet would announce it unreachable a couple of minutes later and
recovered a minute after that: two alerts about something the operator just
did. So the proxy records the reboot here, with the moment the node said it
would happen, and the aggregator (:mod:`noust.fleet.aggregate`) neither counts
the silence towards an outage nor announces it while the expectation holds.

The expectation is bounded: it lasts until :data:`REBOOT_GRACE_SECONDS` after
the reboot is due. A node that is still down then is an outage like any other,
announced after the usual grace. One that answers after the due moment has
rebooted, and the expectation goes. Cancelling the scheduled reboot through the
central removes it at once.

Kept in the store (``node_expected_outages``), not in memory: the reboot is
asked through the console, and the probe that notices the silence may run in
the monitor daemon.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from noust.core.store import NoustStore, get_store

log = logging.getLogger(__name__)

#: How long after its due moment a rebooting node may stay silent unannounced.
#: A server with a slow firmware, a file system check or a kernel update can
#: take several minutes; beyond this it is an outage.
REBOOT_GRACE_SECONDS = 15 * 60

#: The node API calls that schedule and cancel a reboot, as the proxy sees them.
REBOOT_PATH = "/api/server/power/reboot"
CANCEL_PATH = "/api/server/power/scheduled"


@dataclass(frozen=True)
class ExpectedOutage:
    """
    A node's silence the central expects.

    Attributes:
        node: The node's name.
        kind: ``reboot``.
        due_at: When the node said it would go down, ISO 8601 UTC.
        expires_at: When the silence stops being expected, ISO 8601 UTC.
        requested_by: Who asked, as the central's audit log names them.
        requested_at: When, ISO 8601 UTC.
    """

    node: str
    kind: str
    due_at: str
    expires_at: str
    requested_by: str | None
    requested_at: str

    def to_dict(self) -> dict[str, Any]:
        """
        Return the expectation as plain data, for JSON.

        Returns:
            Every field.
        """
        return asdict(self)


def _now() -> datetime:
    """
    Return the wall clock, in UTC.

    Returns:
        Now.
    """
    return datetime.now(timezone.utc)


def _parse(text: str | None) -> datetime | None:
    """
    Read an ISO 8601 moment, assuming UTC when it names no zone.

    Args:
        text: The moment.

    Returns:
        It, or None when it is not one.
    """
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def expect_reboot(
    node: str,
    *,
    due: str | None,
    requested_by: str | None,
    store: NoustStore | None = None,
    now: datetime | None = None,
) -> ExpectedOutage:
    """
    Record that a node was asked to reboot, so its silence is not an outage.

    Args:
        node: The node's name.
        due: When the node said the reboot happens (its ``scheduled_for``);
            now when it did not say.
        requested_by: Who asked.
        store: The store; the process-wide one by default.
        now: The current time (tests).

    Returns:
        The expectation.
    """
    moment = now or _now()
    due_at = max(_parse(due) or moment, moment)
    outage = ExpectedOutage(
        node=node,
        kind="reboot",
        due_at=due_at.isoformat(timespec="seconds"),
        expires_at=(due_at + timedelta(seconds=REBOOT_GRACE_SECONDS)).isoformat(timespec="seconds"),
        requested_by=requested_by,
        requested_at=moment.isoformat(timespec="seconds"),
    )
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "INSERT INTO node_expected_outages "
            "(node, kind, due_at, expires_at, requested_by, requested_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(node) DO UPDATE SET kind = excluded.kind, "
            "due_at = excluded.due_at, expires_at = excluded.expires_at, "
            "requested_by = excluded.requested_by, requested_at = excluded.requested_at",
            (
                outage.node,
                outage.kind,
                outage.due_at,
                outage.expires_at,
                outage.requested_by,
                outage.requested_at,
            ),
        )
    return outage


def clear(node: str, *, store: NoustStore | None = None) -> None:
    """
    Stop expecting a node's silence.

    Args:
        node: The node's name.
        store: The store; the process-wide one by default.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute("DELETE FROM node_expected_outages WHERE node = ?", (node,))


def current(
    node: str, *, store: NoustStore | None = None, now: datetime | None = None
) -> ExpectedOutage | None:
    """
    Return a node's expected outage while it holds.

    Args:
        node: The node's name.
        store: The store; the process-wide one by default.
        now: The current time (tests).

    Returns:
        The expectation, or None when there is none or it expired (and is
        then removed).
    """
    store = store or get_store()
    row = (
        store._get_connection()
        .execute(
            "SELECT node, kind, due_at, expires_at, requested_by, requested_at "
            "FROM node_expected_outages WHERE node = ?",
            (node,),
        )
        .fetchone()
    )
    if row is None:
        return None
    outage = ExpectedOutage(*tuple(row))
    expires = _parse(outage.expires_at)
    if expires is None or expires <= (now or _now()):
        clear(node, store=store)
        return None
    return outage


def answered(node: str, *, store: NoustStore | None = None, now: datetime | None = None) -> None:
    """
    Note that a node answered: once its reboot was due, it has happened.

    Args:
        node: The node's name.
        store: The store; the process-wide one by default.
        now: The current time (tests).
    """
    store = store or get_store()
    outage = current(node, store=store, now=now)
    if outage is None:
        return
    due = _parse(outage.due_at)
    if due is not None and due <= (now or _now()):
        clear(node, store=store)


def watches(method: str, target: str) -> bool:
    """
    Tell the proxy whether a forwarded call changes what the central expects.

    Args:
        method: The HTTP method, upper case.
        target: The node's path.

    Returns:
        True for scheduling a reboot and cancelling a scheduled one.
    """
    return (method, target) in (("POST", REBOOT_PATH), ("DELETE", CANCEL_PATH))


def observe(
    node: str,
    method: str,
    target: str,
    status: int,
    body: bytes,
    *,
    actor: str | None,
    store: NoustStore | None = None,
) -> ExpectedOutage | None:
    """
    Learn from a forwarded power call what the central should expect of a node.

    An error boundary for the proxy: the node already answered, and what it
    answered reaches the operator whatever happens here.

    Args:
        node: The node's name.
        method: The HTTP method, upper case.
        target: The node's path.
        status: The node's HTTP status.
        body: The node's answer.
        actor: Who asked.
        store: The store; the process-wide one by default.

    Returns:
        The expectation recorded, or None.
    """
    if not 200 <= status < 300 or not watches(method, target):
        return None
    try:
        if method == "DELETE":
            clear(node, store=store)
            return None
        try:
            answer = json.loads(body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError):
            answer = {}
        due = answer.get("scheduled_for") if isinstance(answer, dict) else None
        return expect_reboot(
            node, due=due if isinstance(due, str) else None, requested_by=actor, store=store
        )
    except sqlite3.Error as exc:
        log.warning("The expected reboot of %s could not be recorded: %s", node, exc)
        return None


__all__ = [
    "REBOOT_GRACE_SECONDS",
    "ExpectedOutage",
    "answered",
    "clear",
    "current",
    "expect_reboot",
    "observe",
    "watches",
]
