# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Schema v17 (Noust 4.2): every table and column 4.2 adds to the store.

The rules of :mod:`noust.core.schema_v14` and :mod:`noust.core.schema_v16`: one fragment per
area, ``CREATE ... IF NOT EXISTS``, columns added only when missing and never with a non-constant
default, and no row rewritten here. :func:`apply_v17` is the one definition of v17, run by the
climb from v16 and by a fresh store alike, and :func:`incomplete` lets a store a development
build of 4.2 stamped v17 before every area had its fragment be completed when it opens
(`NoustStore._complete_v17`).

Each area is owned by one lane of the 4.2 plan and is the only place that lane writes schema: it
fills its own pair and never touches another's, so merging them cannot conflict. A table with a
column that names what an application or a database owns says how a team reaches it
(``OWNED_TABLES`` or ``NOT_OWNED`` in ``tests/test_scope_architecture.py``).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from noust.core import schema_fragments
from noust.core.schema_fragments import Column

__all__ = ["VERSION", "apply_v17", "incomplete"]

#: The schema version this module defines.
VERSION = 17

# Owned by the tasks and panels lane: the panels of the console's Home, one set per account
# (item 113). ``layout`` is a JSON document of version 1 that ``validators/dashboards.py`` judges
# before it is written, so nothing here trusts its shape; ``scope`` says whether a panel follows
# the server chosen in the selector or shows the whole fleet. ``(account_id, id)`` is the key: an
# id is the person's own, and two accounts may both have a panel called ``main``. The rows go with
# their account. Times are UNIX seconds, like every table of the identity area.
HOME_SQL = """
CREATE TABLE IF NOT EXISTS account_dashboards (
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    name TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('server', 'fleet')),
    position INTEGER NOT NULL DEFAULT 0,
    layout TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (account_id, id)
);
"""
HOME_COLUMNS: list[Column] = []

# Owned by the software lane: the ledger of what Noust installed on the server (item 118), one row
# per component of ``managers/server/components.py``. ``packages`` and ``repository_files`` are JSON
# lists (what the install brought, and the source and key files Noust added so that a removal can
# take them back out); ``via`` names the installer that did it. Nothing here names an application
# or a database, so a team's scope has no way to it. ``noust.core.software_ledger`` judges every
# value before it is written. The ledger starts empty on a server that exists: what was there
# before is recognised by the marks Noust leaves, not by a row nobody wrote.
SOFTWARE_SQL = """
CREATE TABLE IF NOT EXISTS installed_software (
    component TEXT PRIMARY KEY,
    packages TEXT NOT NULL DEFAULT '[]',
    repository_files TEXT NOT NULL DEFAULT '[]',
    installed_at TEXT NOT NULL,
    actor TEXT,
    via TEXT NOT NULL
);
"""
SOFTWARE_COLUMNS: list[Column] = []


def _fragments() -> list[str]:
    return [HOME_SQL, SOFTWARE_SQL]


def _columns() -> list[Column]:
    return [*HOME_COLUMNS, *SOFTWARE_COLUMNS]


def incomplete(cursor: sqlite3.Cursor) -> bool:
    """
    Whether a store lacks any table, index or column v17 defines.

    Args:
        cursor: A cursor on the store.

    Returns:
        True when :func:`apply_v17` would add something.
    """
    return schema_fragments.incomplete(cursor, _fragments(), _columns())


def apply_v17(cursor: sqlite3.Cursor, run_script: Callable[[sqlite3.Cursor, str], None]) -> None:
    """
    Bring a v16 schema to v17, inside the caller's transaction.

    Args:
        cursor: Cursor already inside the migration's transaction.
        run_script: The store's statement-by-statement script runner, passed in so this module
            does not import the store.
    """
    schema_fragments.apply(cursor, run_script, _fragments(), _columns())
