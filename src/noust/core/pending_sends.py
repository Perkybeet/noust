# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The copies that were made and could not be sent, and so are owed to a destination (item 201).

A copy that did not reach its destination used to stay on this server, and nothing remembered that
it was owed: the next run took a new copy and sent that one. The copy that did not arrive was only
as safe as the disk it was on, and nobody was told it was only there. This module is where the owing
is written down and read back, and the only reader and writer of ``pending_sends`` (schema v18).

What a row is, and is not:

* **One row per copy and destination.** The same copy failing again is the same row: it keeps the
  moment it was first owed and counts its deferred tries (the ones after the run that made it).
* **Owed is not failed.** A row says a send is going to be tried again, when, and how it has gone
  so far; whether anybody is told is :mod:`noust.managers.backup_sending`'s decision, and the row
  remembers only that a notice covered it, so that its recovery has something to answer.
* **Nothing is owed to nobody.** A destination that was removed, and a copy that was deleted by
  hand, take their rows with them (:meth:`PendingSends.forget_destination`,
  :meth:`PendingSends.forget_copy`).
* **What waits is spared.** :mod:`noust.core.backup_hold` asks :meth:`PendingSends.ids`, so no
  retention of any kind of copy deletes one that is still owed.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from noust.core.store import NoustStore, get_store

__all__ = ["KINDS", "MAX_REASON", "PendingSend", "PendingSends", "owed_by_copy"]

#: The kinds of copy there are, spelled as :mod:`noust.core.backup_hold` and the catalogue spell them.
KINDS = ("app", "database", "volume", "noust")

#: The most characters of what a destination said that a row keeps. The whole output is the record
#: ``backup_failures`` keeps; a row needs enough to tell why, not all of it.
MAX_REASON = 2000

_COLUMNS = (
    "kind, item_id, destination, owner, copy_created_at, reason, permanent, attempts, "
    "failed_at, last_attempt_at, next_attempt_at, noticed_at, failure_id"
)


@dataclass(frozen=True)
class PendingSend:
    """
    One copy owed to one destination.

    Attributes:
        kind: ``app``, ``database``, ``volume`` or ``noust``.
        item_id: The copy's identifier as its manager gives it.
        destination: The destination it is owed to.
        owner: What it is a copy of (an application's domain, ``engine/database``, ``Noust``).
        copy_created_at: When the copy was made, ISO 8601, UTC.
        reason: What the destination said the last time, scrubbed and bounded.
        permanent: Whether that was of the kind waiting does not mend.
        attempts: How many deferred tries have been made.
        failed_at: When the send first failed, ISO 8601, UTC.
        last_attempt_at: When it was last tried.
        next_attempt_at: When it is tried next.
        noticed_at: When a notice first covered it, or None.
        failure_id: The record that keeps the whole output of the last failure, or None.
    """

    kind: str
    item_id: str
    destination: str
    owner: str
    copy_created_at: str
    reason: str
    permanent: bool
    attempts: int
    failed_at: str
    last_attempt_at: str
    next_attempt_at: str
    noticed_at: str | None
    failure_id: int | None

    @property
    def key(self) -> tuple[str, str, str]:
        """Returns: What names the row: kind, copy and destination."""
        return (self.kind, self.item_id, self.destination)

    @property
    def noticed(self) -> bool:
        """Returns: Whether a notice has covered this row."""
        return self.noticed_at is not None

    def to_dict(self) -> dict[str, object]:
        """
        Returns:
            The row as the API and ``noust backup list`` show it: what is owed, to whom, why, and
            when it is tried next.
        """
        return {
            "destination": self.destination,
            "reason": self.reason,
            "attempts": self.attempts,
            "since": self.failed_at,
            "next_attempt_at": self.next_attempt_at,
            "permanent": self.permanent,
        }


def _iso(moment: datetime) -> str:
    """
    Args:
        moment: A moment.

    Returns:
        It in UTC, to the second, as the store keeps every time of this table.
    """
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _kind(kind: str) -> str:
    """
    Args:
        kind: A kind of copy.

    Returns:
        The kind.

    Raises:
        ValueError: When it is not one of :data:`KINDS`.
    """
    if kind not in KINDS:
        raise ValueError(f"A pending send has a kind among {', '.join(KINDS)}, not {kind!r}")
    return kind


def _bounded(text: str) -> str:
    """
    Args:
        text: What a destination said.

    Returns:
        The end of it, within :data:`MAX_REASON`: a tool says why it stopped last.
    """
    return text if len(text) <= MAX_REASON else text[-MAX_REASON:]


def _from_row(row: tuple[object, ...]) -> PendingSend:
    """
    Args:
        row: A row selected with :data:`_COLUMNS`.

    Returns:
        The send it describes.
    """
    return PendingSend(
        kind=str(row[0]),
        item_id=str(row[1]),
        destination=str(row[2]),
        owner=str(row[3]),
        copy_created_at=str(row[4]),
        reason=str(row[5]),
        permanent=bool(row[6]),
        attempts=int(row[7]),  # type: ignore[call-overload]
        failed_at=str(row[8]),
        last_attempt_at=str(row[9]),
        next_attempt_at=str(row[10]),
        noticed_at=None if row[11] is None else str(row[11]),
        failure_id=None if row[12] is None else int(row[12]),  # type: ignore[call-overload]
    )


def owed_by_copy(kind: str, store: NoustStore | None = None) -> dict[str, list[PendingSend]]:
    """
    Say which copies of a kind were made and could not be sent to a destination.

    The one answer a listing asks (``noust backup list``, the API's list of each kind of copy): a store
    that cannot be read is no reason to fail a list, it costs the list this note and nothing else.

    Args:
        kind: ``app``, ``volume``, ``database`` or ``noust``.
        store: The store; the process-wide one by default.

    Returns:
        What is owed for each copy, by the copy's identifier; empty for a kind with nothing owed.
    """
    found: dict[str, list[PendingSend]] = {}
    try:
        rows = PendingSends(store).all()
    except (sqlite3.Error, OSError):
        return found
    for row in rows:
        if row.kind == kind:
            found.setdefault(row.item_id, []).append(row)
    return found


class PendingSends:
    """
    Read and write the copies that are owed to a destination.

    Every method can raise :class:`sqlite3.Error`; the caller is a scheduled run or a pass of the
    resend, which decides what a store that cannot be written means for it.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    # ----------------------------------------------------------------------- writing

    def defer(
        self,
        kind: str,
        item_id: str,
        destination: str,
        *,
        owner: str,
        copy_created_at: datetime,
        reason: str,
        permanent: bool,
        failure_id: int | None,
        now: datetime,
        next_attempt_at: datetime,
    ) -> PendingSend:
        """
        Record that a copy could not be sent, or that it still could not.

        The first failure makes the row; a later one of the same copy and destination keeps when
        it was first owed, how often it was tried and whether a notice covered it, and replaces
        why it failed and when it is tried next.

        Args:
            kind: One of :data:`KINDS`.
            item_id: The copy's identifier.
            destination: The destination.
            owner: What it is a copy of.
            copy_created_at: When the copy was made.
            reason: What the destination said, already scrubbed.
            permanent: Whether waiting does not mend it.
            failure_id: The record of the whole output, if one was kept.
            now: The moment of the failure.
            next_attempt_at: When to try again.

        Returns:
            The row as it is now.

        Raises:
            ValueError: When ``kind`` is not a kind of copy.
        """
        _kind(kind)
        with self.store._transaction() as cursor:
            cursor.execute(
                f"INSERT INTO pending_sends ({_COLUMNS}) "  # noqa: S608 - a constant list of names
                "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, NULL, ?) "
                "ON CONFLICT (kind, item_id, destination) DO UPDATE SET "
                "reason = excluded.reason, permanent = excluded.permanent, "
                "last_attempt_at = excluded.last_attempt_at, "
                "next_attempt_at = excluded.next_attempt_at, "
                "failure_id = COALESCE(excluded.failure_id, pending_sends.failure_id)",
                (
                    kind,
                    item_id,
                    destination,
                    owner,
                    _iso(copy_created_at),
                    _bounded(reason),
                    int(permanent),
                    _iso(now),
                    _iso(now),
                    _iso(next_attempt_at),
                    failure_id,
                ),
            )
        row = self.get(kind, item_id, destination)
        if row is None:
            raise sqlite3.OperationalError("the row that was just written is not there")
        return row

    def failed_again(
        self,
        kind: str,
        item_id: str,
        destination: str,
        *,
        reason: str,
        permanent: bool,
        now: datetime,
        next_attempt_at: datetime,
        failure_id: int | None,
    ) -> PendingSend | None:
        """
        Record that a deferred try failed too.

        Args:
            kind: One of :data:`KINDS`.
            item_id: The copy's identifier.
            destination: The destination.
            reason: What the destination said, already scrubbed.
            permanent: Whether waiting does not mend it.
            now: The moment of the try.
            next_attempt_at: When to try next.
            failure_id: The record of the whole output, if one was kept.

        Returns:
            The row as it is now, or None when nothing is owed any more (it was delivered or
            forgotten while this was being tried).

        Raises:
            ValueError: When ``kind`` is not a kind of copy.
        """
        _kind(kind)
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE pending_sends SET attempts = attempts + 1, reason = ?, permanent = ?, "
                "last_attempt_at = ?, next_attempt_at = ?, "
                "failure_id = COALESCE(?, failure_id) "
                "WHERE kind = ? AND item_id = ? AND destination = ?",
                (
                    _bounded(reason),
                    int(permanent),
                    _iso(now),
                    _iso(next_attempt_at),
                    failure_id,
                    kind,
                    item_id,
                    destination,
                ),
            )
        return self.get(kind, item_id, destination)

    def delivered(self, kind: str, item_id: str, destination: str) -> PendingSend | None:
        """
        Record that a copy reached its destination, which ends what was owed.

        Args:
            kind: One of :data:`KINDS`.
            item_id: The copy's identifier.
            destination: The destination.

        Returns:
            The row that was owed, or None when nothing was.
        """
        row = self.get(kind, item_id, destination)
        if row is None:
            return None
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM pending_sends WHERE kind = ? AND item_id = ? AND destination = ?",
                (kind, item_id, destination),
            )
        return row

    def mark_noticed(self, keys: Iterable[tuple[str, str, str]], now: datetime) -> None:
        """
        Record that a notice covered these rows, the first time it does.

        Args:
            keys: ``(kind, item_id, destination)`` of each row.
            now: The moment of the notice.
        """
        with self.store._transaction() as cursor:
            for kind, item_id, destination in keys:
                cursor.execute(
                    "UPDATE pending_sends SET noticed_at = ? "
                    "WHERE kind = ? AND item_id = ? AND destination = ? AND noticed_at IS NULL",
                    (_iso(now), kind, item_id, destination),
                )

    def forget_destination(self, destination: str) -> int:
        """
        Forget what was owed to a destination that no longer exists.

        Args:
            destination: Its name.

        Returns:
            How many rows went.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM pending_sends WHERE destination = ?", (destination,))
            return int(cursor.rowcount)

    def forget_copy(self, kind: str, item_id: str) -> int:
        """
        Forget what was owed for a copy that no longer exists.

        Args:
            kind: One of :data:`KINDS`.
            item_id: The copy's identifier.

        Returns:
            How many rows went (one per destination it was owed to).
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM pending_sends WHERE kind = ? AND item_id = ?", (kind, item_id)
            )
            return int(cursor.rowcount)

    # ----------------------------------------------------------------------- reading

    def get(self, kind: str, item_id: str, destination: str) -> PendingSend | None:
        """
        Args:
            kind: One of :data:`KINDS`.
            item_id: The copy's identifier.
            destination: The destination.

        Returns:
            The row, or None when the copy is not owed to that destination.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                f"SELECT {_COLUMNS} FROM pending_sends "  # noqa: S608 - a constant list of names
                "WHERE kind = ? AND item_id = ? AND destination = ?",
                (kind, item_id, destination),
            )
            row = cursor.fetchone()
        return _from_row(tuple(row)) if row else None

    def all(self) -> list[PendingSend]:
        """
        Returns:
            Every row, the oldest owed first.
        """
        return self._select("", ())

    def due(self, now: datetime) -> list[PendingSend]:
        """
        Args:
            now: The moment to judge at.

        Returns:
            The rows whose next try has come, the oldest owed first.
        """
        return self._select("WHERE next_attempt_at <= ?", (_iso(now),))

    def for_destination(self, destination: str) -> list[PendingSend]:
        """
        Args:
            destination: A destination.

        Returns:
            What is owed to it, the oldest first.
        """
        return self._select("WHERE destination = ?", (destination,))

    def for_owner(self, kind: str, owner: str) -> list[PendingSend]:
        """
        Args:
            kind: One of :data:`KINDS`.
            owner: What the copies are of.

        Returns:
            What is owed for that owner, the oldest first.
        """
        return self._select("WHERE kind = ? AND owner = ?", (kind, owner))

    def for_copy(self, kind: str, item_id: str) -> list[PendingSend]:
        """
        Args:
            kind: One of :data:`KINDS`.
            item_id: The copy's identifier.

        Returns:
            The destinations the copy is still owed to.
        """
        return self._select("WHERE kind = ? AND item_id = ?", (kind, item_id))

    def ids(self, kind: str) -> set[str]:
        """
        Args:
            kind: One of :data:`KINDS`.

        Returns:
            The identifiers of the copies of that kind that are owed to any destination: what no
            retention may delete.
        """
        return {row.item_id for row in self._select("WHERE kind = ?", (kind,))}

    def _select(self, where: str, params: tuple[object, ...]) -> list[PendingSend]:
        """
        Args:
            where: A ``WHERE`` clause made of constants, or nothing.
            params: Its values.

        Returns:
            The rows, the oldest owed first.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                f"SELECT {_COLUMNS} FROM pending_sends {where} "  # noqa: S608 - constant text
                "ORDER BY failed_at, kind, item_id, destination",
                params,
            )
            return [_from_row(tuple(row)) for row in cursor.fetchall()]
