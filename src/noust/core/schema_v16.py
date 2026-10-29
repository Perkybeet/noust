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
# account (item 96). ``roles`` holds the six every server has (kind ``seeded`` and ``protected``)
# and the ones the operator defines (``custom``); ``edited`` and ``base`` are how a role that
# follows Noust is told from one the operator has taken over, and what a new release may offer it.
# ``account_roles`` is every role an account holds, in order; ``accounts.role`` cannot say it all
# (its CHECK names the five roles of 3.1 and SQLite cannot lift one without rebuilding the table
# everything references), so it stays the first of the five among them, for what still reads
# one role. The data step that fills both is :mod:`noust.core.access.roles_backfill`.
ROLES_SQL = """
CREATE TABLE IF NOT EXISTS roles (
    name TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('seeded', 'custom', 'protected')),
    label TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    permissions TEXT NOT NULL DEFAULT '[]',
    edited INTEGER NOT NULL DEFAULT 0,
    base TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    created_by TEXT,
    updated_at REAL NOT NULL,
    updated_by TEXT
);
CREATE TABLE IF NOT EXISTS account_roles (
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    created_by TEXT,
    PRIMARY KEY (account_id, role)
);
CREATE INDEX IF NOT EXISTS idx_account_roles_role ON account_roles(role);
"""
ROLES_COLUMNS: list[Column] = []

# Owned by the fleet settings lane: the state a central wants (fleet_desired, on the central) and
# what each node applied of it (fleet_managed, on every node) (item 90).
#
# ``version`` of fleet_desired is the row's own AUTOINCREMENT id: one counter for every setting and
# scope, never reused, so that "the version a node applied" says which row of which scope it was
# even after an exception is added or taken away. fleet_managed holds a fingerprint, never a
# value: a node can say what it applied and whether it still holds it, and a central can learn
# that without anything secret crossing back.
FLEET_SQL = """
CREATE TABLE IF NOT EXISTS fleet_desired (
    version INTEGER PRIMARY KEY AUTOINCREMENT,
    resource TEXT NOT NULL,
    name TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'fleet',
    value TEXT NOT NULL,
    secret_paths TEXT NOT NULL DEFAULT '[]',
    change_id TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    UNIQUE (resource, name, scope)
);

CREATE TABLE IF NOT EXISTS fleet_managed (
    resource TEXT NOT NULL,
    name TEXT NOT NULL,
    central TEXT NOT NULL,
    version INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'managed',
    change_id TEXT NOT NULL,
    applied_by TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    overridden_at TEXT,
    restore_requested_at TEXT,
    PRIMARY KEY (resource, name)
);

CREATE TABLE IF NOT EXISTS fleet_node_settings (
    node TEXT PRIMARY KEY,
    mode TEXT NOT NULL DEFAULT 'managed',
    changed_by TEXT,
    changed_at TEXT,
    resync_requested_at TEXT
);
"""
FLEET_COLUMNS: list[Column] = []

# Owned by the fleet notifications lane (item 90, D3): when each central that manages this server
# last called it, and whether it said it keeps watch, so that a server can tell its central went
# quiet. A server cannot call its central, so the silence is the only sign it has. The routes of a
# channel (domain, tag, severity, event and server) are inside the channel's own value and need no
# table.
NOTIFY_SQL = """
CREATE TABLE IF NOT EXISTS fleet_central_calls (
    central TEXT PRIMARY KEY,
    first_call_at REAL NOT NULL,
    last_call_at REAL NOT NULL,
    heartbeat_s INTEGER
);
"""
NOTIFY_COLUMNS: list[Column] = []

# Owned by the sources lane (item 90, D6): the code host's access a central gives the servers it
# manages. ``source_leases`` is on a node: the installation tokens a central leased to it, each
# scoped to the repositories the node serves and to reading their contents, kept as a secret file
# sealed under the node's own key (``token_name`` names that file; the token is never a column).
# ``expires_at`` is the node's own clock, set from the seconds the central said the token lives
# for, so a node whose clock is off does not trust a token for longer than it lives.
# ``source_lease_grants`` is on the central: what it leased to which node, so that it renews a
# lease before it ends and can say, per node, why one is missing.
# ``source_forwards`` and ``source_follows`` are on the central too: a delivery of the code host
# that it forwards to a node (kept until the node has it, so a node that is down when a push
# arrives gets it when it is back) and the deployment or preview the node started for it, which the
# central follows until it ends to write the commit's status and the preview's comment.
SOURCES_SQL = """
CREATE TABLE IF NOT EXISTS source_leases (
    central TEXT NOT NULL,
    lease_key TEXT NOT NULL,
    host TEXT NOT NULL,
    installation_id INTEGER NOT NULL,
    repos TEXT NOT NULL DEFAULT '[]',
    permissions TEXT NOT NULL DEFAULT '{}',
    token_name TEXT NOT NULL,
    received_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    PRIMARY KEY (central, lease_key)
);

CREATE TABLE IF NOT EXISTS source_lease_grants (
    node TEXT NOT NULL,
    lease_key TEXT NOT NULL,
    host TEXT NOT NULL,
    installation_id INTEGER NOT NULL,
    repos TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL,
    pushed_at REAL,
    expires_at REAL,
    error TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY (node, lease_key)
);

CREATE TABLE IF NOT EXISTS source_forwards (
    id TEXT PRIMARY KEY,
    delivery TEXT NOT NULL,
    node TEXT NOT NULL,
    kind TEXT NOT NULL,
    repo TEXT NOT NULL,
    ref TEXT,
    number INTEGER,
    commit_sha TEXT,
    event TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL,
    error TEXT,
    answer TEXT,
    created_at REAL NOT NULL,
    delivered_at REAL
);
CREATE INDEX IF NOT EXISTS idx_source_forwards_state ON source_forwards(state, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_source_forwards_created ON source_forwards(created_at);

CREATE TABLE IF NOT EXISTS source_follows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    forward_id TEXT NOT NULL,
    node TEXT NOT NULL,
    domain TEXT NOT NULL,
    kind TEXT NOT NULL,
    repo TEXT NOT NULL,
    installation_id INTEGER,
    ref TEXT,
    environment TEXT NOT NULL,
    environment_url TEXT,
    job_id TEXT,
    number INTEGER,
    action TEXT,
    provider_ref INTEGER,
    comment_ref TEXT,
    last_state TEXT,
    state TEXT NOT NULL DEFAULT 'active',
    detail TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_source_follows_state ON source_follows(state, updated_at);
"""
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
