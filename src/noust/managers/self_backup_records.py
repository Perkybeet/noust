# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The store's record of how Noust's own state is backed up.

One table from schema v14 (``core/schema_v14.py``, the backups fragment):
``self_backup_schedule``, a single row (``id`` is always 1, which the table
enforces) saying when the bundle of Noust's own state is taken, how many are
kept here and for how long, where each is sent, and how the last run ended.

The passphrase the bundles are encrypted under is never here: it is a secret
in the secret store (:mod:`noust.managers.self_backup`).

Rows go through the store's own transaction so ``--dry-run`` rehearses them
like every other write.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from noust.core.store import NoustStore

__all__ = ["STATUS_FAILED", "STATUS_OK", "SelfBackupRecords", "SelfBackupSchedule"]

#: How a run or a verification ended.
STATUS_OK = "ok"
STATUS_FAILED = "failed"

#: The longest error a row keeps: the end of it, where a tool says why it stopped.
_MAX_ERROR = 4000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SelfBackupSchedule:
    """
    When and where Noust's own state is backed up.

    Attributes:
        schedule: systemd ``OnCalendar`` expression, after alias expansion.
        retention_count: Scheduled bundles kept locally; None for no limit by count.
        retention_days: Days a scheduled bundle is kept locally; None for no limit by age.
        destinations: Remote copies: ``{"name", "retention_count",
            "retention_days"}`` per destination.
        enabled: Whether the timer exists.
        last_run_at: When the schedule last ran.
        last_status: ``ok``, ``failed`` or None before the first run.
        last_error: What the last failed run said, verbatim.
        last_file: The bundle the last run took.
        last_success_at: When a run last went everywhere it was meant to.
        last_verified_at: When a bundle was last proven restorable.
        last_verify_status: ``ok`` or ``failed`` for that.
        created_at: When the schedule was made.
        updated_at: When it last changed.
    """

    schedule: str
    retention_count: int | None = None
    retention_days: int | None = None
    destinations: list[dict[str, Any]] = field(default_factory=list)
    enabled: bool = True
    last_run_at: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    last_file: str | None = None
    last_success_at: str | None = None
    last_verified_at: str | None = None
    last_verify_status: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> SelfBackupSchedule:
        """
        Build a schedule from a row.

        Args:
            row: The ``self_backup_schedule`` row.

        Returns:
            The schedule.
        """
        data = dict(row)
        data.pop("id", None)
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
            The schedule as plain data, JSON-ready.
        """
        return asdict(self)


class SelfBackupRecords:
    """
    Read and write the schedule, through a store's transactions.

    The store's ``_transaction`` is used on purpose, not a connection of this
    module's own: it is where ``--dry-run`` turns a write into a rehearsal.
    """

    def __init__(self, store: NoustStore) -> None:
        """
        Args:
            store: The store holding the table.
        """
        self.store = store

    def get(self) -> SelfBackupSchedule | None:
        """
        Returns:
            The schedule, or None when Noust's own state is not backed up on one.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM self_backup_schedule WHERE id = 1")
            row = cursor.fetchone()
            return SelfBackupSchedule.from_row(row) if row else None

    def save(self, schedule: SelfBackupSchedule) -> SelfBackupSchedule:
        """
        Create or replace the schedule, keeping what its last run recorded.

        Args:
            schedule: The schedule.

        Returns:
            The schedule as stored.
        """
        stamp = _now()
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO self_backup_schedule (id, schedule, retention_count, "
                "retention_days, destinations, enabled, created_at, updated_at) "
                "VALUES (1, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET schedule = excluded.schedule, "
                "retention_count = excluded.retention_count, "
                "retention_days = excluded.retention_days, "
                "destinations = excluded.destinations, enabled = excluded.enabled, "
                "updated_at = excluded.updated_at",
                (
                    schedule.schedule,
                    schedule.retention_count,
                    schedule.retention_days,
                    json.dumps(schedule.destinations, sort_keys=True),
                    int(schedule.enabled),
                    stamp,
                    stamp,
                ),
            )
        stored = self.get()
        return stored if stored is not None else schedule

    def record_run(self, *, ok: bool, error: str | None, file: str | None) -> None:
        """
        Note how a run ended.

        Args:
            ok: Whether it went everywhere it was meant to.
            error: What it said when it did not, verbatim.
            file: The bundle it took, when it took one.
        """
        stamp = _now()
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE self_backup_schedule SET last_run_at = ?, last_status = ?, "
                "last_error = ?, last_file = ?, "
                "last_success_at = CASE WHEN ? THEN ? ELSE last_success_at END WHERE id = 1",
                (
                    stamp,
                    STATUS_OK if ok else STATUS_FAILED,
                    None if ok else (error or "")[-_MAX_ERROR:],
                    file,
                    int(ok),
                    stamp,
                ),
            )

    def record_verification(self, *, ok: bool) -> None:
        """
        Note that a bundle was proven restorable, or was not.

        Args:
            ok: Whether it verified.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE self_backup_schedule SET last_verified_at = ?, "
                "last_verify_status = ? WHERE id = 1",
                (_now(), STATUS_OK if ok else STATUS_FAILED),
            )

    def delete(self) -> bool:
        """
        Forget the schedule.

        Returns:
            Whether there was one.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM self_backup_schedule WHERE id = 1")
            return cursor.rowcount > 0
