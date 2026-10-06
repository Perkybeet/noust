"""
Schema v14 (Noust 3.4): every table and column 3.4 adds to the store.

The same rules as :mod:`noust.core.schema_v13`: one fragment per area,
``CREATE ... IF NOT EXISTS``, columns added only when missing and never with a
non-constant default, and no row rewritten here. :func:`apply_v14` is the one
definition of v14, run by the migration from v13 and by a fresh store alike.

Each area below is owned by one lane of the 3.4 plan and is the only place
that lane writes schema: lanes fill their own pair (``<AREA>_SQL`` and
``<AREA>_COLUMNS``) and never touch another's, so merging them cannot conflict.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable

#: The schema version this module defines.
VERSION = 14

# Owned by the teams lane: teams, their members, projects and environments.
TEAMS_SQL = ""
TEAMS_COLUMNS: list[tuple[str, str, str]] = []

# Owned by the build lane: the native build block of an application
# (noust.core.app_build_store is its only reader and writer). One row per
# application: ``document`` is the operator's ``build:`` block, as the
# validated YAML Noust wrote (no row, or none, means the repository's own
# applies); ``applied`` is what the application's unit was last written from
# (its start command, base directory and runtime), which is how an update
# knows the unit is stale. A table of its own, not columns of ``apps``, so a
# deploy's full-row write of ``apps`` cannot put back what an operator changed
# while it ran.
BUILD_SQL = """
CREATE TABLE IF NOT EXISTS app_build (
    app_id INTEGER PRIMARY KEY REFERENCES apps(id) ON DELETE CASCADE,
    document TEXT,
    applied TEXT,
    updated_by TEXT,
    updated_at TEXT NOT NULL
);
"""
BUILD_COLUMNS: list[tuple[str, str, str]] = []

# Owned by the container lane: what a container application is built from and
# how it runs. One row per application, in a table of its own so that the
# full-row write of a redeploy (``apps`` is rewritten whole) cannot touch it,
# and removed with the application. Nothing secret is stored here: a registry
# account's password and a build secret live in the secret store, and the row
# holds the name they are kept under.
#
# method: how the image comes to exist (dockerfile, railpack, image).
# image: the registry reference (image method only).
# dockerfile, context, target: the Dockerfile, the build context directory and
#   the stage, relative to the repository.
# build_args: JSON object of non-secret build arguments.
# build_secrets: JSON list of the ids of the secrets a build mounts.
# container_port: where the process listens inside the container.
# volumes: JSON list of container paths kept in named volumes.
# registry_*: the account the image (or a private base image) is pulled with.
# digest: the digest the last deployment of a registry image resolved.
CONTAINER_SQL = """
CREATE TABLE IF NOT EXISTS app_container (
    app_id INTEGER PRIMARY KEY REFERENCES apps(id) ON DELETE CASCADE,
    method TEXT NOT NULL DEFAULT 'dockerfile'
        CHECK (method IN ('dockerfile', 'railpack', 'image')),
    image TEXT,
    dockerfile TEXT,
    context TEXT NOT NULL DEFAULT '.',
    target TEXT,
    build_args TEXT,
    build_secrets TEXT,
    container_port INTEGER,
    volumes TEXT,
    restart_policy TEXT NOT NULL DEFAULT 'unless-stopped',
    registry_server TEXT,
    registry_username TEXT,
    registry_secret TEXT,
    digest TEXT,
    updated_by TEXT,
    updated_at TEXT NOT NULL
);
"""
CONTAINER_COLUMNS: list[tuple[str, str, str]] = []

# Owned by the recipes lane: which recipe an application was installed from,
# and at which version. One row per application, in a table of its own so a
# redeploy's full-row write of ``apps`` never touches it; the row goes with the
# application. Written only through ``noust.recipes.installed``.
RECIPES_SQL = """
CREATE TABLE IF NOT EXISTS app_recipes (
    app_id INTEGER PRIMARY KEY REFERENCES apps(id) ON DELETE CASCADE,
    recipe TEXT NOT NULL,
    kind TEXT NOT NULL,
    version TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    installed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_app_recipes_recipe ON app_recipes(recipe);
"""
RECIPES_COLUMNS: list[tuple[str, str, str]] = []

# Owned by the delivery lane: the deploy queue, cancellation and zero downtime.
#
# A request to cancel what runs on an application. The operation that holds the
# application's lock is the one it is addressed to, by the lock holder's pid and
# start time: a request outlives neither that operation nor its process, so it
# can never cancel the next deployment. It is a table of its own, not a column,
# because the process that asks (the CLI, the console) is not the one that runs
# the deployment, and ``outcome`` is how the second tells the first what came
# of it (``cancelled``, ``too_late``, ``finished`` or ``unsupported``).
DELIVERY_SQL = """
CREATE TABLE IF NOT EXISTS delivery_cancel_requests (
    domain TEXT PRIMARY KEY,
    holder_pid INTEGER NOT NULL,
    holder_started_at TEXT NOT NULL,
    requested_by TEXT,
    requested_at TEXT NOT NULL,
    outcome TEXT,
    decided_at TEXT
);
"""
# The deployments status column has a CHECK SQLite cannot alter (see
# schema_v13), so a cancelled deployment is a failed row that says when and by
# whom it was cancelled, not a new status.
DELIVERY_COLUMNS: list[tuple[str, str, str]] = [
    ("deployments", "cancelled_at", "TEXT"),
    ("deployments", "cancelled_by", "TEXT"),
]

# Owned by the sources lane: per-application deploy keys and previews.
#
# deploy_keys: one SSH key pair per row. The private half is a secret file
# (``deploy-keys/<name>/key``), never a column; the row keeps what is safe to
# list: the public key, its fingerprint, where it came from and, while a
# rotation waits for the code host to accept the new key, the new public half.
# deploy_key_repositories: which repository each key reaches. A repository has
# one key and a key may serve several repositories (a machine user's does). The
# key is found by the repository's address, because a clone, a fetch and a
# webhook all know that and none of them knows the application.
SOURCES_SQL = """
CREATE TABLE IF NOT EXISTS deploy_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    origin TEXT NOT NULL CHECK (origin IN ('generated', 'imported')),
    key_type TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    public_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT,
    rotated_at TEXT,
    last_used_at TEXT,
    pending_key_type TEXT,
    pending_fingerprint TEXT,
    pending_public_key TEXT,
    pending_since TEXT
);
CREATE TABLE IF NOT EXISTS deploy_key_repositories (
    repository TEXT PRIMARY KEY,
    key_id INTEGER NOT NULL REFERENCES deploy_keys(id) ON DELETE CASCADE,
    assigned_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deploy_key_repositories_key ON deploy_key_repositories(key_id);
"""
SOURCES_COLUMNS: list[tuple[str, str, str]] = []

# Owned by the databases lane: database exposure and the choice of engine.
#
# database_exposures: a database deliberately reachable from the network. The
# row is written before anything is changed (``state`` says how far the
# opening or the closing got), so an interrupted action is finished or undone
# from what it records instead of being guessed at.
DATABASES_SQL = """
CREATE TABLE IF NOT EXISTS database_exposures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    engine TEXT NOT NULL,
    db_name TEXT NOT NULL,
    port INTEGER NOT NULL UNIQUE,
    bind_address TEXT NOT NULL,
    sources TEXT NOT NULL,
    tls TEXT NOT NULL CHECK (tls IN ('required', 'optional')),
    remote_user TEXT NOT NULL,
    profile TEXT NOT NULL,
    unit TEXT NOT NULL,
    firewall TEXT,
    state TEXT NOT NULL CHECK (state IN ('opening', 'exposed', 'closing')),
    created_at TEXT NOT NULL,
    created_by TEXT,
    UNIQUE (engine, db_name)
);
"""
DATABASES_COLUMNS: list[tuple[str, str, str]] = []

# Owned by the backups lane: volume backups and the backup of Noust itself.
#
# volume_backup_policies: what an application's data directories and Docker
#   volumes are backed up with (one row per application, keyed by domain like
#   backup_schedules): the targets, the calendar, retention, the destinations
#   and how consistent the copy must be. The archives themselves are files
#   with a signed sidecar, as application backups are.
# self_backup_schedule: the one schedule of Noust's own state bundle (the
#   singleton row has id 1): calendar, retention, destinations, last outcome.
# database_backup_policies.scope / exclusions: a policy that covers every
#   database of an engine, and what it leaves out (databases, schemas, tables
#   or collections), as a JSON object.
BACKUPS_SQL = """
CREATE TABLE IF NOT EXISTS volume_backup_policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_domain TEXT NOT NULL UNIQUE,
    targets TEXT NOT NULL DEFAULT '[]',
    extra_paths TEXT NOT NULL DEFAULT '[]',
    schedule TEXT NOT NULL,
    retention_count INTEGER,
    retention_days INTEGER,
    destinations TEXT NOT NULL DEFAULT '[]',
    consistency TEXT NOT NULL DEFAULT 'live',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,
    last_status TEXT,
    last_error TEXT,
    last_backup TEXT,
    last_success_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS self_backup_schedule (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    schedule TEXT NOT NULL,
    retention_count INTEGER,
    retention_days INTEGER,
    destinations TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,
    last_status TEXT,
    last_error TEXT,
    last_file TEXT,
    last_success_at TEXT,
    last_verified_at TEXT,
    last_verify_status TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""
BACKUPS_COLUMNS: list[tuple[str, str, str]] = [
    ("database_backup_policies", "scope", "TEXT NOT NULL DEFAULT 'database'"),
    ("database_backup_policies", "exclusions", "TEXT NOT NULL DEFAULT '{}'"),
]

# Owned by the access lane: the console's exposure and guided enrollment.
#
# console_exposure holds at most one row (id = 1): the public name the console
# was given with `noust console expose`, and exactly what that call created or
# changed, so `unexpose` removes those and nothing else. Every "previous_*"
# column is what the setting held before, JSON text, NULL when it was unset.
# site_created / certificate_created / proxy_added say whether Noust made that
# part (a name that already had a site or a certificate is never removed).
ACCESS_SQL = """
CREATE TABLE IF NOT EXISTS console_exposure (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    domain TEXT NOT NULL,
    port INTEGER NOT NULL,
    scheme TEXT NOT NULL DEFAULT 'http',
    allow_ips TEXT NOT NULL DEFAULT '[]',
    site_created INTEGER NOT NULL DEFAULT 0,
    certificate_created INTEGER NOT NULL DEFAULT 0,
    proxy_added TEXT,
    previous_public_url TEXT,
    previous_allowed_hosts TEXT,
    state TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    created_by TEXT
);
"""
ACCESS_COLUMNS: list[tuple[str, str, str]] = []

# Owned by the notifications lane: notification channels.
NOTIFY_SQL = ""
NOTIFY_COLUMNS: list[tuple[str, str, str]] = []


def _fragments() -> list[str]:
    return [
        TEAMS_SQL,
        BUILD_SQL,
        CONTAINER_SQL,
        RECIPES_SQL,
        DELIVERY_SQL,
        SOURCES_SQL,
        DATABASES_SQL,
        BACKUPS_SQL,
        ACCESS_SQL,
        NOTIFY_SQL,
    ]


def _columns() -> list[tuple[str, str, str]]:
    return [
        *TEAMS_COLUMNS,
        *BUILD_COLUMNS,
        *CONTAINER_COLUMNS,
        *RECIPES_COLUMNS,
        *DELIVERY_COLUMNS,
        *SOURCES_COLUMNS,
        *DATABASES_COLUMNS,
        *BACKUPS_COLUMNS,
        *ACCESS_COLUMNS,
        *NOTIFY_COLUMNS,
    ]


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
    Whether a store lacks any table, index or column v14 defines.

    Only reads, so a complete store is checked without a write lock.

    Args:
        cursor: A cursor on the store.

    Returns:
        True when :func:`apply_v14` would add something.
    """
    cursor.execute("SELECT type, name FROM sqlite_master WHERE type IN ('table', 'index')")
    present = {(str(kind), str(name)) for kind, name in cursor.fetchall()}
    for fragment in _fragments():
        for kind, name in _CREATED.findall(fragment):
            if (kind.lower(), name) not in present:
                return True
    return any(column not in _existing_columns(cursor, table) for table, column, _ in _columns())


def apply_v14(cursor: sqlite3.Cursor, run_script: Callable[[sqlite3.Cursor, str], None]) -> None:
    """
    Bring a v13 schema to v14, inside the caller's transaction.

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
