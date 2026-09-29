"""
Schema v12 (Noust 3.1): every table and column 3.1 adds to the store.

3.1 is built by several areas at once (identity, audit, server, fleet, builds,
databases, metrics, notifications), and each one owns exactly one fragment
below instead of editing the store's monolithic schema. :func:`apply_v12` is the
single definition of what v12 is: the migration from v11 runs it, and so does
the creation of a fresh store after the v11 script, so an upgraded store and a
new one can never differ.

Rules for a fragment:

- Tables and indexes are ``CREATE ... IF NOT EXISTS``, so the step is
  idempotent like every migration since v8.
- A column added to an existing table goes in the area's ``*_COLUMNS`` list as
  ``(table, column, definition)``; it is added only when missing. SQLite cannot
  add a column with a non-constant default, a ``PRIMARY KEY`` or ``UNIQUE``
  constraint, so a definition must not use them.
- Nothing here reads or rewrites rows. A data migration that must run once
  belongs in the area's own module, called explicitly and tested there.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

# Identity: accounts, roles, invitations, approvals (backlog 30, ENS G01/G02/G08).
ACCOUNTS_SQL = ""
ACCOUNTS_COLUMNS: list[tuple[str, str, str]] = []

# Audit v2: the event chain, shipping checkpoints, review attestations (ENS G03-G05).
AUDIT_SQL = ""
AUDIT_COLUMNS: list[tuple[str, str, str]] = []

# The server: accepted risks, scheduled reboots, pending host changes (backlog 32, 46).
SERVER_SQL = ""
SERVER_COLUMNS: list[tuple[str, str, str]] = []

# The fleet: node labels, access ceilings, fleet jobs (backlog 33, 45).
FLEET_SQL = ""
FLEET_COLUMNS: list[tuple[str, str, str]] = []

# Unprivileged builds: per-application sandbox state (backlog 44).
BUILD_SQL = ""
BUILD_COLUMNS: list[tuple[str, str, str]] = []

# Databases: links to applications, backup policies, saved queries (backlog 38).
DATABASES_SQL = ""
DATABASES_COLUMNS: list[tuple[str, str, str]] = []

# Metrics: rollup tiers and their maxima (backlog 39, 51).
METRICS_SQL = ""
METRICS_COLUMNS: list[tuple[str, str, str]] = []

# Notifications and webhooks: deliveries received and sent (backlog 49, 52).
NOTIFICATIONS_SQL = ""
NOTIFICATIONS_COLUMNS: list[tuple[str, str, str]] = []


def _fragments() -> list[str]:
    return [
        ACCOUNTS_SQL,
        AUDIT_SQL,
        SERVER_SQL,
        FLEET_SQL,
        BUILD_SQL,
        DATABASES_SQL,
        METRICS_SQL,
        NOTIFICATIONS_SQL,
    ]


def _columns() -> list[tuple[str, str, str]]:
    return [
        *ACCOUNTS_COLUMNS,
        *AUDIT_COLUMNS,
        *SERVER_COLUMNS,
        *FLEET_COLUMNS,
        *BUILD_COLUMNS,
        *DATABASES_COLUMNS,
        *METRICS_COLUMNS,
        *NOTIFICATIONS_COLUMNS,
    ]


def _existing_columns(cursor: sqlite3.Cursor, table: str) -> set[str]:
    # PRAGMA arguments cannot be bound; the table names come from the lists
    # above, which are code, never input.
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def apply_v12(cursor: sqlite3.Cursor, run_script: Callable[[sqlite3.Cursor, str], None]) -> None:
    """
    Bring a v11 schema to v12, inside the caller's transaction.

    Args:
        cursor: Cursor already inside the migration's transaction.
        run_script: The store's statement-by-statement script runner
            (``noust.core.store._run_script``), passed in so this module does
            not import the store and the two cannot import each other.
    """
    for fragment in _fragments():
        if fragment.strip():
            run_script(cursor, fragment)
    for table, column, definition in _columns():
        if column not in _existing_columns(cursor, table):
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
