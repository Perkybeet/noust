# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The store's record of database backups: policies, and what is known of each dump.

Two tables from schema v12 (``core/schema_v12.py``, the databases fragment):

- ``database_backup_policies``: what a database's timer does, one row per
  database, whether or not an application uses it.
- ``database_dumps``: what Noust knows about a dump file beyond the file
  itself: who made it (a person, the timer, a restore's safety copy), its
  SHA-256, the evidence of its last verification and restore test, and which
  destinations hold a copy.

The files on disk stay the truth for which dumps exist; a row without a file
is ignored and pruned, and a file without a row is a dump Noust did not record
(taken before 3.1, or by hand) that reads as unverified.

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

#: Who made a dump. Retention only ever deletes ``scheduled`` ones.
ORIGINS = ("manual", "scheduled", "safety", "unknown")

#: How a verification or a restore test ended.
STATUS_OK = "ok"
STATUS_FAILED = "failed"

#: Longest evidence text kept in a row. The tool's own words, bounded: a failed
#: ``pg_restore --list`` of a large archive is not a thing to keep whole.
MAX_EVIDENCE = 4000


def now() -> str:
    """
    Returns:
        The current time, ISO 8601 with its UTC offset, to the second.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def bound_evidence(text: str) -> str:
    """
    Keep the end of an evidence text, where a tool says why it stopped.

    Args:
        text: What a check printed.

    Returns:
        The text, at most :data:`MAX_EVIDENCE` characters, cut at the front.
    """
    text = text.strip()
    if len(text) <= MAX_EVIDENCE:
        return text
    return "[...]\n" + text[-MAX_EVIDENCE:]


def _decode_list(raw: str | None) -> list[dict[str, Any]]:
    """
    Args:
        raw: A JSON column.

    Returns:
        Its objects; empty when it is not a list of them.
    """
    try:
        data = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


@dataclass
class BackupPolicy:
    """
    What one database's backups do.

    Attributes:
        engine: Canonical engine name.
        db_name: The database (a slot number for Redis, whose snapshot covers
            the instance whichever slot names it).
        schedule: systemd ``OnCalendar`` expression, after alias expansion.
        retention_count: Scheduled dumps kept locally, newest first; None for
            no limit by count.
        retention_days: Days a scheduled dump is kept locally; None for no
            limit by age.
        destinations: Remote copies: ``{"name", "retention_count",
            "retention_days"}`` per destination, the shape an application's
            schedule stores.
        dump_format: PostgreSQL's dump format; None for the engine's default.
        verify_restore: Load each dump into a temporary database and drop it,
            as proof it restores.
        enabled: Whether the timer exists.
        last_run_at: When the policy last ran.
        last_status: ``ok``, ``failed`` or None before the first run.
        last_error: What the last failed run said, verbatim.
        last_dump: The dump the last run took.
        last_success_at: When a run last went everywhere it was meant to.
        id: Row id.
        created_at: When the policy was made.
        updated_at: When it last changed.
    """

    engine: str
    db_name: str
    schedule: str
    retention_count: int | None = None
    retention_days: int | None = None
    destinations: list[dict[str, Any]] = field(default_factory=list)
    dump_format: str | None = None
    verify_restore: bool = False
    enabled: bool = True
    last_run_at: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    last_dump: str | None = None
    last_success_at: str | None = None
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> BackupPolicy:
        """
        Build a policy from a row.

        Args:
            row: A ``database_backup_policies`` row.

        Returns:
            The policy.
        """
        data = dict(row)
        data["destinations"] = _decode_list(data.get("destinations"))
        data["verify_restore"] = bool(data.get("verify_restore"))
        data["enabled"] = bool(data.get("enabled"))
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        """
        Render the policy as plain data.

        Returns:
            A JSON-serialisable dictionary.
        """
        return asdict(self)


@dataclass
class DumpRecord:
    """
    What Noust knows about one dump file.

    Attributes:
        engine: Canonical engine name.
        file_name: The file's name inside the engine's backup directory.
        db_name: The database it belongs to.
        origin: One of :data:`ORIGINS`.
        size: Bytes when it was taken.
        sha256: The digest recorded when it was taken.
        verified_at: When it was last checked.
        verify_status: ``ok`` or ``failed`` for that check.
        verify_method: What the check was (``pg_restore --list``,
            ``tail``...).
        verify_detail: The check's own words, verbatim.
        restore_tested_at: When it was last loaded into a temporary database.
        restore_test_status: ``ok`` or ``failed`` for that test.
        restore_test_detail: The test's evidence, verbatim.
        remote_copies: One object per destination that holds it:
            ``{"destination", "pushed_at", "verified_by", "folder"}``.
        id: Row id.
        created_at: When the row was written.
        updated_at: When it last changed.
    """

    engine: str
    file_name: str
    db_name: str | None = None
    origin: str = "unknown"
    size: int | None = None
    sha256: str | None = None
    verified_at: str | None = None
    verify_status: str | None = None
    verify_method: str | None = None
    verify_detail: str | None = None
    restore_tested_at: str | None = None
    restore_test_status: str | None = None
    restore_test_detail: str | None = None
    remote_copies: list[dict[str, Any]] = field(default_factory=list)
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> DumpRecord:
        """
        Build a record from a row.

        Args:
            row: A ``database_dumps`` row.

        Returns:
            The record.
        """
        data = dict(row)
        data["remote_copies"] = _decode_list(data.get("remote_copies"))
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        """
        Render the record as plain data.

        Returns:
            A JSON-serialisable dictionary.
        """
        return asdict(self)


class BackupRecords:
    """
    Read and write the two tables, through a store's transactions.

    The store's ``_transaction`` is used on purpose, not a connection of this
    module's own: it is where ``--dry-run`` turns a write into a rehearsal.
    """

    def __init__(self, store: NoustStore) -> None:
        """
        Args:
            store: The store holding the tables.
        """
        self.store = store

    # ---------------------------------------------------------------- policies

    def policies(self, engine: str | None = None) -> list[BackupPolicy]:
        """
        List policies.

        Args:
            engine: Only this engine's.

        Returns:
            The policies, by engine then database.
        """
        query = "SELECT * FROM database_backup_policies"
        params: list[object] = []
        if engine is not None:
            query += " WHERE engine = ?"
            params.append(engine)
        with self.store._transaction() as cursor:
            cursor.execute(query + " ORDER BY engine, db_name", params)
            return [BackupPolicy.from_row(row) for row in cursor.fetchall()]

    def policy(self, engine: str, db_name: str) -> BackupPolicy | None:
        """
        Read one database's policy.

        Args:
            engine: The engine.
            db_name: The database.

        Returns:
            The policy, or None.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT * FROM database_backup_policies WHERE engine = ? AND db_name = ?",
                (engine, db_name),
            )
            row = cursor.fetchone()
            return BackupPolicy.from_row(row) if row else None

    def save_policy(self, policy: BackupPolicy) -> BackupPolicy:
        """
        Create or replace a database's policy, keeping what its last run recorded.

        Args:
            policy: The policy.

        Returns:
            The policy as stored.
        """
        stamp = now()
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO database_backup_policies (engine, db_name, schedule, "
                "retention_count, retention_days, destinations, dump_format, verify_restore, "
                "enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(engine, db_name) DO UPDATE SET schedule = excluded.schedule, "
                "retention_count = excluded.retention_count, "
                "retention_days = excluded.retention_days, "
                "destinations = excluded.destinations, dump_format = excluded.dump_format, "
                "verify_restore = excluded.verify_restore, enabled = excluded.enabled, "
                "updated_at = excluded.updated_at",
                (
                    policy.engine,
                    policy.db_name,
                    policy.schedule,
                    policy.retention_count,
                    policy.retention_days,
                    json.dumps(policy.destinations, sort_keys=True),
                    policy.dump_format,
                    int(policy.verify_restore),
                    int(policy.enabled),
                    stamp,
                    stamp,
                ),
            )
        stored = self.policy(policy.engine, policy.db_name)
        return stored if stored is not None else policy

    def record_run(
        self,
        engine: str,
        db_name: str,
        *,
        ok: bool,
        error: str | None,
        dump: str | None,
    ) -> None:
        """
        Note how a policy's run ended.

        Args:
            engine: The engine.
            db_name: The database.
            ok: Whether it went everywhere it was meant to.
            error: What it said when it did not, verbatim.
            dump: The dump it took, when it took one.
        """
        stamp = now()
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE database_backup_policies SET last_run_at = ?, last_status = ?, "
                "last_error = ?, last_dump = ?, "
                "last_success_at = CASE WHEN ? THEN ? ELSE last_success_at END "
                "WHERE engine = ? AND db_name = ?",
                (
                    stamp,
                    STATUS_OK if ok else STATUS_FAILED,
                    None if ok else bound_evidence(error or ""),
                    dump,
                    int(ok),
                    stamp,
                    engine,
                    db_name,
                ),
            )

    def delete_policy(self, engine: str, db_name: str) -> bool:
        """
        Forget a database's policy.

        Args:
            engine: The engine.
            db_name: The database.

        Returns:
            Whether a policy was removed.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM database_backup_policies WHERE engine = ? AND db_name = ?",
                (engine, db_name),
            )
            return cursor.rowcount > 0

    # -------------------------------------------------------------------- dumps

    def dumps(self, engine: str | None = None, db_name: str | None = None) -> list[DumpRecord]:
        """
        List dump records.

        Args:
            engine: Only this engine's.
            db_name: Only this database's (with ``engine``).

        Returns:
            The records, oldest first.
        """
        query = "SELECT * FROM database_dumps WHERE 1=1"
        params: list[object] = []
        for column, value in (("engine", engine), ("db_name", db_name)):
            if value is not None:
                query += f" AND {column} = ?"
                params.append(value)
        with self.store._transaction() as cursor:
            cursor.execute(query + " ORDER BY id", params)
            return [DumpRecord.from_row(row) for row in cursor.fetchall()]

    def dump(self, engine: str, file_name: str) -> DumpRecord | None:
        """
        Read one dump's record.

        Args:
            engine: The engine.
            file_name: The dump's file name.

        Returns:
            The record, or None.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT * FROM database_dumps WHERE engine = ? AND file_name = ?",
                (engine, file_name),
            )
            row = cursor.fetchone()
            return DumpRecord.from_row(row) if row else None

    def save_dump(self, record: DumpRecord) -> DumpRecord:
        """
        Create or replace a dump's record.

        Args:
            record: The record.

        Returns:
            The record as stored.
        """
        stamp = now()
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO database_dumps (engine, file_name, db_name, origin, size, sha256, "
                "verified_at, verify_status, verify_method, verify_detail, restore_tested_at, "
                "restore_test_status, restore_test_detail, remote_copies, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(engine, file_name) DO UPDATE SET db_name = excluded.db_name, "
                "origin = excluded.origin, size = excluded.size, sha256 = excluded.sha256, "
                "verified_at = excluded.verified_at, verify_status = excluded.verify_status, "
                "verify_method = excluded.verify_method, verify_detail = excluded.verify_detail, "
                "restore_tested_at = excluded.restore_tested_at, "
                "restore_test_status = excluded.restore_test_status, "
                "restore_test_detail = excluded.restore_test_detail, "
                "remote_copies = excluded.remote_copies, updated_at = excluded.updated_at",
                (
                    record.engine,
                    record.file_name,
                    record.db_name,
                    record.origin,
                    record.size,
                    record.sha256,
                    record.verified_at,
                    record.verify_status,
                    record.verify_method,
                    record.verify_detail,
                    record.restore_tested_at,
                    record.restore_test_status,
                    record.restore_test_detail,
                    json.dumps(record.remote_copies, sort_keys=True),
                    stamp,
                    stamp,
                ),
            )
        stored = self.dump(record.engine, record.file_name)
        return stored if stored is not None else record

    def delete_dump(self, engine: str, file_name: str) -> bool:
        """
        Forget a dump that is gone.

        Args:
            engine: The engine.
            file_name: The dump's file name.

        Returns:
            Whether a record was removed.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM database_dumps WHERE engine = ? AND file_name = ?",
                (engine, file_name),
            )
            return cursor.rowcount > 0
