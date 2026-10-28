# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Schema v16 (Noust 4.1): every table and column 4.1 adds to the store.

The rules of :mod:`noust.core.schema_v14`: one fragment per area, ``CREATE ... IF NOT EXISTS``,
columns added only when missing and never with a non-constant default, and no row rewritten here.
:func:`apply_v16` is the one definition of v16, run by the climb from v15 and by a fresh store
alike, and :func:`incomplete` lets a store a development build of 4.1 stamped v16 before every
area had its fragment be completed when it opens (`NoustStore._complete_v16`).

Each area is owned by one lane of the 4.1 plan and is the only place that lane writes schema: it
fills its own pair and never touches another's, so merging them cannot conflict. A table with a
column that names what an application or a database owns says how a team reaches it
(``OWNED_TABLES`` or ``NOT_OWNED`` in ``tests/test_scope_architecture.py``).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from noust.core import schema_fragments
from noust.core.schema_fragments import Column

__all__ = ["VERSION", "apply_v16", "incomplete"]

#: The schema version this module defines.
VERSION = 16

# Owned by the roles lane: one editable list of roles, the protected owner, several roles per
# account (item 96).
ROLES_SQL = ""
ROLES_COLUMNS: list[Column] = []

# Owned by the fleet settings lane: the state a central wants (fleet_desired, on the central) and
# what each node applied of it (fleet_managed, on every node) (item 90).
FLEET_SQL = ""
FLEET_COLUMNS: list[Column] = []

# Owned by the fleet notifications lane: routes by domain, tag, severity, event and server.
NOTIFY_SQL = ""
NOTIFY_COLUMNS: list[Column] = []

# Owned by the sources lane: the installation tokens a central leases to a node.
SOURCES_SQL = ""
SOURCES_COLUMNS: list[Column] = []

# Owned by the engines lane: the default database server of each engine on a server (item 103).
ENGINES_SQL = ""
ENGINES_COLUMNS: list[Column] = []

# Owned by the DNS lane: the zones and records of Noust DNS (item 104).
DNS_SQL = ""
DNS_COLUMNS: list[Column] = []


def _fragments() -> list[str]:
    return [ROLES_SQL, FLEET_SQL, NOTIFY_SQL, SOURCES_SQL, ENGINES_SQL, DNS_SQL]


def _columns() -> list[Column]:
    return [
        *ROLES_COLUMNS,
        *FLEET_COLUMNS,
        *NOTIFY_COLUMNS,
        *SOURCES_COLUMNS,
        *ENGINES_COLUMNS,
        *DNS_COLUMNS,
    ]


def incomplete(cursor: sqlite3.Cursor) -> bool:
    """
    Whether a store lacks any table, index or column v16 defines.

    Args:
        cursor: A cursor on the store.

    Returns:
        True when :func:`apply_v16` would add something.
    """
    return schema_fragments.incomplete(cursor, _fragments(), _columns())


def apply_v16(cursor: sqlite3.Cursor, run_script: Callable[[sqlite3.Cursor, str], None]) -> None:
    """
    Bring a v15 schema to v16, inside the caller's transaction.

    Args:
        cursor: Cursor already inside the migration's transaction.
        run_script: The store's statement-by-statement script runner, passed in so this module
            does not import the store.
    """
    schema_fragments.apply(cursor, run_script, _fragments(), _columns())
