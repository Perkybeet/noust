"""
Schema v13 (Noust 3.2): every table and column 3.2 adds to the store.

The same rules as :mod:`noust.core.schema_v12`: one fragment per area,
``CREATE ... IF NOT EXISTS``, columns added only when missing and never with a
non-constant default, and no row rewritten here. :func:`apply_v13` is the one
definition of v13, run by the migration from v12 and by a fresh store alike.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable

#: The schema version this module defines.
VERSION = 13

# Deploy hooks the operator declared for an application, as the YAML document
# they wrote (the same shape as a repository's noust.yaml). One row per app;
# no row means the repository's own hooks, if any, apply.
HOOKS_SQL = """
CREATE TABLE IF NOT EXISTS app_hooks (
    app_id INTEGER PRIMARY KEY REFERENCES apps(id) ON DELETE CASCADE,
    document TEXT NOT NULL,
    updated_by TEXT,
    updated_at TEXT NOT NULL
);
"""
HOOKS_COLUMNS: list[tuple[str, str, str]] = []

# What a deployment ran and whether it changed the database's schema, which
# is what makes going back past it a decision instead of a click. The status
# column has a CHECK SQLite cannot alter, so "deployed with warnings" is a
# successful row with warnings, not a new status.
DEPLOY_SQL = ""
DEPLOY_COLUMNS: list[tuple[str, str, str]] = [
    ("deployments", "schema_changed", "INTEGER NOT NULL DEFAULT 0"),
    ("deployments", "hooks", "TEXT"),
    ("deployments", "warnings", "TEXT"),
]

# Per-application settings 3.2 adds, each with its own setter in the store.
# compose_project: the Compose project an adopted stack already runs as.
# site_name: the web server file serving the app when it is not the domain.
# follow_tags: the tag pattern an app deploys instead of a branch.
# backup_before_update: dump the stack's databases before an update.
# identity: the system account the app runs as, when it has its own.
APPS_SQL = ""
APPS_COLUMNS: list[tuple[str, str, str]] = [
    ("apps", "compose_project", "TEXT"),
    ("apps", "site_name", "TEXT"),
    ("apps", "follow_tags", "TEXT"),
    ("apps", "backup_before_update", "INTEGER NOT NULL DEFAULT 1"),
    ("apps", "identity", "TEXT"),
]


# The sandbox by default (3.2): when Noust itself tried an application still
# building as root in the sandbox, before one of its updates. Set once, so a
# trial that failed is not repeated (and re-notified) on every update; an
# operator retries with `noust app sandbox test`.
SANDBOX_SQL = ""
SANDBOX_COLUMNS: list[tuple[str, str, str]] = [
    ("build_sandbox", "auto_trial_at", "TEXT"),
]


def _fragments() -> list[str]:
    return [HOOKS_SQL, DEPLOY_SQL, APPS_SQL, SANDBOX_SQL]


def _columns() -> list[tuple[str, str, str]]:
    return [*HOOKS_COLUMNS, *DEPLOY_COLUMNS, *APPS_COLUMNS, *SANDBOX_COLUMNS]


def _existing_columns(cursor: sqlite3.Cursor, table: str) -> set[str]:
    # PRAGMA arguments cannot be bound; the table names come from the lists
    # above, which are code, never input.
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


_CREATED = re.compile(
    r"CREATE\s+(?:UNIQUE\s+)?(TABLE|INDEX)\s+IF\s+NOT\s+EXISTS\s+(\w+)", re.IGNORECASE
)


def incomplete(cursor: sqlite3.Cursor) -> bool:
    """
    Whether a store lacks any table, index or column v13 defines.

    Only reads, so a complete store is checked without a write lock.

    Args:
        cursor: A cursor on the store.

    Returns:
        True when :func:`apply_v13` would add something.
    """
    cursor.execute("SELECT type, name FROM sqlite_master WHERE type IN ('table', 'index')")
    present = {(str(kind), str(name)) for kind, name in cursor.fetchall()}
    for fragment in _fragments():
        for kind, name in _CREATED.findall(fragment):
            if (kind.lower(), name) not in present:
                return True
    return any(column not in _existing_columns(cursor, table) for table, column, _ in _columns())


def apply_v13(cursor: sqlite3.Cursor, run_script: Callable[[sqlite3.Cursor, str], None]) -> None:
    """
    Bring a v12 schema to v13, inside the caller's transaction.

    Args:
        cursor: Cursor already inside the migration's transaction.
        run_script: The store's statement-by-statement script runner, passed
            in so this module does not import the store.
    """
    for fragment in _fragments():
        if fragment.strip():
            run_script(cursor, fragment)
    for table, column, definition in _columns():
        if column not in _existing_columns(cursor, table):
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
