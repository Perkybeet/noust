# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Schema v18 (Noust 4.3): every table and column 4.3 adds to the store.

The rules of :mod:`noust.core.schema_v14`, :mod:`noust.core.schema_v16` and
:mod:`noust.core.schema_v17`: one fragment per area, ``CREATE ... IF NOT EXISTS``, columns added
only when missing and never with a non-constant default, and no row rewritten here.
:func:`apply_v18` is the one definition of v18, run by the climb from v17 and by a fresh store
alike, and :func:`incomplete` lets a store a development build of 4.3 stamped v18 before every
area had its fragment be completed when it opens (`NoustStore._complete_v18`).

Each area is owned by one lane of the 4.3 plan and is the only place that lane writes schema: it
fills its own pair and never touches another's, so merging them cannot conflict. Every pair
starts empty and an empty fragment runs nothing, so a lane whose item turns out to need no table
leaves its pair as it is. The pairs are all listed in :func:`_fragments` and :func:`_columns`
already: a lane fills a string and a list and edits nothing else in this module. A table with a
column that names what an application or a database owns says how a team reaches it
(``OWNED_TABLES`` or ``NOT_OWNED`` in ``tests/test_scope_architecture.py``), and a column that
holds a secret is ruled on in ``core/store_secrets.py``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from noust.core import schema_fragments
from noust.core.schema_fragments import Column

__all__ = ["VERSION", "apply_v18", "incomplete"]

#: The schema version this module defines.
VERSION = 18

# Owned by the states lane: what a first deployment that failed leaves behind for its
# domain (item 141). The job the store keeps carries the domain, the error and how it ended,
# but not what was asked for, and the application's own row is undone on purpose when the first
# deployment fails, so the page of that domain has nothing to retry from. One row per attempt
# (``job_id`` is the job's): ``origin`` says how the application was to start (``source``,
# ``image``, ``compose`` or ``recipe``), ``source`` and ``branch`` what to read again, with
# every credential already masked by ``noust.core.first_deploys``, the only writer. The
# project and the environment are the ones the deployment was queued for, so a team sees the
# failure of its own and no other. ``dismissed_at`` puts an attempt away. Times are ISO 8601.
DEPLOYS_SQL = """
CREATE TABLE IF NOT EXISTS first_deploys (
    job_id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    origin TEXT NOT NULL,
    source TEXT,
    branch TEXT,
    app_type TEXT,
    recipe TEXT,
    project_id INTEGER,
    environment_id INTEGER,
    created_at TEXT NOT NULL,
    dismissed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_first_deploys_domain ON first_deploys(domain);
"""
DEPLOYS_COLUMNS: list[Column] = []

# Owned by the dates lane: when the registration of a domain expires, read from its
# registry and off until the operator turns it on (item 173).
# domain_registrations: one row per registrable domain, what its registry last said
#   (noust.core.domain_registrations): when the registration expires, how the last look ended
#   and when to look again.
# domain_registrables: which registrable domain each name Noust serves was found to belong to,
#   so that the registry is not asked label by label every day.
DOMAINS_SQL = """
CREATE TABLE IF NOT EXISTS domain_registrations (
    domain TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    expires_at TEXT,
    rdap_base TEXT,
    checked_at TEXT NOT NULL,
    next_check_at TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS domain_registrables (
    name TEXT PRIMARY KEY,
    registrable TEXT NOT NULL,
    found_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_domain_registrables_domain ON domain_registrables(registrable);
"""
DOMAINS_COLUMNS: list[Column] = []

# Owned by the functions lane: the tasks that run once and retire themselves
# (item 164).
TASKS_SQL = ""
TASKS_COLUMNS: list[Column] = []

# Owned by the settings lane: what a server follows of its central and which
# servers keep their own, decided on the server's own page and off by default (item 166).
FLEET_SQL = ""
FLEET_COLUMNS: list[Column] = []

# Owned by the verdict lane: what each release measured in the first two hours,
# in windows of a minute, and the verdict against the one before (item 176).
RELEASES_SQL = ""
RELEASES_COLUMNS: list[Column] = []

# Owned by the sleep lane: which applications sleep when nobody visits them, and the
# state of each (item 177).
SLEEP_SQL = ""
SLEEP_COLUMNS: list[Column] = []

# Owned by the copies lanes. Two of them write here, each its own block, and neither touches the
# other's: the central that collects the copies of its servers (item 195), and what a backup
# destination was found able to do with a file it was given - delete it, overwrite it - and the
# space its retention could not free (item 180, the block that follows).
#
# Item 195, on a server: what each central that collects its copies said it holds
# (``backup_central_held``, replaced as a whole whenever that central says it, by
# ``noust.core.backup_collection_store``) and when it last said it (``backup_central_syncs``),
# which is what a retention that waits for a copy nobody has taken yet reads, and what says a copy
# is somewhere else too.
#
# Item 195, on a central (``noust.fleet.collect``): ``collected_backups``, one row per copy it holds
# of a server (``node``, ``kind`` and the server's own identifier of it), with the files it keeps
# for it and what it checked of them (``state`` is ``held``, or ``retired`` once the central's own
# retention or its operator let it go, so that a server that still has it is not asked for it again);
# ``collect_nodes``, one row per server that has anything to say
# about its collecting (whether it follows, how the last pass went, the catalogue's fingerprint
# then); ``collect_settings``, the knobs of the whole fleet's collecting, one row each.
BACKUPS_SQL = """
CREATE TABLE IF NOT EXISTS backup_central_held (
    central TEXT NOT NULL,
    kind TEXT NOT NULL,
    item_id TEXT NOT NULL,
    sha256 TEXT,
    held_at TEXT NOT NULL,
    PRIMARY KEY (central, kind, item_id)
);
CREATE TABLE IF NOT EXISTS backup_central_syncs (
    central TEXT PRIMARY KEY,
    synced_at TEXT NOT NULL,
    held INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS collected_backups (
    node TEXT NOT NULL,
    kind TEXT NOT NULL,
    item_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    folder TEXT NOT NULL,
    origin TEXT NOT NULL,
    created_at TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    size INTEGER NOT NULL,
    files TEXT NOT NULL,
    verified TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'held',
    PRIMARY KEY (node, kind, item_id)
);
CREATE INDEX IF NOT EXISTS idx_collected_backups_owner ON collected_backups(node, kind, owner);
CREATE TABLE IF NOT EXISTS collect_nodes (
    node TEXT PRIMARY KEY,
    follow INTEGER,
    last_pass_at TEXT,
    last_ok_at TEXT,
    last_error TEXT,
    last_error_code TEXT,
    failures INTEGER NOT NULL DEFAULT 0,
    fingerprint TEXT,
    support TEXT
);
CREATE TABLE IF NOT EXISTS collect_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""
BACKUPS_COLUMNS: list[Column] = []

# Owned by the clients lane, first of its two areas: the maintenance window of an
# application, with the end it cannot be without (item 181).
MAINTENANCE_SQL = ""
MAINTENANCE_COLUMNS: list[Column] = []

# Owned by the clients lane, second of its two areas: the monthly record of a project,
# kept apart because the deployments it summarises are purged to the last 20 (item 182).
REPORTS_SQL = ""
REPORTS_COLUMNS: list[Column] = []

# Owned by the policy lane: what each credential that is not a person may do, in which
# environment, and what it did (items 184 and 185).
AGENTS_SQL = ""
AGENTS_COLUMNS: list[Column] = []

# Owned by the assistant's base lane: the optional assistant's settings, its provider and
# the limits it runs under (item 171).
ASSISTANT_SQL = ""
ASSISTANT_COLUMNS: list[Column] = []


def _fragments() -> list[str]:
    return [
        DEPLOYS_SQL,
        DOMAINS_SQL,
        TASKS_SQL,
        FLEET_SQL,
        RELEASES_SQL,
        SLEEP_SQL,
        BACKUPS_SQL,
        MAINTENANCE_SQL,
        REPORTS_SQL,
        AGENTS_SQL,
        ASSISTANT_SQL,
    ]


def _columns() -> list[Column]:
    return [
        *DEPLOYS_COLUMNS,
        *DOMAINS_COLUMNS,
        *TASKS_COLUMNS,
        *FLEET_COLUMNS,
        *RELEASES_COLUMNS,
        *SLEEP_COLUMNS,
        *BACKUPS_COLUMNS,
        *MAINTENANCE_COLUMNS,
        *REPORTS_COLUMNS,
        *AGENTS_COLUMNS,
        *ASSISTANT_COLUMNS,
    ]


def incomplete(cursor: sqlite3.Cursor) -> bool:
    """
    Whether a store lacks any table, index or column v18 defines.

    Args:
        cursor: A cursor on the store.

    Returns:
        True when :func:`apply_v18` would add something.
    """
    return schema_fragments.incomplete(cursor, _fragments(), _columns())


def apply_v18(cursor: sqlite3.Cursor, run_script: Callable[[sqlite3.Cursor, str], None]) -> None:
    """
    Bring a v17 schema to v18, inside the caller's transaction.

    Args:
        cursor: Cursor already inside the migration's transaction.
        run_script: The store's statement-by-statement script runner, passed in so this module
            does not import the store.
    """
    schema_fragments.apply(cursor, run_script, _fragments(), _columns())
