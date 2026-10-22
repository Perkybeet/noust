# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The store's record of an engine's continuous archiving (point-in-time recovery).

One table from schema v14 (``core/schema_v14.py``, the backups fragment): ``db_pitr``, one row
per engine, saying which cluster is archived (its system identifier, so a cluster restored
from elsewhere is never mixed into the archive of another), where the archive is, how often a
base backup is taken, what is kept and where it is sent, and how the last run ended.

The base backups themselves, and the archived segments, are files that describe themselves
(:mod:`noust.managers.database.pitr`): this row is the policy, so a server rebuilt from the
files alone can still recover.

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

__all__ = ["STATUS_FAILED", "STATUS_OK", "PitrPolicy", "PitrRecords"]

#: How a run ended.
STATUS_OK = "ok"
STATUS_FAILED = "failed"

#: The longest error a row keeps: the end of it, where a tool says why it stopped.
_MAX_ERROR = 4000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class PitrPolicy:
    """
    How an engine's write-ahead log is archived and what recovery is kept.

    Attributes:
        engine: The engine's key (``postgresql``).
        system_id: The cluster's system identifier: the archive is this cluster's alone.
        archive_path: Where the engine's archiver puts each segment.
        base_every_hours: A new base backup is taken when the last is older than this.
        retention_count: Base backups kept (each with the log from it on); None for no limit.
        retention_days: How far back a recovery must be possible; None for no limit by age.
        destinations: Remote copies: ``{"name", "retention_count", "retention_days"}`` per
            destination.
        wal_level_was: The ``wal_level`` before archiving raised it, so disabling puts it back.
        enabled: Whether archiving is on.
        last_run_at: When the schedule last ran.
        last_status: ``ok``, ``failed`` or None before the first run.
        last_error: What the last failed run said, verbatim.
        last_base_at: When the last base backup finished.
        last_sync_at: When the archive was last sent to every destination.
        created_at: When archiving was turned on.
        updated_at: When the policy last changed.
    """

    engine: str
    system_id: str
    archive_path: str
    base_every_hours: int
    retention_count: int | None = None
    retention_days: int | None = None
    destinations: list[dict[str, Any]] = field(default_factory=list)
    wal_level_was: str | None = None
    enabled: bool = True
    last_run_at: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    last_base_at: str | None = None
    last_sync_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> PitrPolicy:
        """
        Build a policy from a row.

        Args:
            row: The ``db_pitr`` row.

        Returns:
            The policy.
        """
        data = dict(row)
        try:
            destinations = json.loads(data.get("destinations") or "[]")
        except (json.JSONDecodeError, TypeError):
            destinations = []
        data["destinations"] = [d for d in destinations if isinstance(d, dict)]
        data["enabled"] = bool(data.get("enabled"))
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The policy as plain data, JSON-ready.
        """
        return asdict(self)


class PitrRecords:
    """
    Read and write the policies, through a store's transactions.

    The store's ``_transaction`` is used on purpose, not a connection of this module's own: it
    is where ``--dry-run`` turns a write into a rehearsal.
    """

    def __init__(self, store: NoustStore) -> None:
        """
        Args:
            store: The store holding the table.
        """
        self.store = store

    def get(self, engine: str) -> PitrPolicy | None:
        """
        Args:
            engine: The engine's key.

        Returns:
            Its policy, or None when its log is not archived.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM db_pitr WHERE engine = ?", (engine,))
            row = cursor.fetchone()
            return PitrPolicy.from_row(row) if row else None

    def policies(self) -> list[PitrPolicy]:
        """
        Returns:
            Every policy, by engine.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM db_pitr ORDER BY engine")
            return [PitrPolicy.from_row(row) for row in cursor.fetchall()]

    def save(self, policy: PitrPolicy) -> PitrPolicy:
        """
        Create or replace a policy, keeping what its last run recorded.

        Args:
            policy: The policy.

        Returns:
            The policy as stored.
        """
        stamp = _now()
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO db_pitr (engine, system_id, archive_path, base_every_hours, "
                "retention_count, retention_days, destinations, wal_level_was, enabled, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(engine) DO UPDATE SET system_id = excluded.system_id, "
                "archive_path = excluded.archive_path, "
                "base_every_hours = excluded.base_every_hours, "
                "retention_count = excluded.retention_count, "
                "retention_days = excluded.retention_days, "
                "destinations = excluded.destinations, "
                "wal_level_was = COALESCE(db_pitr.wal_level_was, excluded.wal_level_was), "
                "enabled = excluded.enabled, updated_at = excluded.updated_at",
                (
                    policy.engine,
                    policy.system_id,
                    policy.archive_path,
                    policy.base_every_hours,
                    policy.retention_count,
                    policy.retention_days,
                    json.dumps(policy.destinations, sort_keys=True),
                    policy.wal_level_was,
                    int(policy.enabled),
                    stamp,
                    stamp,
                ),
            )
        stored = self.get(policy.engine)
        return stored if stored is not None else policy

    def record_run(
        self, engine: str, *, ok: bool, error: str | None, base: bool, sync: bool
    ) -> None:
        """
        Note how a run ended.

        Args:
            engine: The engine's key.
            ok: Whether the run did everything it was meant to.
            error: What it said when it did not, verbatim.
            base: Whether the run took a base backup.
            sync: Whether the run sent the archive to every destination.
        """
        stamp = _now()
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE db_pitr SET last_run_at = ?, last_status = ?, last_error = ?, "
                "last_base_at = CASE WHEN ? THEN ? ELSE last_base_at END, "
                "last_sync_at = CASE WHEN ? THEN ? ELSE last_sync_at END WHERE engine = ?",
                (
                    stamp,
                    STATUS_OK if ok else STATUS_FAILED,
                    None if ok else (error or "")[-_MAX_ERROR:],
                    int(base),
                    stamp,
                    int(sync),
                    stamp,
                    engine,
                ),
            )

    def delete(self, engine: str) -> bool:
        """
        Forget a policy.

        Args:
            engine: The engine's key.

        Returns:
            Whether there was one.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM db_pitr WHERE engine = ?", (engine,))
            return cursor.rowcount > 0
