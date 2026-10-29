# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The deliveries a central owes its servers, and what became of each: the ``source_forwards`` table.

GitHub delivers a push to the central once, and answers nobody if the central could not pass it on.
So the central writes the event down for each server that deploys the repository before it answers
GitHub, and a worker hands it over (:mod:`~noust.fleet.sources.forward`): a server that is down
when a push arrives gets it when it is back, and a delivery that could not be made says why. The
row holds the event and what the server answered, never a credential.
"""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from noust.core.store import NoustStore, get_store

__all__ = ["STATES", "Forward", "Forwards"]

#: Where a delivery is: waiting for its server (``pending``), handed over (``delivered``), refused
#: for good (``failed``), or given up on after the server stayed away too long (``expired``).
STATES = ("pending", "delivered", "failed", "expired")

#: Rows kept after they were settled, in seconds: thirty days of what was forwarded and why not.
KEEP_FOR = 30 * 86400


@dataclass(frozen=True)
class Forward:
    """
    One event owed to one server.

    Attributes:
        id: Its id, ``f`` and twelve hex digits.
        delivery: The code host's id for the delivery it came from.
        node: The server.
        kind: ``push``, ``tag`` or ``pull_request``.
        repository: ``owner/repo``.
        ref: The branch or the tag; None for a pull request.
        number: The pull request; None otherwise.
        commit: The commit the event announces, when it says.
        body: What is sent: ``kind``, ``event``, ``delivery``, ``host`` and ``default_branch``.
        state: One of :data:`STATES`.
        attempts: How many times it was tried.
        next_attempt_at: When it is tried next (epoch seconds); None once settled.
        error: Why the last try failed, as ``{text, hint, coded, output}``.
        answer: What the server answered when it took it.
        created_at: When it was written.
        delivered_at: When the server took it.
    """

    id: str
    delivery: str
    node: str
    kind: str
    repository: str
    ref: str | None
    number: int | None
    commit: str | None
    body: Mapping[str, Any]
    state: str
    attempts: int
    next_attempt_at: float | None
    error: Mapping[str, Any] | None
    answer: Mapping[str, Any] | None
    created_at: float
    delivered_at: float | None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The delivery as the API shows it: what it was, where it is, and why not.
        """
        return {
            "id": self.id,
            "delivery": self.delivery,
            "node": self.node,
            "kind": self.kind,
            "repository": self.repository,
            "ref": self.ref,
            "number": self.number,
            "commit": self.commit,
            "state": self.state,
            "attempts": self.attempts,
            "next_attempt_at": self.next_attempt_at,
            "error": dict(self.error) if self.error else None,
            "answer": dict(self.answer) if self.answer else None,
            "created_at": self.created_at,
            "delivered_at": self.delivered_at,
        }


def _forward(row: Any) -> Forward:
    """
    Args:
        row: A ``source_forwards`` row.

    Returns:
        The delivery.
    """
    return Forward(
        id=str(row["id"]),
        delivery=str(row["delivery"]),
        node=str(row["node"]),
        kind=str(row["kind"]),
        repository=str(row["repo"]),
        ref=row["ref"],
        number=row["number"],
        commit=row["commit_sha"],
        body=json.loads(row["event"]),
        state=str(row["state"]),
        attempts=int(row["attempts"]),
        next_attempt_at=row["next_attempt_at"],
        error=json.loads(row["error"]) if row["error"] else None,
        answer=json.loads(row["answer"]) if row["answer"] else None,
        created_at=float(row["created_at"]),
        delivered_at=row["delivered_at"],
    )


class Forwards:
    """
    The deliveries owed, read and written in one place.

    Args:
        store: The central's store; the process-wide one by default.
        clock: Epoch seconds (tests move it).
    """

    def __init__(
        self, store: NoustStore | None = None, clock: Callable[[], float] = time.time
    ) -> None:
        self._store = store
        self._clock = clock

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def add(
        self,
        node: str,
        *,
        body: Mapping[str, Any],
        delivery: str,
        repository: str,
        ref: str | None,
        number: int | None,
        commit: str | None,
    ) -> Forward:
        """
        Write down an event owed to a server.

        Args:
            node: The server.
            body: What is sent to it.
            delivery: The code host's id for the delivery.
            repository: ``owner/repo``.
            ref: The branch or tag.
            number: The pull request.
            commit: The commit announced.

        Returns:
            The delivery, pending, due now.
        """
        now = self._clock()
        forward_id = "f" + secrets.token_hex(6)
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO source_forwards (id, delivery, node, kind, repo, ref, number, "
                "commit_sha, event, state, attempts, next_attempt_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?)",
                (
                    forward_id,
                    delivery,
                    node,
                    str(body["kind"]),
                    repository,
                    ref,
                    number,
                    commit,
                    json.dumps(body, sort_keys=True),
                    now,
                    now,
                ),
            )
        added = self.get(forward_id)
        if added is None:
            raise RuntimeError("a delivery that was just written is not there")
        return added

    def get(self, forward_id: str) -> Forward | None:
        """
        Args:
            forward_id: A delivery's id.

        Returns:
            The delivery, or None.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT * FROM source_forwards WHERE id = ?", (forward_id,))
            .fetchone()
        )
        return _forward(row) if row else None

    def due(self) -> list[Forward]:
        """
        Returns:
            The pending deliveries whose turn has come, oldest first.
        """
        rows = self.store._get_connection().execute(
            "SELECT * FROM source_forwards WHERE state = 'pending' AND next_attempt_at <= ? "
            "ORDER BY created_at, id",
            (self._clock(),),
        )
        return [_forward(row) for row in rows.fetchall()]

    def pending(self) -> int:
        """
        Returns:
            How many deliveries wait for their server.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT COUNT(*) AS n FROM source_forwards WHERE state = 'pending'")
            .fetchone()
        )
        return int(row["n"])

    def counts(self) -> dict[str, int]:
        """
        Returns:
            How many deliveries are in each of :data:`STATES`.
        """
        rows = self.store._get_connection().execute(
            "SELECT state, COUNT(*) AS n FROM source_forwards GROUP BY state"
        )
        found = {str(row["state"]): int(row["n"]) for row in rows.fetchall()}
        return {state: found.get(state, 0) for state in STATES}

    def requeue(self, forward_id: str) -> Forward | None:
        """
        Make a delivery that was given up on due again.

        Args:
            forward_id: The delivery.

        Returns:
            The delivery, pending and due now; None when there is no such delivery or it is not
            one that was given up on (a delivery that was taken is not sent twice).
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE source_forwards SET state = 'pending', next_attempt_at = ?, "
                "created_at = ?, error = NULL WHERE id = ? AND state IN ('failed', 'expired')",
                (self._clock(), self._clock(), forward_id),
            )
            changed = cursor.rowcount
        return self.get(forward_id) if changed else None

    def list(
        self, *, node: str | None = None, state: str | None = None, limit: int = 100
    ) -> list[Forward]:
        """
        Args:
            node: Only this server's.
            state: Only in this state.
            limit: Most rows.

        Returns:
            The deliveries, newest first.
        """
        rows = self.store._get_connection().execute(
            "SELECT * FROM source_forwards WHERE (? IS NULL OR node = ?) AND (? IS NULL OR state = ?) "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (node, node, state, state, max(1, min(limit, 1000))),
        )
        return [_forward(row) for row in rows.fetchall()]

    def delivered(self, forward_id: str, answer: Mapping[str, Any]) -> None:
        """
        Record that the server took the event.

        Args:
            forward_id: The delivery.
            answer: What the server answered.
        """
        now = self._clock()
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE source_forwards SET state = 'delivered', attempts = attempts + 1, "
                "next_attempt_at = NULL, error = NULL, answer = ?, delivered_at = ? WHERE id = ?",
                (json.dumps(dict(answer), sort_keys=True), now, forward_id),
            )

    def retry(self, forward_id: str, error: Mapping[str, Any], at: float) -> None:
        """
        Record that the server could not be reached and the event is tried again.

        Args:
            forward_id: The delivery.
            error: Why.
            at: When it is tried next (epoch seconds).
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE source_forwards SET attempts = attempts + 1, next_attempt_at = ?, "
                "error = ? WHERE id = ?",
                (at, json.dumps(dict(error), sort_keys=True), forward_id),
            )

    def settle(self, forward_id: str, state: str, error: Mapping[str, Any]) -> None:
        """
        Record that the event will not be delivered.

        Args:
            forward_id: The delivery.
            state: ``failed`` or ``expired``.
            error: Why.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE source_forwards SET state = ?, attempts = attempts + 1, "
                "next_attempt_at = NULL, error = ? WHERE id = ?",
                (state, json.dumps(dict(error), sort_keys=True), forward_id),
            )

    def forget(self, node: str) -> int:
        """
        Forget every delivery owed to a server, which left the fleet.

        Args:
            node: The server.

        Returns:
            How many were forgotten.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM source_forwards WHERE node = ?", (node,))
            return int(cursor.rowcount)

    def purge(self) -> int:
        """
        Forget the deliveries that were settled more than :data:`KEEP_FOR` seconds ago.

        Returns:
            How many were forgotten.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM source_forwards WHERE state != 'pending' AND created_at < ?",
                (self._clock() - KEEP_FOR,),
            )
            return int(cursor.rowcount)
