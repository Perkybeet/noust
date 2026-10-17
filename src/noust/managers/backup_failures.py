# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The whole output of a backup that failed, kept so the notice can point at it.

A notice carries the end of the failure, literal but bounded to what a chat message holds (twelve
lines, twelve hundred characters), and a policy keeps four thousand. A dump that fails after ninety
lines of warnings, or an rclone that says why on its first line, left the operator with a sentence
that stopped short of the reason. Every place that announces a failed backup now records what the
tool said here first, and the notice names the record: ``noust backup failures show 42``, and a link
to ``/api/backups/failures/42/output`` that a signed-in operator opens as plain text.

Three rules shape it:

* **Whole, with a generous bound.** :data:`MAX_OUTPUT` characters, cut at the front when it is
  exceeded because a tool says why it stopped last, and the record says it was cut.
* **Scrubbed before it is stored.** The excerpt of a notice passes through the redaction of the
  runner; the whole output is a bigger thing to leave in the store, so it is scrubbed again with
  every credential Noust knows and the shapes of vendor tokens (:mod:`noust.core.redact`).
* **Never a second failure.** A log that cannot be written turns into "no record", not into an
  exception that replaces the backup's own error: the notice then goes out as it always did.

Only the newest :data:`KEEP_LAST` records are kept.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from noust.core.exceptions import NoustError
from noust.core.notifications.composers import failure_command
from noust.core.redact import scrubber_for
from noust.core.store import NoustStore, get_store

__all__ = [
    "KEEP_LAST",
    "MAX_OUTPUT",
    "BackupFailure",
    "BackupFailureLog",
    "record_failure",
]

logger = logging.getLogger(__name__)

#: The most characters of one failure that are kept. Past it the front is cut.
MAX_OUTPUT = 512 * 1024

#: How many records the log keeps; the oldest go when a new one arrives.
KEEP_LAST = 200

#: What leads a cut output, so nobody takes the start of it for the start of the tool's words.
_CUT_MARK = "[...]\n"

#: The kinds of failure: no backup was taken, or a destination refused the one that was.
KINDS = ("backup", "upload")


@dataclass(frozen=True)
class BackupFailure:
    """
    One failed backup as recorded.

    Attributes:
        id: The record's number, the one a notice names.
        created_at: When it was recorded, UTC.
        kind: ``backup`` when no backup was taken, ``upload`` when a destination refused one.
        subject: What it was about: an application's domain, ``Noust``, ``engine/database``.
        destination: The destination that refused it, for an ``upload``.
        output: What the tool said, whole up to :data:`MAX_OUTPUT`.
        truncated: Whether the front of it was cut.
    """

    id: int
    created_at: str
    kind: str
    subject: str
    destination: str | None
    output: str
    truncated: bool

    @property
    def size(self) -> int:
        """The output's size in bytes."""
        return len(self.output.encode("utf-8"))

    @property
    def command(self) -> str:
        """The command that prints this record."""
        return failure_command(self.id)

    def summary(self) -> dict[str, Any]:
        """
        Returns:
            The record without its output, for a list.
        """
        return {
            "id": self.id,
            "created_at": self.created_at,
            "kind": self.kind,
            "subject": self.subject,
            "destination": self.destination,
            "size": self.size,
            "truncated": self.truncated,
            "command": self.command,
        }

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The record with its output.
        """
        return {**self.summary(), "output": self.output}


def _bounded(text: str) -> tuple[str, bool]:
    """
    Args:
        text: What a tool said.

    Returns:
        The text within :data:`MAX_OUTPUT` characters, cut at the front with a mark when it was
        longer, and whether it was.
    """
    if len(text) <= MAX_OUTPUT:
        return text, False
    return _CUT_MARK + text[-MAX_OUTPUT:], True


class BackupFailureLog:
    """
    The records of failed backups, in the store.

    Args:
        store: The store; the process-wide one when None.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store, resolved on first use."""
        if self._store is None:
            self._store = get_store()
        return self._store

    def record(self, kind: str, subject: str, output: str, destination: str | None = None) -> int:
        """
        Keep what a tool said when a backup failed.

        Args:
            kind: One of :data:`KINDS`.
            subject: What the backup was about.
            output: What was said, verbatim; scrubbing is :func:`record_failure`'s job.
            destination: The destination that refused it, for an ``upload``.

        Returns:
            The new record's id.
        """
        kept, truncated = _bounded(output)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO backup_failures (created_at, kind, subject, destination, output, "
                "truncated) VALUES (?, ?, ?, ?, ?, ?)",
                (stamp, kind, subject, destination, kept, int(truncated)),
            )
            new_id = int(cursor.lastrowid or 0)
            cursor.execute(
                "DELETE FROM backup_failures WHERE id NOT IN "
                "(SELECT id FROM backup_failures ORDER BY id DESC LIMIT ?)",
                (KEEP_LAST,),
            )
        return new_id

    def get(self, failure_id: int) -> BackupFailure | None:
        """
        Args:
            failure_id: A record's id.

        Returns:
            The record, or None when there is none (or it was pruned).
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM backup_failures WHERE id = ?", (failure_id,))
            row = cursor.fetchone()
        return _from_row(row) if row else None

    def recent(self, *, subject: str | None = None, limit: int = 50) -> list[BackupFailure]:
        """
        Args:
            subject: Only the failures about this.
            limit: How many at most.

        Returns:
            The newest first.
        """
        query = "SELECT * FROM backup_failures"
        params: list[Any] = []
        if subject is not None:
            query += " WHERE subject = ?"
            params.append(subject)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, limit))
        with self.store._transaction() as cursor:
            cursor.execute(query, params)
            return [_from_row(row) for row in cursor.fetchall()]


def _from_row(row: sqlite3.Row) -> BackupFailure:
    """
    Args:
        row: A ``backup_failures`` row.

    Returns:
        The record.
    """
    return BackupFailure(
        id=int(row["id"]),
        created_at=str(row["created_at"]),
        kind=str(row["kind"]),
        subject=str(row["subject"]),
        destination=row["destination"],
        output=str(row["output"]),
        truncated=bool(row["truncated"]),
    )


def record_failure(
    kind: str, subject: str, error: str | None, *, destination: str | None = None
) -> int | None:
    """
    Keep the whole output of a failed backup, scrubbed, for the notice to name.

    The one call every announcer makes before it composes its notice. It never raises: a store that
    is locked or unreadable must not replace the backup's own error with its own.

    Args:
        kind: ``backup`` or ``upload``.
        subject: What the backup was about.
        error: ``str(error)`` of the failure, ``message`` then ``Details``.
        destination: The destination that refused it, for an ``upload``.

    Returns:
        The record's id, or None when there was nothing to keep or it could not be written.
    """
    if error is None:
        return None
    try:
        text = scrubber_for(subject).scrub(error)
        return BackupFailureLog().record(kind, subject, text, destination=destination)
    except (sqlite3.Error, NoustError, OSError) as exc:
        logger.warning("Could not keep the output of a failed backup of %s: %s", subject, exc)
        return None
