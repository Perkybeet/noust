# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The store's record of the replicas of a host engine, seen from both ends.

One table from schema v14 (``core/schema_v14.py``, the databases fragment): ``db_replication``.
On the primary there is a row per replica that was made room for; on a replica there is one row,
for the primary it follows. It says what Noust made (the account, the slot, the firewall rule, the
setting it changed) so that taking a replica away removes that and nothing else. Never a
password.

Rows go through the store's own transaction so ``--dry-run`` rehearses them like every other
write.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from noust.core.store import NoustStore

__all__ = ["PRIMARY", "REPLICA", "ReplicaRecord", "ReplicationRecords"]

#: Which end of the replication a row describes.
PRIMARY = "primary"
REPLICA = "replica"

#: The longest error a row keeps.
_MAX_ERROR = 4000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ReplicaRecord:
    """
    One end of a replication.

    Attributes:
        side: ``primary`` (a replica was made room for here) or ``replica`` (this server follows).
        name: The replica's name.
        account: The replication account.
        slot: The physical replication slot.
        host: The name the replica reaches the primary by.
        port: The primary's port.
        source: Where the replica connects from, as ``pg_hba.conf`` has it (primary rows).
        listen_was: ``listen_addresses`` before Noust widened it, when it did.
        firewall: What the firewall was told: ``{"backend", "active", "created"}``.
        passfile: The replica's passfile (replica rows).
        saved_as: Where the replica's own data directory was set aside (replica rows).
        state: ``prepared``, ``following``, ``promoted``.
        last_error: What the last failed step said.
        engine: The engine's key.
        created_at: When the row was made.
        updated_at: When it last changed.
    """

    side: str
    name: str
    account: str
    slot: str
    host: str
    port: int
    source: str | None = None
    listen_was: str | None = None
    firewall: dict[str, Any] = field(default_factory=dict)
    passfile: str | None = None
    saved_as: str | None = None
    state: str = "prepared"
    last_error: str | None = None
    engine: str = "postgresql"
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ReplicaRecord:
        """
        Args:
            row: The ``db_replication`` row.

        Returns:
            The record.
        """
        data = dict(row)
        try:
            data["firewall"] = json.loads(data.get("firewall") or "{}")
        except ValueError:
            data["firewall"] = {}
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The record as plain data, JSON-ready.
        """
        return asdict(self)


class ReplicationRecords:
    """
    Read and write the records, through a store's transactions.
    """

    def __init__(self, store: NoustStore) -> None:
        """
        Args:
            store: The store holding the table.
        """
        self.store = store

    def get(self, side: str, name: str, engine: str = "postgresql") -> ReplicaRecord | None:
        """
        Args:
            side: ``primary`` or ``replica``.
            name: The replica's name.
            engine: The engine's key.

        Returns:
            The record, or None.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT * FROM db_replication WHERE engine = ? AND side = ? AND name = ?",
                (engine, side, name),
            )
            row = cursor.fetchone()
            return ReplicaRecord.from_row(row) if row else None

    def records(self, side: str | None = None, engine: str = "postgresql") -> list[ReplicaRecord]:
        """
        Args:
            side: Only this end's rows; both when None.
            engine: The engine's key.

        Returns:
            The records, by name.
        """
        with self.store._transaction() as cursor:
            if side is None:
                cursor.execute(
                    "SELECT * FROM db_replication WHERE engine = ? ORDER BY side, name", (engine,)
                )
            else:
                cursor.execute(
                    "SELECT * FROM db_replication WHERE engine = ? AND side = ? ORDER BY name",
                    (engine, side),
                )
            return [ReplicaRecord.from_row(row) for row in cursor.fetchall()]

    def save(self, record: ReplicaRecord) -> ReplicaRecord:
        """
        Create or replace a record.

        Args:
            record: The record.

        Returns:
            The record as stored.
        """
        stamp = _now()
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO db_replication (engine, side, name, account, slot, host, port, "
                "source, listen_was, firewall, passfile, saved_as, state, last_error, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(engine, side, name) DO UPDATE SET account = excluded.account, "
                "slot = excluded.slot, host = excluded.host, port = excluded.port, "
                "source = excluded.source, "
                "listen_was = COALESCE(db_replication.listen_was, excluded.listen_was), "
                "firewall = excluded.firewall, passfile = excluded.passfile, "
                "saved_as = excluded.saved_as, state = excluded.state, "
                "last_error = excluded.last_error, updated_at = excluded.updated_at",
                (
                    record.engine,
                    record.side,
                    record.name,
                    record.account,
                    record.slot,
                    record.host,
                    record.port,
                    record.source,
                    record.listen_was,
                    json.dumps(record.firewall, sort_keys=True),
                    record.passfile,
                    record.saved_as,
                    record.state,
                    (record.last_error or "")[-_MAX_ERROR:] or None,
                    stamp,
                    stamp,
                ),
            )
        stored = self.get(record.side, record.name, record.engine)
        return stored if stored is not None else record

    def set_state(
        self,
        side: str,
        name: str,
        state: str,
        *,
        error: str | None = None,
        engine: str = "postgresql",
    ) -> None:
        """
        Note how a replica stands.

        Args:
            side: ``primary`` or ``replica``.
            name: The replica's name.
            state: ``prepared``, ``following`` or ``promoted``.
            error: What went wrong, verbatim; cleared when None.
            engine: The engine's key.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE db_replication SET state = ?, last_error = ?, updated_at = ? "
                "WHERE engine = ? AND side = ? AND name = ?",
                (state, (error or "")[-_MAX_ERROR:] or None, _now(), engine, side, name),
            )

    def delete(self, side: str, name: str, engine: str = "postgresql") -> bool:
        """
        Forget a record.

        Args:
            side: ``primary`` or ``replica``.
            name: The replica's name.
            engine: The engine's key.

        Returns:
            Whether there was one.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM db_replication WHERE engine = ? AND side = ? AND name = ?",
                (engine, side, name),
            )
            return cursor.rowcount > 0
