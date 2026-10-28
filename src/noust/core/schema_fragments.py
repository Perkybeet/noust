# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The machinery a schema version made of per-area fragments shares.

From v14 on, a version's tables and columns are written one fragment per area (``<AREA>_SQL``
and ``<AREA>_COLUMNS``), so lanes that each add to the store never touch each other's lines.
Applying such a version, and asking whether a store already has all of it, is the same for every
version: it lives here once (rule 3), and :mod:`noust.core.schema_v14` and
:mod:`noust.core.schema_v16` hand it their own lists.

A fragment holds ``CREATE ... IF NOT EXISTS`` statements only; a column is added only when it is
missing and never with a non-constant default; nothing here rewrites a row.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Sequence

__all__ = ["CREATED", "Column", "apply", "existing_columns", "incomplete"]

#: A column a version adds to a table: (table, column, definition).
Column = tuple[str, str, str]

#: What a fragment creates, as the store's catalog names it.
CREATED = re.compile(
    r"CREATE\s+(?:UNIQUE\s+)?(TABLE|INDEX)\s+IF\s+NOT\s+EXISTS\s+(\w+)", re.IGNORECASE
)


def existing_columns(cursor: sqlite3.Cursor, table: str) -> set[str]:
    """
    The columns a table has now.

    Args:
        cursor: A cursor on the store.
        table: A table named in a version's column list (code, never input).

    Returns:
        The names of its columns; empty when the table does not exist.
    """
    # PRAGMA arguments cannot be bound; the table names come from the versions' lists, which are
    # code, never input.
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def incomplete(cursor: sqlite3.Cursor, fragments: Sequence[str], columns: Sequence[Column]) -> bool:
    """
    Whether a store lacks any table, index or column of a version.

    Only reads, so a complete store is checked without a write lock.

    Args:
        cursor: A cursor on the store.
        fragments: The version's fragments.
        columns: The version's columns.

    Returns:
        True when :func:`apply` would add something.
    """
    cursor.execute("SELECT type, name FROM sqlite_master WHERE type IN ('table', 'index')")
    present = {(str(kind), str(name)) for kind, name in cursor.fetchall()}
    for fragment in fragments:
        for kind, name in CREATED.findall(fragment):
            if (kind.lower(), name) not in present:
                return True
    return any(column not in existing_columns(cursor, table) for table, column, _ in columns)


def apply(
    cursor: sqlite3.Cursor,
    run_script: Callable[[sqlite3.Cursor, str], None],
    fragments: Sequence[str],
    columns: Sequence[Column],
) -> None:
    """
    Add a version's tables, indexes and columns, inside the caller's transaction.

    Args:
        cursor: Cursor already inside the migration's transaction.
        run_script: The store's statement-by-statement script runner, passed in so no schema
            module imports the store.
        fragments: The version's fragments.
        columns: The version's columns.
    """
    for fragment in fragments:
        if fragment.strip():
            run_script(cursor, fragment)
    for table, column, definition in columns:
        if column not in existing_columns(cursor, table):
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
