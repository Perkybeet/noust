# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The store's record of what an application's data is backed up with.

One table from schema v14 (``core/schema_v14.py``, the backups fragment):
``volume_backup_policies``, one row per application, keyed by its domain like
an application's own backup schedule. A row says which of the application's
data to back up (every declared target, or the ones listed), when, how long
local and remote copies are kept, where they are sent and how still the data
must be while it is copied.

The archives are not rows: they are files with a signed sidecar beside them,
like application backups, so a backup directory copied to another server is
still a set of backups (:mod:`noust.managers.volume_backups`).

Rows go through the store's own transaction so ``--dry-run`` rehearses them
like every other write.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore

__all__ = [
    "CONSISTENCY_MODES",
    "STATUS_FAILED",
    "STATUS_OK",
    "VolumePolicy",
    "VolumeRecords",
]

#: How still the data is kept while it is archived: left running, frozen in
#: place (``systemctl freeze``, or ``docker pause`` for a Compose stack), or
#: stopped for the duration.
CONSISTENCY_MODES = ("live", "pause", "stop")

#: How a run ended.
STATUS_OK = "ok"
STATUS_FAILED = "failed"

#: The longest error a row keeps: the end of it, where a tool says why it stopped.
_MAX_ERROR = 4000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _list_of(raw: str | None, kind: type) -> list[Any]:
    """
    Args:
        raw: A JSON column.
        kind: The type each item must be.

    Returns:
        The items of that type; empty when the column is anything else.
    """
    try:
        data = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return [item for item in data if isinstance(item, kind)] if isinstance(data, list) else []


@dataclass
class VolumePolicy:
    """
    What one application's data backups do.

    Attributes:
        app_domain: The application.
        schedule: systemd ``OnCalendar`` expression, after alias expansion.
        targets: The data to back up, by target key (``path:uploads``,
            ``volume:shop_pgdata``); empty is every target the application
            declares.
        extra_paths: Data directories to back up besides the ones the
            application declares, relative to where its data lives (``shared/``
            on releases, the application directory in place).
        retention_count: Scheduled backups kept locally per target; None for
            no limit by count.
        retention_days: Days a scheduled backup is kept locally; None for no
            limit by age.
        retention_bytes: The most the scheduled backups kept locally per target may
            weigh together; None for no limit by size.
        remote_only: Delete each scheduled backup from this server once every
            destination holds it and its upload was checked.
        destinations: Remote copies: ``{"name", "retention_count",
            "retention_days", "retention_gb"}`` per destination.
        consistency: One of :data:`CONSISTENCY_MODES`.
        enabled: Whether the timer exists.
        last_run_at: When the policy last ran.
        last_status: ``ok``, ``failed`` or None before the first run.
        last_error: What the last failed run said, verbatim.
        last_backup: The id of the last backup it took.
        last_success_at: When a run last went everywhere it was meant to.
        id: Row id.
        created_at: When the policy was made.
        updated_at: When it last changed.
    """

    app_domain: str
    schedule: str
    targets: list[str] = field(default_factory=list)
    extra_paths: list[str] = field(default_factory=list)
    retention_count: int | None = None
    retention_days: int | None = None
    retention_bytes: int | None = None
    remote_only: bool = False
    destinations: list[dict[str, Any]] = field(default_factory=list)
    consistency: str = "live"
    enabled: bool = True
    last_run_at: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    last_backup: str | None = None
    last_success_at: str | None = None
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> VolumePolicy:
        """
        Build a policy from a row.

        Args:
            row: A ``volume_backup_policies`` row.

        Returns:
            The policy.
        """
        data = dict(row)
        data["targets"] = _list_of(data.get("targets"), str)
        data["extra_paths"] = _list_of(data.get("extra_paths"), str)
        data["destinations"] = _list_of(data.get("destinations"), dict)
        data["enabled"] = bool(data.get("enabled"))
        data["remote_only"] = bool(data.get("remote_only"))
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The policy as plain data, JSON-ready.
        """
        return asdict(self)


class VolumeRecords:
    """
    Read and write the policies, through a store's transactions.

    The store's ``_transaction`` is used on purpose, not a connection of this
    module's own: it is where ``--dry-run`` turns a write into a rehearsal.
    """

    def __init__(self, store: NoustStore) -> None:
        """
        Args:
            store: The store holding the table.
        """
        self.store = store

    def policies(self) -> list[VolumePolicy]:
        """
        Returns:
            Every policy, by application.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM volume_backup_policies ORDER BY app_domain")
            return [VolumePolicy.from_row(row) for row in cursor.fetchall()]

    def policy(self, app_domain: str) -> VolumePolicy | None:
        """
        Read one application's policy.

        Args:
            app_domain: The application.

        Returns:
            The policy, or None.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT * FROM volume_backup_policies WHERE app_domain = ?", (app_domain,)
            )
            row = cursor.fetchone()
            return VolumePolicy.from_row(row) if row else None

    def save_policy(self, policy: VolumePolicy) -> VolumePolicy:
        """
        Create or replace an application's policy, keeping what its last run recorded.

        Args:
            policy: The policy.

        Returns:
            The policy as stored.

        Raises:
            ValidationError: When the consistency mode is not one there is.
        """
        if policy.consistency not in CONSISTENCY_MODES:
            raise ValidationError(
                f"{policy.consistency!r} is not a consistency mode",
                details=f"Use one of: {', '.join(CONSISTENCY_MODES)}.",
                field="consistency",
            )
        stamp = _now()
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO volume_backup_policies (app_domain, targets, extra_paths, schedule, "
                "retention_count, retention_days, retention_bytes, remote_only, destinations, "
                "consistency, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(app_domain) DO UPDATE SET targets = excluded.targets, "
                "extra_paths = excluded.extra_paths, schedule = excluded.schedule, "
                "retention_count = excluded.retention_count, "
                "retention_days = excluded.retention_days, "
                "retention_bytes = excluded.retention_bytes, "
                "remote_only = excluded.remote_only, "
                "destinations = excluded.destinations, consistency = excluded.consistency, "
                "enabled = excluded.enabled, updated_at = excluded.updated_at",
                (
                    policy.app_domain,
                    json.dumps(policy.targets),
                    json.dumps(policy.extra_paths),
                    policy.schedule,
                    policy.retention_count,
                    policy.retention_days,
                    policy.retention_bytes,
                    int(policy.remote_only),
                    json.dumps(policy.destinations, sort_keys=True),
                    policy.consistency,
                    int(policy.enabled),
                    stamp,
                    stamp,
                ),
            )
        stored = self.policy(policy.app_domain)
        return stored if stored is not None else policy

    def record_run(
        self, app_domain: str, *, ok: bool, error: str | None, backup: str | None
    ) -> None:
        """
        Note how a policy's run ended.

        Args:
            app_domain: The application.
            ok: Whether it went everywhere it was meant to.
            error: What it said when it did not, verbatim.
            backup: The last backup it took, when it took one.
        """
        stamp = _now()
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE volume_backup_policies SET last_run_at = ?, last_status = ?, "
                "last_error = ?, last_backup = ?, "
                "last_success_at = CASE WHEN ? THEN ? ELSE last_success_at END "
                "WHERE app_domain = ?",
                (
                    stamp,
                    STATUS_OK if ok else STATUS_FAILED,
                    None if ok else (error or "")[-_MAX_ERROR:],
                    backup,
                    int(ok),
                    stamp,
                    app_domain,
                ),
            )

    def delete_policy(self, app_domain: str) -> bool:
        """
        Forget an application's policy.

        Args:
            app_domain: The application.

        Returns:
            Whether a policy was removed.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM volume_backup_policies WHERE app_domain = ?", (app_domain,))
            return cursor.rowcount > 0
