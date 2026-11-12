# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a central says it holds of this server's copies (item 195).

A central takes the copies of a server away through its tunnel (:mod:`noust.fleet.collect`), and
tells the server which it has: a copy it carried, checked against the digest the server listed
for it, and keeps. This module is where the server writes that down and reads it back, and it is
the only reader and writer of ``backup_central_held`` and ``backup_central_syncs`` (schema v18).

What the record is for, and what it is not for:

* It is **what a retention waits for.** A server whose central collects its copies does not delete
  a copy the central does not have yet while its own rules allow keeping it
  (:mod:`noust.core.backup_hold`).
* It is **what makes a copy count as kept elsewhere** for the checks that ask for one
  (``backup_offsite``, ENS) and the sentence that says where it is.
* It is **not a promise.** A central can say anything it likes about itself, so the record is
  replaced as a whole each time the central speaks (what it no longer holds is gone from it), it
  never makes a server delete anything, and a central that stops speaking stops counting after
  :data:`FRESH_DAYS`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore, get_store
from noust.core.timeutil import utc_now_iso

__all__ = [
    "FRESH_DAYS",
    "MAX_HELD",
    "CentralHeld",
    "HeldCopy",
    "Sync",
]

#: How long a central that has not spoken still counts as holding what it said: a day's backups
#: and a long weekend. After it, a copy it holds no longer counts as kept elsewhere and nothing
#: waits for it.
FRESH_DAYS = 7

#: The most copies a central may say it holds of one server. A server that keeps more than this
#: has bigger problems than this table.
MAX_HELD = 200_000

_CENTRAL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?")
_KIND = re.compile(r"[a-z]{1,16}")
_ITEM = re.compile(r"[^\x00-\x1f\x7f]{1,300}")
_SHA = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class HeldCopy:
    """
    A copy a central says it holds.

    Attributes:
        kind: The kind of copy (``app``, ``database``, ``volume``, ``noust``).
        item_id: Its identifier on this server.
        sha256: The digest the central checked it against, or None when the server gave none.
    """

    kind: str
    item_id: str
    sha256: str | None = None


@dataclass(frozen=True)
class Sync:
    """
    When a central last said what it holds, and how much.

    Attributes:
        central: The central's name.
        synced_at: When, UTC, ISO 8601.
        held: How many copies it said.
    """

    central: str
    synced_at: str
    held: int

    def fresh(self, now: datetime | None = None) -> bool:
        """
        Args:
            now: The moment to judge at; now by default.

        Returns:
            Whether it spoke within :data:`FRESH_DAYS`.
        """
        try:
            spoke = datetime.fromisoformat(self.synced_at)
        except ValueError:
            return False
        if spoke.tzinfo is None:
            spoke = spoke.replace(tzinfo=timezone.utc)
        return (now or datetime.now(timezone.utc)) - spoke <= timedelta(days=FRESH_DAYS)


def _central_name(value: str) -> str:
    """
    Args:
        value: A central's name.

    Returns:
        The name.

    Raises:
        ValidationError: When it is not one a fleet token can carry.
    """
    if not isinstance(value, str) or not _CENTRAL.fullmatch(value):
        raise ValidationError(
            f"Invalid central name: {value!r}",
            details="A central is named like its fleet token: lower-case letters, digits and '-'.",
            field="central",
        )
    return value


class CentralHeld:
    """
    Read and write what the centrals say they hold.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def replace(self, central: str, copies: Iterable[HeldCopy]) -> Sync:
        """
        Record what a central holds, in place of whatever it said before.

        Args:
            central: The central.
            copies: Every copy it holds of this server.

        Returns:
            When it spoke and how much it said.

        Raises:
            ValidationError: The name is not a central's, a copy is malformed, or there are more than
                :data:`MAX_HELD`.
        """
        name = _central_name(central)
        rows: dict[tuple[str, str], str | None] = {}
        for copy in copies:
            if not _KIND.fullmatch(copy.kind) or not _ITEM.fullmatch(copy.item_id):
                raise ValidationError(
                    f"Not a copy: {copy.kind!r} {copy.item_id!r}",
                    details="A copy is named by its kind and the identifier the list gave it.",
                    field="items",
                )
            if copy.sha256 is not None and not _SHA.fullmatch(copy.sha256):
                raise ValidationError(
                    f"Not a SHA-256: {copy.sha256!r}", details="It is 64 hex digits.", field="items"
                )
            rows[(copy.kind, copy.item_id)] = copy.sha256
            if len(rows) > MAX_HELD:
                raise ValidationError(
                    f"A central may say it holds {MAX_HELD} copies of a server, no more",
                    field="items",
                )
        now = utc_now_iso()
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM backup_central_held WHERE central = ?", (name,))
            cursor.executemany(
                "INSERT INTO backup_central_held (central, kind, item_id, sha256, held_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [(name, kind, item, sha, now) for (kind, item), sha in rows.items()],
            )
            cursor.execute(
                "INSERT INTO backup_central_syncs (central, synced_at, held) VALUES (?, ?, ?) "
                "ON CONFLICT(central) DO UPDATE SET synced_at = excluded.synced_at, "
                "held = excluded.held",
                (name, now, len(rows)),
            )
        return Sync(name, now, len(rows))

    def forget(self, central: str) -> None:
        """
        Forget a central: what it held and that it ever spoke.

        Args:
            central: The central.
        """
        name = _central_name(central)
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM backup_central_held WHERE central = ?", (name,))
            cursor.execute("DELETE FROM backup_central_syncs WHERE central = ?", (name,))

    def syncs(self) -> list[Sync]:
        """
        Returns:
            Each central that has spoken, the most recent first.
        """
        rows = (
            self.store._get_connection()
            .execute("SELECT central, synced_at, held FROM backup_central_syncs")
            .fetchall()
        )
        found = [Sync(str(r["central"]), str(r["synced_at"]), int(r["held"])) for r in rows]
        return sorted(found, key=lambda sync: sync.synced_at, reverse=True)

    def following(self, now: datetime | None = None) -> list[Sync]:
        """
        Returns:
            The centrals that spoke recently enough to count (:data:`FRESH_DAYS`).
        """
        return [sync for sync in self.syncs() if sync.fresh(now)]

    def holders(self, kind: str, item_id: str, now: datetime | None = None) -> list[str]:
        """
        Name the centrals that hold a copy and spoke recently.

        Args:
            kind: The kind of copy.
            item_id: Its identifier.
            now: The moment to judge at; now by default.

        Returns:
            The centrals, by name.
        """
        fresh = {sync.central for sync in self.following(now)}
        rows = (
            self.store._get_connection()
            .execute(
                "SELECT central FROM backup_central_held WHERE kind = ? AND item_id = ?",
                (kind, item_id),
            )
            .fetchall()
        )
        return sorted(str(row["central"]) for row in rows if str(row["central"]) in fresh)

    def holders_map(self, kind: str, now: datetime | None = None) -> dict[str, set[str]]:
        """
        Say, for every copy of a kind, which centrals that spoke recently hold it.

        Args:
            kind: The kind of copy.
            now: The moment to judge at; now by default.

        Returns:
            Identifier to the centrals; a copy no central holds is not in it.
        """
        fresh = {sync.central for sync in self.following(now)}
        rows = (
            self.store._get_connection()
            .execute("SELECT central, item_id FROM backup_central_held WHERE kind = ?", (kind,))
            .fetchall()
        )
        found: dict[str, set[str]] = {}
        for row in rows:
            if str(row["central"]) in fresh:
                found.setdefault(str(row["item_id"]), set()).add(str(row["central"]))
        return found
