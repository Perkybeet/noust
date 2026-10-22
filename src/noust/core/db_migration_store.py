# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which moves of an engine to a newer major version Noust has made, and how far each got.

A major version cannot be installed over the one that holds the data: its
databases are dumped, loaded into a new instance and the applications are
pointed at it (:mod:`noust.managers.database.instance_migration`). That is many
steps over many applications, so the row of ``db_instance_migrations`` (schema
v14, :mod:`noust.core.schema_v14`) is written *before* the first database moves
and says how far it got: a move that fails is undone from it, and a move that
finished is rolled back from it, long after the process that made it is gone.

``moved`` is what went across: each database and the applications that were
pointed at the new instance. No secret is in a row.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from noust.core.exceptions import ValidationError

if TYPE_CHECKING:
    from noust.core.store import NoustStore

#: Where a move is: it is moving databases, it finished, it is being rolled
#: back, it was rolled back, or it failed and was undone.
MOVING = "moving"
DONE = "done"
ROLLING_BACK = "rolling_back"
ROLLED_BACK = "rolled_back"
FAILED = "failed"
STATES: tuple[str, ...] = (MOVING, DONE, ROLLING_BACK, ROLLED_BACK, FAILED)


@dataclass(frozen=True)
class MovedDatabase:
    """
    One database a migration carried across.

    Attributes:
        database: Its name.
        apps: The applications that were pointed at the new instance for it.
    """

    database: str
    apps: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The entry, JSON-ready.
        """
        return {"database": self.database, "apps": list(self.apps)}


@dataclass(frozen=True)
class MigrationRecord:
    """
    One move of an engine to a newer major version.

    Attributes:
        id: Row id.
        source_key: The engine the databases left: the host's (``postgresql``)
            or an instance's key (``postgresql@pg16``).
        target_name: The instance that was made for them.
        flavour: What both run.
        from_version: The version they left, when it is known.
        to_version: The version they went to.
        state: One of :data:`STATES`.
        moved: What went across.
        created_at: When the move began, ISO 8601 in UTC.
        created_by: Who started it.
        finished_at: When it ended, or None while it is under way.
    """

    id: int
    source_key: str
    target_name: str
    flavour: str
    from_version: str | None
    to_version: str
    state: str
    moved: tuple[MovedDatabase, ...] = field(default_factory=tuple)
    created_at: str = ""
    created_by: str | None = None
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "id": self.id,
            "source": self.source_key,
            "target": self.target_name,
            "flavour": self.flavour,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "state": self.state,
            "moved": [entry.to_dict() for entry in self.moved],
            "created_at": self.created_at,
            "created_by": self.created_by,
            "finished_at": self.finished_at,
        }


def _now() -> str:
    """
    Returns:
        The current time, ISO 8601 in UTC.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _decode(raw: str | None) -> tuple[MovedDatabase, ...]:
    """
    Read the ``moved`` column.

    Args:
        raw: The JSON text.

    Returns:
        The entries; none when the text is not a list of them.
    """
    try:
        loaded = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return ()
    entries = []
    for item in loaded if isinstance(loaded, list) else []:
        if isinstance(item, dict) and isinstance(item.get("database"), str):
            apps = item.get("apps")
            entries.append(
                MovedDatabase(
                    item["database"],
                    tuple(str(app) for app in apps) if isinstance(apps, list) else (),
                )
            )
    return tuple(entries)


def _build(row: sqlite3.Row) -> MigrationRecord:
    """
    Build a migration from its row.

    Args:
        row: A ``db_instance_migrations`` row.

    Returns:
        The migration.
    """
    return MigrationRecord(
        id=row["id"],
        source_key=row["source_key"],
        target_name=row["target_name"],
        flavour=row["flavour"],
        from_version=row["from_version"],
        to_version=row["to_version"],
        state=row["state"],
        moved=_decode(row["moved"]),
        created_at=row["created_at"],
        created_by=row["created_by"],
        finished_at=row["finished_at"],
    )


class DbMigrationStore:
    """The moves to a newer major version, in the store's own connection."""

    def __init__(self, store: NoustStore | None = None) -> None:
        """
        Args:
            store: The store; the process-wide one when None.
        """
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store, resolved on first use."""
        if self._store is None:
            from noust.core.store import get_store

            self._store = get_store()
        return self._store

    def all(self) -> list[MigrationRecord]:
        """
        List every move, newest first.

        Returns:
            The rows.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM db_instance_migrations ORDER BY id DESC")
            return [_build(row) for row in cursor.fetchall()]

    def get(self, migration_id: int) -> MigrationRecord | None:
        """
        Find a move.

        Args:
            migration_id: Its number.

        Returns:
            The row, or None.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM db_instance_migrations WHERE id = ?", (migration_id,))
            row = cursor.fetchone()
        return _build(row) if row is not None else None

    def of_target(self, target_name: str) -> MigrationRecord | None:
        """
        Find the newest move that made an instance.

        Args:
            target_name: The new instance's name.

        Returns:
            The row, or None when no move made it.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT * FROM db_instance_migrations WHERE target_name = ? "
                "ORDER BY id DESC LIMIT 1",
                (target_name,),
            )
            row = cursor.fetchone()
        return _build(row) if row is not None else None

    def begin(
        self,
        *,
        source_key: str,
        target_name: str,
        flavour: str,
        from_version: str | None,
        to_version: str,
        created_by: str | None,
    ) -> MigrationRecord:
        """
        Record that a move is about to begin.

        Args:
            source_key: The engine the databases leave.
            target_name: The instance made for them.
            flavour: What both run.
            from_version: The version they leave.
            to_version: The version they go to.
            created_by: Who is moving them.

        Returns:
            The row, ``moving``.
        """
        now = _now()
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO db_instance_migrations (source_key, target_name, flavour, "
                "from_version, to_version, state, moved, created_at, created_by) "
                "VALUES (?, ?, ?, ?, ?, ?, '[]', ?, ?)",
                (
                    source_key,
                    target_name,
                    flavour,
                    from_version,
                    to_version,
                    MOVING,
                    now,
                    created_by,
                ),
            )
            new_id = int(cursor.lastrowid or 0)
        found = self.get(new_id)
        if found is None:  # a rehearsal rolls the insert back
            return MigrationRecord(
                id=new_id,
                source_key=source_key,
                target_name=target_name,
                flavour=flavour,
                from_version=from_version,
                to_version=to_version,
                state=MOVING,
                created_at=now,
                created_by=created_by,
            )
        return found

    def set_state(self, migration_id: int, state: str) -> bool:
        """
        Say how far a move got.

        Args:
            migration_id: Its number.
            state: One of :data:`STATES`; the final ones stamp the end.

        Returns:
            True when the move exists and the state was written.

        Raises:
            ValidationError: Not a state.
        """
        if state not in STATES:
            raise ValidationError(
                f"{state!r} is not a state of a migration",
                details=f"The states are: {', '.join(STATES)}.",
                field="state",
            )
        finished = _now() if state in (DONE, ROLLED_BACK, FAILED) else None
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE db_instance_migrations SET state = ?, finished_at = ? WHERE id = ?",
                (state, finished, migration_id),
            )
            return cursor.rowcount > 0

    def set_moved(self, migration_id: int, moved: list[MovedDatabase]) -> bool:
        """
        Record what has gone across so far.

        Args:
            migration_id: Its number.
            moved: The databases carried over, with their applications.

        Returns:
            True when the move exists and the list was written.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE db_instance_migrations SET moved = ? WHERE id = ?",
                (json.dumps([entry.to_dict() for entry in moved]), migration_id),
            )
            return cursor.rowcount > 0
