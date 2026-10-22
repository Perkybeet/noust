# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Schema v14 (Noust 4.0): every table and column 4.0 adds to the store.

The same rules as :mod:`noust.core.schema_v13`: one fragment per area,
``CREATE ... IF NOT EXISTS``, columns added only when missing and never with a
non-constant default, and no row rewritten here. :func:`apply_v14` is the one
definition of v14, run by the migration from v13 and by a fresh store alike.

Each area below is owned by one lane of the 4.0 plan and is the only place
that lane writes schema: lanes fill their own pair (``<AREA>_SQL`` and
``<AREA>_COLUMNS``) and never touch another's, so merging them cannot conflict.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable

#: The schema version this module defines.
VERSION = 14

# Owned by the teams lane: teams, their members, projects and environments, and
# the project and environment every application, site, database and backup
# schedule belongs to (noust.core.teams).
#
# Ownership is nullable in SQL on purpose: SQLite cannot add a NOT NULL column
# without a constant default, and a constant would name a project that may not
# exist. The store fills it for every new row and noust.core.teams.backfill
# fills it for every old one, so a NULL is a row nobody has seen yet, which a
# team-scoped reader does not see either (it fails closed).
#
# The columns reference with RESTRICT: a project or an environment that still
# holds something cannot be deleted by any writer, whatever the manager above
# it checked. The triggers that say the same about an environment and its
# project (a row cannot sit in an environment of another project) are
# noust.core.teams.guards, installed by the data step: a trigger body holds
# semicolons, which a fragment is not allowed to (it is run statement by statement).
TEAMS_SQL = """
CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    created_by TEXT
);

CREATE TABLE IF NOT EXISTS team_members (
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'operator', 'viewer')),
    created_at REAL NOT NULL,
    created_by TEXT,
    PRIMARY KEY (team_id, account_id)
);

CREATE INDEX IF NOT EXISTS idx_team_members_account ON team_members(account_id);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    created_by TEXT,
    UNIQUE (team_id, slug)
);

CREATE TABLE IF NOT EXISTS environments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    created_by TEXT,
    UNIQUE (project_id, slug)
);

CREATE TABLE IF NOT EXISTS account_active_team (
    account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE
);
"""
TEAMS_COLUMNS: list[tuple[str, str, str]] = [
    (table, column, definition)
    for table in ("apps", "sites", "databases", "backup_schedules")
    for column, definition in (
        ("project_id", "INTEGER REFERENCES projects(id) ON DELETE RESTRICT"),
        ("environment_id", "INTEGER REFERENCES environments(id) ON DELETE RESTRICT"),
    )
]

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
# entrypoint, command: JSON lists of words that replace the image's ENTRYPOINT and CMD; NULL
#   leaves the image's own, an empty list clears it (added in 4.0, as columns).
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
CONTAINER_COLUMNS: list[tuple[str, str, str]] = [
    ("app_container", "entrypoint", "TEXT"),
    ("app_container", "command", "TEXT"),
]

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
SOURCES_SQL += """
-- app_source: how an application's repository is fetched and when a push deploys it
-- (noust.core.app_source_store is its only reader and writer). One row per application, in
-- a table of its own so a redeploy's full-row write of ``apps`` cannot put back what an
-- operator changed meanwhile. No row means the defaults. submodules: initialise and export
-- the submodules. lfs: auto (when the repository tracks files with Git LFS), on, or off.
-- shallow: fetch only the latest commit. watch_paths: JSON list of globs a push must touch.
-- skip_markers: honour [skip ci] / [skip cd] in commit messages.
CREATE TABLE IF NOT EXISTS app_source (
    app_id INTEGER PRIMARY KEY REFERENCES apps(id) ON DELETE CASCADE,
    submodules INTEGER NOT NULL DEFAULT 1,
    lfs TEXT NOT NULL DEFAULT 'auto' CHECK (lfs IN ('auto', 'on', 'off')),
    shallow INTEGER NOT NULL DEFAULT 1,
    watch_paths TEXT NOT NULL DEFAULT '[]',
    skip_markers INTEGER NOT NULL DEFAULT 1,
    updated_by TEXT,
    updated_at TEXT NOT NULL
);
"""
SOURCES_SQL += """
-- registry_logins: the accounts Noust logs in to a container registry with (Docker Hub,
-- GHCR, GitLab, ECR, ACR, Artifact Registry or any other), kept apart from the applications that
-- use them. noust.core.registry_store is its only reader and writer. The password is never
-- a column: it is the secret named by secret_name. kind says how a login is made (ecr
-- trades an access key for a token that expires). last_status is ok or failed after the
-- last test.
CREATE TABLE IF NOT EXISTS registry_logins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('dockerhub', 'ghcr', 'gitlab', 'ecr', 'acr', 'gar', 'custom')),
    server TEXT NOT NULL,
    username TEXT NOT NULL,
    secret_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT NOT NULL,
    last_tested_at TEXT,
    last_status TEXT,
    last_error TEXT
);
-- app_registry_logins: which logins an application pulls and builds with. Both sides cascade.
CREATE TABLE IF NOT EXISTS app_registry_logins (
    app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    login_id INTEGER NOT NULL REFERENCES registry_logins(id) ON DELETE CASCADE,
    assigned_at TEXT NOT NULL,
    assigned_by TEXT,
    PRIMARY KEY (app_id, login_id)
);
"""
SOURCES_SQL += """
-- app_compose: which compose file, profiles and extra arguments a Compose stack is run with
-- (noust.core.app_compose_store is its only reader and writer). One row per stack, in a table
-- of its own so a redeploy's full-row write of apps cannot put back what an operator changed.
-- The lists are JSON. No row means the stack's unit records the file and the defaults apply.
CREATE TABLE IF NOT EXISTS app_compose (
    app_id INTEGER PRIMARY KEY REFERENCES apps(id) ON DELETE CASCADE,
    compose_file TEXT,
    profiles TEXT NOT NULL DEFAULT '[]',
    build_args TEXT NOT NULL DEFAULT '[]',
    up_args TEXT NOT NULL DEFAULT '[]',
    services TEXT NOT NULL DEFAULT '[]',
    updated_by TEXT,
    updated_at TEXT NOT NULL
);
"""
SOURCES_SQL += """
-- gitlab_connections: the GitLab accounts Noust lists projects and clones private ones with, one
-- per GitLab user and host (noust.core.gitlab_store is its only reader and writer). The
-- credentials are never a column: they are the secret named by secret_name. kind says how the
-- connection signs in (a pasted access token, or the tokens an OAuth sign-in handed back).
-- last_status is ok or failed after the last time GitLab was asked who it is.
CREATE TABLE IF NOT EXISTS gitlab_connections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    host TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('token', 'oauth')),
    username TEXT,
    secret_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT NOT NULL,
    last_checked_at TEXT,
    last_status TEXT,
    last_error TEXT
);
-- gitlab_oauth_apps: the application an operator registered in GitLab so that people can connect
-- without pasting a token. One per host, and the client secret is the secret named by secret_name.
CREATE TABLE IF NOT EXISTS gitlab_oauth_apps (
    host TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    secret_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT
);
"""
SOURCES_SQL += """
-- push_repos: the applications whose code arrives with git push (noust.core.push_store is its
-- only reader and writer). One row per application: the branch a push deploys, and the source the
-- application had before, so that turning push off puts it back. The keys of who may push are not
-- here: they are lines of the push account's key file, each forced to the application's own
-- repository, because sshd reads that file and nothing else decides who gets in.
CREATE TABLE IF NOT EXISTS push_repos (
    app_id INTEGER PRIMARY KEY REFERENCES apps(id) ON DELETE CASCADE,
    branch TEXT NOT NULL,
    previous_source TEXT,
    previous_branch TEXT,
    enabled_at TEXT NOT NULL,
    enabled_by TEXT
);
"""
SOURCES_SQL += """
-- app_packages: the system packages an application needs (noust.core.app_packages_store is its
-- only reader and writer). One row per application, the names as a JSON list: only the operator
-- writes them, because Noust installs them as root before the next build.
CREATE TABLE IF NOT EXISTS app_packages (
    app_id INTEGER PRIMARY KEY REFERENCES apps(id) ON DELETE CASCADE,
    packages TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);
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
DATABASES_SQL += """
-- db_host_tls: the certificate a host engine serves TLS with (noust.managers.database.host_tls
-- is its only reader and writer). One row per engine: the name it is reached by, which
-- certificate Noust copies from and the fingerprint of the copy the engine serves, so a renewal
-- is noticed by comparing it with the source. Never a key: the key stays in the certificate's
-- own directory and in the copy only the engine's account reads.
CREATE TABLE IF NOT EXISTS db_host_tls (
    engine TEXT PRIMARY KEY,
    hostname TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('acme', 'custom', 'lineage')),
    certificate TEXT,
    installed_dir TEXT NOT NULL,
    fingerprint TEXT,
    not_after TEXT,
    ssl_was TEXT,
    last_refresh_at TEXT,
    last_status TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- db_replication: the replicas of a host engine, from both ends
-- (noust.managers.database.replication_records is its only reader and writer). 'primary' rows
-- are a replica this server made room for: the account, the slot, the address allowed to stream,
-- what the firewall was told and the setting changed so it could connect. A 'replica' row is
-- this server following a primary: where, the passfile it connects with and the data directory
-- that was set aside. Never a password: the primary's lives in the secret store and the
-- replica's in its passfile.
CREATE TABLE IF NOT EXISTS db_replication (
    engine TEXT NOT NULL DEFAULT 'postgresql',
    side TEXT NOT NULL CHECK (side IN ('primary', 'replica')),
    name TEXT NOT NULL,
    account TEXT NOT NULL,
    slot TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    source TEXT,
    listen_was TEXT,
    firewall TEXT NOT NULL DEFAULT '{}',
    passfile TEXT,
    saved_as TEXT,
    state TEXT NOT NULL DEFAULT 'prepared',
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (engine, side, name)
);
"""
DATABASES_COLUMNS: list[tuple[str, str, str]] = [
    # How a client verifies the engine a link signs in to: require, verify-ca or
    # verify-full. Null leaves the engine's own default (noust.managers.database.tls_links).
    ("database_links", "tls_mode", "TEXT"),
]

# Owned by the database-instances lane: the database engines Noust runs itself,
# as containers, beside the host's (noust.core.db_instance_store is the only
# reader and writer).
#
# db_instances: one row per instance, written *before* the first thing is
# created (``state`` says how far it got), so an interrupted creation, upgrade
# or removal is finished or undone from what the row records. The image is kept
# as the repository, the tag Noust offered (``version``, a major such as ``17``
# or ``8.4``) and the digest the container really runs; ``previous_digest`` is
# what ran before the last minor upgrade, which is what a failed one is put
# back to. Nothing secret is here: the superuser's password is in the
# instance's own 0600 env file, where the container reads it from.
#
# The unit, the volume, the container and the directory are not columns: each
# is derived from ``name`` in one place (noust.managers.database.instance_layout),
# so a row can never name a unit it was not made with.
#
# db_instance_migrations: a move of an engine's databases to a new instance of a
# newer major version (noust.core.db_migration_store). The row is written before
# the first database moves and says how far it got, so a failed or interrupted
# move is undone, and a finished one rolled back, from what is written. ``moved``
# is a JSON list of {"database", "apps"}: what went across and which applications
# were pointed at the new instance. No secret is here.
DBINSTANCES_SQL = """
CREATE TABLE IF NOT EXISTS db_instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    flavour TEXT NOT NULL,
    version TEXT NOT NULL,
    image TEXT NOT NULL,
    digest TEXT NOT NULL,
    previous_digest TEXT,
    host_port INTEGER NOT NULL UNIQUE,
    memory_limit_mb INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('creating', 'ready', 'upgrading', 'removing')),
    created_at TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT NOT NULL,
    upgraded_at TEXT
);

CREATE TABLE IF NOT EXISTS db_instance_migrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT NOT NULL,
    target_name TEXT NOT NULL,
    flavour TEXT NOT NULL,
    from_version TEXT,
    to_version TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('moving', 'done', 'rolling_back', 'rolled_back', 'failed')
    ),
    moved TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    created_by TEXT,
    finished_at TEXT
);
"""
DBINSTANCES_COLUMNS: list[tuple[str, str, str]] = [
    # Whether the engine serves TLS (off, optional, required): the Compose file is
    # rendered from the row, so the mode is a column and not a file's content.
    ("db_instances", "tls_mode", "TEXT NOT NULL DEFAULT 'off'"),
    # What the Compose file is rendered from besides the memory: the CPUs and the swap the
    # container may use (NULL: Docker's own default) and the health probe's thresholds
    # (NULL: Noust's defaults), so a change is a validated setter and not an edited file.
    ("db_instances", "cpu_limit", "REAL"),
    ("db_instances", "swap_limit_mb", "INTEGER"),
    ("db_instances", "probe_interval_s", "INTEGER"),
    ("db_instances", "probe_timeout_s", "INTEGER"),
    ("db_instances", "probe_retries", "INTEGER"),
    # The image variant (pgvector, postgis) and the tag the registry serves its digest as:
    # NULL for an engine's own image, whose tag is its version.
    ("db_instances", "variant", "TEXT"),
    ("db_instances", "image_tag", "TEXT"),
    # The names of the SQL scripts the engine ran the first time it started (a JSON list):
    # the Compose file mounts their directory only when there are some.
    ("db_instances", "init_scripts", "TEXT"),
    # What the operator chose for the container beyond the catalog (a JSON list each, NULL for
    # none): a command that replaces the engine's whole one, or arguments appended to it. The image
    # an operator pins is the existing image, digest and image_tag, with variant 'custom'.
    ("db_instances", "custom_command", "TEXT"),
    ("db_instances", "custom_args", "TEXT"),
]

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
# restore_drills: one row per rehearsal of restoring an application's newest
#   backup (noust.managers.restore_drill): what it found, step by step, as JSON.
# app_update_policy: whether an application's update is refused when its backup
#   fails (noust.core.update_policy_store). No row means the default: required
#   for an application with a database.
# backup_timer_options: the time limit and the time zone of a backup timer, one row per unit
#   (noust.managers.backup_timer_options). Apart from the policies on purpose: a policy is written
#   whole by whoever saves it, and a limit set on a timer must not be put back by that write.
# db_pitr: the continuous archiving of an engine's write-ahead log, which a point-in-time
#   recovery is made from (noust.managers.database.pitr_records): one row per engine, with
#   the cluster it archives (its system identifier), where the archive is, how often a base
#   backup is taken, what is kept and where it is sent, and how the last run ended.
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
CREATE TABLE IF NOT EXISTS restore_drills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_domain TEXT NOT NULL,
    backup_id TEXT,
    trigger TEXT NOT NULL,
    ok INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    report TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_restore_drills_app ON restore_drills(app_domain, id DESC);
CREATE TABLE IF NOT EXISTS app_update_policy (
    app_id INTEGER PRIMARY KEY REFERENCES apps(id) ON DELETE CASCADE,
    backup_required INTEGER NOT NULL CHECK (backup_required IN (0, 1)),
    updated_by TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backup_timer_options (
    unit TEXT PRIMARY KEY,
    max_runtime_seconds INTEGER,
    timezone TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backup_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    destination TEXT,
    output TEXT NOT NULL,
    truncated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_backup_failures_subject ON backup_failures(subject, id DESC);
CREATE TABLE IF NOT EXISTS db_pitr (
    engine TEXT PRIMARY KEY,
    system_id TEXT NOT NULL,
    archive_path TEXT NOT NULL,
    base_every_hours INTEGER NOT NULL,
    retention_count INTEGER,
    retention_days INTEGER,
    destinations TEXT NOT NULL DEFAULT '[]',
    wal_level_was TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,
    last_status TEXT,
    last_error TEXT,
    last_base_at TEXT,
    last_sync_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""
BACKUPS_COLUMNS: list[tuple[str, str, str]] = [
    ("database_backup_policies", "scope", "TEXT NOT NULL DEFAULT 'database'"),
    ("database_backup_policies", "exclusions", "TEXT NOT NULL DEFAULT '{}'"),
    # The most the scheduled dumps kept here may weigh together (retention by size).
    ("database_backup_policies", "retention_bytes", "INTEGER"),
    # Keep no durable local copy: once a scheduled dump is on every destination the policy
    # names, the local file goes (a server with a small disk). 0 or 1.
    ("database_backup_policies", "remote_only", "INTEGER NOT NULL DEFAULT 0"),
    # The most the scheduled backups kept here may weigh together (retention by size), for
    # Noust's own state, an application's data and an application.
    ("self_backup_schedule", "retention_bytes", "INTEGER"),
    ("volume_backup_policies", "retention_bytes", "INTEGER"),
    ("backup_schedules", "retention_bytes", "INTEGER"),
    # Keep no durable local copy of a scheduled backup once every destination holds it and its
    # upload was checked: an application's, its data's and Noust's own state. 0 or 1.
    ("backup_schedules", "remote_only", "INTEGER NOT NULL DEFAULT 0"),
    ("volume_backup_policies", "remote_only", "INTEGER NOT NULL DEFAULT 0"),
    ("self_backup_schedule", "remote_only", "INTEGER NOT NULL DEFAULT 0"),
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

# Owned by the networking lane: what a site answers with, how it is trusted, and
# the DNS, tunnel and port exposure that reach it (noust.core.site_policy_store,
# certificate_store, dns_store, stream_store and tunnel_store are the only
# readers and writers; the managers in noust.managers say what the values mean).
#
# site_policies: one JSON document per application, keyed by its primary domain:
#   basic authentication, IP lists, the real client address behind a CDN, rate
#   limits, response headers, and which of www or the bare name is canonical. A
#   table of its own, not columns of ``apps`` or ``sites``, so the full-row write
#   of a redeploy cannot put back what an operator changed while it ran, and the
#   site is rendered from it every time, whichever deployer renders it.
# site_auth_users: the accounts of an application's basic authentication. Only the
#   salted hash is kept (the password is never stored); the htpasswd file the web
#   server reads is derived from these rows each time the site is written.
# custom_certificates: a certificate and key the operator uploaded; the files live
#   in Noust's certificates directory (the key 0600), the row keeps what is safe to list.
# certificate_settings: how an application's certificate comes to be. ``acme`` is the
#   lineage Let's Encrypt (or another CA) keeps under the application's own name, ordered
#   with the HTTP-01 challenge or with DNS-01 through a DNS provider; ``custom`` is an
#   uploaded certificate; ``lineage`` is another lineage that already covers the name
#   (a wildcard), served instead of ordering one.
# dns_providers: an account at a DNS provider. Its token is a secret file, never a
#   column; ``settings`` holds what is not secret (an account id).
# dns_records: a record Noust manages at a provider, and whether Noust created it
#   (``owned``) or adopted one that was already there; dns_record_refs: which
#   applications use it, so a name shared by two is deleted when the last lets go.
# stream_exposures: a TCP or UDP port nginx's ``stream`` module publishes in front of
#   a backend. ``firewall`` is what Noust opened for it (and so what it closes);
#   ``state`` says how far an opening or a closing got, so an interrupted one is
#   finished from the row. A port is held by one exposure per protocol.
# tunnels: a Cloudflare Tunnel Noust created through a DNS account, and the unit that
#   runs its connector; tunnel_hostnames: the public names it carries, each to a local
#   service. The connector's token is a secret file, never a column.
NETWORK_SQL = """
CREATE TABLE IF NOT EXISTS site_policies (
    domain TEXT PRIMARY KEY,
    document TEXT NOT NULL,
    updated_by TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS site_auth_users (
    domain TEXT NOT NULL,
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_by TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (domain, username)
);
CREATE TABLE IF NOT EXISTS custom_certificates (
    name TEXT PRIMARY KEY,
    names TEXT NOT NULL DEFAULT '[]',
    subject TEXT,
    issuer TEXT,
    key_type TEXT,
    fingerprint TEXT NOT NULL,
    not_before TEXT,
    not_after TEXT,
    uploaded_by TEXT,
    uploaded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS certificate_settings (
    domain TEXT PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'acme' CHECK (source IN ('acme', 'custom', 'lineage')),
    name TEXT,
    challenge TEXT NOT NULL DEFAULT 'http-01' CHECK (challenge IN ('http-01', 'dns-01')),
    dns_provider TEXT,
    updated_by TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dns_providers (
    name TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    automatic INTEGER NOT NULL DEFAULT 0,
    settings TEXT NOT NULL DEFAULT '{}',
    verified_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT
);
CREATE TABLE IF NOT EXISTS dns_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL REFERENCES dns_providers(name) ON DELETE CASCADE,
    zone_id TEXT NOT NULL,
    zone_name TEXT NOT NULL,
    hostname TEXT NOT NULL,
    record_type TEXT NOT NULL,
    content TEXT NOT NULL,
    remote_id TEXT,
    owned INTEGER NOT NULL DEFAULT 1,
    proxied INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    created_by TEXT,
    unreferenced_since TEXT,
    UNIQUE (provider, hostname, record_type)
);
CREATE TABLE IF NOT EXISTS dns_record_refs (
    record_id INTEGER NOT NULL REFERENCES dns_records(id) ON DELETE CASCADE,
    app_domain TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (record_id, app_domain)
);
CREATE TABLE IF NOT EXISTS stream_exposures (
    name TEXT PRIMARY KEY,
    app_domain TEXT,
    protocol TEXT NOT NULL CHECK (protocol IN ('tcp', 'udp')),
    listen_port INTEGER NOT NULL,
    target_host TEXT NOT NULL,
    target_port INTEGER NOT NULL,
    sources TEXT NOT NULL DEFAULT '[]',
    timeout_seconds INTEGER NOT NULL DEFAULT 3600,
    firewall TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL CHECK (state IN ('opening', 'open', 'closing')),
    created_at TEXT NOT NULL,
    created_by TEXT,
    UNIQUE (protocol, listen_port)
);
CREATE TABLE IF NOT EXISTS tunnels (
    name TEXT PRIMARY KEY,
    provider TEXT NOT NULL REFERENCES dns_providers(name) ON DELETE CASCADE,
    account_id TEXT NOT NULL,
    tunnel_id TEXT NOT NULL,
    unit TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('creating', 'ready', 'removing')),
    created_at TEXT NOT NULL,
    created_by TEXT
);
CREATE TABLE IF NOT EXISTS tunnel_hostnames (
    tunnel TEXT NOT NULL REFERENCES tunnels(name) ON DELETE CASCADE,
    hostname TEXT NOT NULL UNIQUE,
    app_domain TEXT,
    service TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tunnel, hostname)
);
"""
# site_config_versions: the text a site's file held, each time Noust wrote it, an operator saved
#   it or it was found changed (noust.core.site_history_store is its only reader and writer).
#   ``domain`` is the name the API addresses the site by; ``source`` says how the text came to
#   be (``generated``: rendered by a deployer; ``edit``: saved from the editor or the API;
#   ``restore``: an earlier version put back; ``found``: on disk when Noust first looked, or
#   different from what Noust last wrote, so a deploy about to overwrite it keeps it).
NETWORK_SQL += """
CREATE TABLE IF NOT EXISTS site_config_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    webserver TEXT NOT NULL,
    version INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('generated', 'edit', 'restore', 'found')),
    actor TEXT,
    created_at TEXT NOT NULL,
    text TEXT NOT NULL,
    UNIQUE (domain, webserver, version)
);
CREATE INDEX IF NOT EXISTS idx_site_config_versions_site ON site_config_versions(domain, webserver, version);
"""

# client_certificates: the certificates Noust signed for the clients of one application's site
#   (noust.core.client_cert_store is its only reader and writer). ``app_domain`` is the
#   application's primary domain; ``serial`` is what the web server reads of a presented
#   certificate, upper-case hex, so a revoked one is refused by name; the private key is never
#   here.
NETWORK_SQL += """
CREATE TABLE IF NOT EXISTS client_certificates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_domain TEXT NOT NULL,
    name TEXT NOT NULL,
    serial TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    not_before TEXT NOT NULL,
    not_after TEXT NOT NULL,
    issued_by TEXT,
    issued_at TEXT NOT NULL,
    revoked_at TEXT,
    revoked_by TEXT,
    UNIQUE (app_domain, serial)
);
CREATE INDEX IF NOT EXISTS idx_client_certificates_app ON client_certificates(app_domain, id);
"""

# The port an alias is proxied to, when it is not the application's own: a name
# answered by another listener of the same application (an admin interface).
# NULL is the application's port.
#
# Whether a name is served. A name that is switched off keeps its row, its kind, its port and its
# DNS record, and the site and the certificate leave it out: the operator who wants a name to rest
# does not have to delete it and add it back.
NETWORK_COLUMNS: list[tuple[str, str, str]] = [
    ("domains", "port", "INTEGER"),
    ("domains", "enabled", "INTEGER NOT NULL DEFAULT 1"),
]

# Owned by the notifications lane (noust.core.notification_ledger and
# noust.core.alerts): the record of every delivery, the user-defined alert rules
# and what the monitor remembers between two scans.
#
# notification_deliveries: one row per notification and channel instance.
#   state is ``retrying`` (failed, waiting for next_attempt_at), ``held`` (quiet
#   hours: delivered at next_attempt_at),
#   ``sent`` or ``failed`` (gave up, or could never succeed). payload is the
#   notification as JSON for as long as it may still be delivered or retried by
#   hand; a row that was sent keeps only the facts that identify it. last_error is
#   the destination's own words, already scrubbed of the channel's credentials.
# alert_rules: what the operator asked to be told about: a metric, a comparator
#   and a threshold that must hold for duration_s seconds, cleared once back
#   inside it by hysteresis, announced at most once per cooldown_s. app_domain
#   NULL is the server for a server metric and every application for an
#   application one.
# alert_states: where each (rule, subject) is. ``firing`` while the limit is
#   breached and ``ok`` once it cleared; the row stays so the cooldown
#   (announced_at, the last time a firing message went out) survives a clear.
#   ``announced`` says whether the current firing was told: one that fired
#   inside the cooldown is silent, and so is its end. ``acknowledged_at`` and
#   ``acknowledged_by`` say that somebody took the firing: it is kept for as long
#   as it goes on firing and forgotten the moment it clears or starts again.
# notification_state: small facts a check of the monitor must keep between scans
#   (the last cron invocation seen, when a missing backup was last announced).
# probes, probe_state, probe_results, probe_daily, probe_incidents: the addresses
#   the monitor asks from outside the machine and what they answered
#   (noust.core.probe_store). The result rows are kept a week; probe_daily, one
#   row per probe and day, is what uptime is read from, kept past a year.
NOTIFY_SQL = """
CREATE TABLE IF NOT EXISTS notification_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    code TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL,
    channel_type TEXT NOT NULL,
    state TEXT NOT NULL
        CHECK (state IN ('retrying', 'held', 'sent', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    next_attempt_at REAL,
    last_error TEXT,
    payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_notification_deliveries_due
    ON notification_deliveries(state, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_notification_deliveries_created
    ON notification_deliveries(created_at);
CREATE TABLE IF NOT EXISTS alert_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    metric TEXT NOT NULL,
    app_domain TEXT,
    comparator TEXT NOT NULL CHECK (comparator IN ('>', '>=', '<', '<=')),
    threshold REAL NOT NULL,
    duration_s INTEGER NOT NULL DEFAULT 300,
    hysteresis REAL NOT NULL DEFAULT 0,
    cooldown_s INTEGER NOT NULL DEFAULT 3600,
    severity TEXT NOT NULL DEFAULT 'warning' CHECK (severity IN ('warning', 'failed')),
    enabled INTEGER NOT NULL DEFAULT 1,
    remind INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    created_at REAL NOT NULL,
    created_by TEXT,
    updated_at REAL NOT NULL,
    updated_by TEXT
);
CREATE TABLE IF NOT EXISTS alert_states (
    rule_id INTEGER NOT NULL REFERENCES alert_rules(id) ON DELETE CASCADE,
    subject TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL CHECK (state IN ('ok', 'firing')),
    since REAL NOT NULL,
    last_value REAL,
    announced INTEGER NOT NULL DEFAULT 0,
    announced_at REAL,
    PRIMARY KEY (rule_id, subject)
);
CREATE TABLE IF NOT EXISTS notification_state (
    scope TEXT NOT NULL,
    item TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (scope, item)
);
CREATE TABLE IF NOT EXISTS probes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT 'GET' CHECK (method IN ('GET', 'HEAD')),
    expect_status TEXT NOT NULL DEFAULT '200-399',
    expect_text TEXT,
    interval_s INTEGER NOT NULL DEFAULT 60,
    timeout_s INTEGER NOT NULL DEFAULT 10,
    fail_after INTEGER NOT NULL DEFAULT 3,
    public INTEGER NOT NULL DEFAULT 0,
    title TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    created_by TEXT,
    updated_at REAL NOT NULL,
    updated_by TEXT
);
CREATE TABLE IF NOT EXISTS probe_state (
    probe_id INTEGER PRIMARY KEY REFERENCES probes(id) ON DELETE CASCADE,
    state TEXT NOT NULL DEFAULT 'unknown' CHECK (state IN ('unknown', 'up', 'down')),
    since REAL NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    announced INTEGER NOT NULL DEFAULT 0,
    failing_since REAL,
    last_checked REAL,
    last_status INTEGER,
    last_latency_ms INTEGER,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS probe_results (
    probe_id INTEGER NOT NULL REFERENCES probes(id) ON DELETE CASCADE,
    ts REAL NOT NULL,
    ok INTEGER NOT NULL,
    status INTEGER,
    latency_ms INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_probe_results_time ON probe_results(probe_id, ts);
CREATE TABLE IF NOT EXISTS probe_daily (
    probe_id INTEGER NOT NULL REFERENCES probes(id) ON DELETE CASCADE,
    day INTEGER NOT NULL,
    checks INTEGER NOT NULL DEFAULT 0,
    good INTEGER NOT NULL DEFAULT 0,
    latency_sum INTEGER NOT NULL DEFAULT 0,
    latency_max INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (probe_id, day)
);
CREATE TABLE IF NOT EXISTS probe_incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    probe_id INTEGER NOT NULL REFERENCES probes(id) ON DELETE CASCADE,
    started_at REAL NOT NULL,
    ended_at REAL,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_probe_incidents_probe ON probe_incidents(probe_id, started_at);
"""
# held_for: why a delivery is held (``quiet`` hours, a ``flood`` of messages, the
#   ``summary`` of the day); NULL for a delivery that is not held, and for one held
#   by the quiet hours before 4.0.
NOTIFY_COLUMNS: list[tuple[str, str, str]] = [
    ("notification_deliveries", "held_for", "TEXT"),
    ("alert_states", "acknowledged_at", "REAL"),
    ("alert_states", "acknowledged_by", "TEXT"),
]

# Owned by the single sign-on lane (noust.core.accounts.oidc_store).
#
# oidc_providers: one row per identity provider the operator configured. The
#   client secret is never a column: it lives in the secret store under
#   ``oidc/<name>``. ``issuer`` is kept exactly as typed, because a token's
#   ``iss`` is compared to it byte for byte (some providers end it in a slash).
#   provisioning says what a first sign-in may do: ``off`` (only an account that
#   exists), ``invite`` (activate the invited account of that e-mail) or ``auto``
#   (create one with default_role, in default_team when one is named).
#   default_role cannot be security or auditor: those are given by a person.
#   group_mappings is a JSON list of {group, role} or {group, team, team_role}.
# account_identities: who an account is at an outside provider, by the pair the
#   provider guarantees (issuer, subject) and never by e-mail alone. ``protocol``
#   is there so a SAML or SCIM identity is a row of the same table. mapped_role is
#   the instance role the provider's group mapping last gave, so removing the
#   group can take it back without touching a role an administrator set by hand.
# oidc_flows: a sign-in between the redirect to the provider and its answer. Only
#   the hashes of the state and of the browser binding are kept, the nonce and
#   the PKCE verifier are what the answer is checked against. Spent by the answer.
# oidc_tickets: the answer, verified, waiting for the console to collect it: a
#   single-use handle on a sign-in that already passed the provider and still
#   owes the account's own second factor.
SSO_SQL = """
CREATE TABLE IF NOT EXISTS oidc_providers (
    name TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    issuer TEXT NOT NULL,
    client_id TEXT NOT NULL,
    scopes TEXT NOT NULL DEFAULT 'openid email profile',
    enabled INTEGER NOT NULL DEFAULT 1,
    require_email_verified INTEGER NOT NULL DEFAULT 1,
    assume_email_verified INTEGER NOT NULL DEFAULT 0,
    clock_skew_seconds INTEGER NOT NULL DEFAULT 60,
    provisioning TEXT NOT NULL DEFAULT 'off'
        CHECK (provisioning IN ('off', 'invite', 'auto')),
    default_role TEXT NOT NULL DEFAULT 'viewer'
        CHECK (default_role IN ('viewer', 'operator', 'admin')),
    default_team TEXT,
    default_team_role TEXT,
    link_by_email INTEGER NOT NULL DEFAULT 0,
    allowed_domains TEXT NOT NULL DEFAULT '[]',
    require_amr TEXT NOT NULL DEFAULT '[]',
    groups_claim TEXT NOT NULL DEFAULT 'groups',
    group_sync INTEGER NOT NULL DEFAULT 0,
    group_mappings TEXT NOT NULL DEFAULT '[]',
    redirect_uri TEXT,
    created_at REAL NOT NULL,
    created_by TEXT,
    updated_at REAL NOT NULL,
    updated_by TEXT
);
CREATE TABLE IF NOT EXISTS account_identities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    protocol TEXT NOT NULL DEFAULT 'oidc',
    provider TEXT NOT NULL,
    issuer TEXT NOT NULL,
    subject TEXT NOT NULL,
    email TEXT,
    mapped_role TEXT,
    created_at REAL NOT NULL,
    created_by TEXT,
    last_login_at REAL,
    last_login_ip TEXT,
    UNIQUE (protocol, issuer, subject)
);
CREATE INDEX IF NOT EXISTS idx_account_identities_account ON account_identities(account_id);
CREATE TABLE IF NOT EXISTS oidc_flows (
    state_hash TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    nonce TEXT NOT NULL,
    code_verifier TEXT NOT NULL,
    binding_hash TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    next_path TEXT NOT NULL DEFAULT '/',
    client_ip TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oidc_flows_expires ON oidc_flows(expires_at);
CREATE TABLE IF NOT EXISTS oidc_tickets (
    ticket_hash TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    binding_hash TEXT NOT NULL,
    client_ip TEXT NOT NULL,
    next_path TEXT NOT NULL DEFAULT '/',
    amr TEXT NOT NULL DEFAULT '[]',
    detail TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oidc_tickets_expires ON oidc_tickets(expires_at);
"""
SSO_COLUMNS: list[tuple[str, str, str]] = []

# Owned by the servers lane (noust.fleet.cloud): the cloud accounts a central
# creates servers with, the cloud-init scripts it keeps for them, and the
# machines it created, how long each server has been silent and whether its monitoring is
# paused (node_liveness; no row means it answers), and the scheduled cleanup of a server's disk with the history of
# every cleanup that ran (storage_cleanup_*; one row per run, the actions it took in
# a JSON list, the newest 200 kept). Nothing secret is stored here: an account's API token
# lives in the secret store (``cloud-accounts/<name>``), sealed with the rest on
# a sealed central, and the row keeps only its last four characters so the
# operator can tell two tokens apart.
#
# No CHECK on a provider or a state: SQLite cannot alter one, and a provider
# added later must not need a table rebuild. The code validates both.
#
# cloud_servers keeps a machine after its server is removed from the fleet (state
# ``deleted``), so the history of what was created and when survives. ``node`` is
# the name the server has on this central.
#
# node_links: a server that dials this central, instead of the other way round (it
# is behind NAT or a CGNAT, with no SSH port anyone can reach). ``port`` is the
# loopback port of this machine its link listens on, one per server and never
# shared; ``state`` is ``invited`` until the server's code is accepted and
# ``active`` after; ``nonce`` ties the code to the invitation it answers;
# ``link_key`` is the public half of the key the server dials in with. Nothing
# secret is stored: the server's fleet token is in the secret store like every
# node's.
#
# node_ssh_options: how the central connects to one server beyond its address
# (noust.fleet.ssh_options is its only reader and writer): the seconds ssh waits
# for the connection (NULL is the default of 10) and the bastion it goes through
# (host, port, account and the bastion's pinned ed25519 host key; NULL host is a
# direct connection). A row goes with its node. Nothing secret: the key the
# central presents at the bastion is the node's own, in the secret store.
#
# fleet_placements: an application that runs on several servers
# (noust.fleet.placements is its only reader and writer): the servers it is named
# on (a JSON list), the label selector that adds the servers carrying it (a JSON
# object), and the default way an update goes out over them (servers at a time, how
# many failures stop it, which goes first, whether the ones already moved are put
# back). It holds no copy of the application, which each server keeps; and it is the
# central's, not a team's: the fleet routes are the instance's.
FLEET_SQL = """
CREATE TABLE IF NOT EXISTS cloud_accounts (
    name TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    token_tail TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT,
    verified_at TEXT,
    last_used_at TEXT
);

CREATE TABLE IF NOT EXISTS cloud_init_scripts (
    name TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    description TEXT,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cloud_servers (
    node TEXT PRIMARY KEY,
    account TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    address TEXT,
    location TEXT,
    plan TEXT,
    image TEXT,
    state TEXT NOT NULL,
    error TEXT,
    job_id TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS node_liveness (
    node TEXT PRIMARY KEY,
    failures INTEGER NOT NULL DEFAULT 0,
    down_since REAL,
    announced INTEGER NOT NULL DEFAULT 0,
    next_probe_at REAL,
    last_counted_at REAL,
    last_error TEXT,
    paused INTEGER NOT NULL DEFAULT 0,
    paused_reason TEXT,
    paused_by TEXT,
    paused_at TEXT
);

CREATE TABLE IF NOT EXISTS storage_cleanup_policy (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    document TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);

CREATE TABLE IF NOT EXISTS storage_cleanup_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    mount TEXT,
    percent_before REAL,
    percent_after REAL,
    freed_bytes INTEGER,
    actions TEXT NOT NULL,
    reason TEXT,
    actor TEXT
);

CREATE TABLE IF NOT EXISTS node_links (
    node TEXT PRIMARY KEY,
    port INTEGER NOT NULL UNIQUE,
    state TEXT NOT NULL,
    nonce TEXT NOT NULL,
    central_host TEXT NOT NULL,
    central_port INTEGER NOT NULL,
    link_key TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT,
    activated_at TEXT
);

CREATE TABLE IF NOT EXISTS fleet_placements (
    domain TEXT PRIMARY KEY,
    nodes TEXT NOT NULL DEFAULT '[]',
    selector TEXT NOT NULL DEFAULT '{}',
    serial TEXT,
    max_failures INTEGER,
    canary TEXT,
    rollback_on_failure INTEGER NOT NULL DEFAULT 1 CHECK (rollback_on_failure IN (0, 1)),
    created_at TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS node_ssh_options (
    node TEXT PRIMARY KEY REFERENCES nodes(name) ON DELETE CASCADE,
    connect_timeout INTEGER,
    bastion_host TEXT,
    bastion_port INTEGER,
    bastion_user TEXT,
    bastion_host_key TEXT,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);
"""
FLEET_COLUMNS: list[tuple[str, str, str]] = []


# Owned by the runtime lane: how an application runs and is operated.
#
# app_health: what the health gate asks beyond the path, the statuses and the
#   wait that live on ``apps`` since v9: the kind (a request, or a command run as
#   the application), the method, scheme, host name and port of the request, the
#   command and the Compose service it runs in, the seconds between probes, the
#   most probes and the start period. A table of its own so a redeploy's
#   full-row write of ``apps`` cannot put back what an operator changed while it
#   ran. NULL is the gate's own default.
#
# app_applied_config: what an application's configuration was when it was last
#   put to work (noust.core.applied_config_store is its only reader and writer):
#   one JSON document, one section per area (environment, limits, health, hooks,
#   build, container, sandbox...), taken when a deployment succeeds and refreshed,
#   for the areas a restart applies, when the application restarts. The pending
#   changes of an application are what differs between it and the configuration
#   now (noust.deployers.pending_changes). Values are never stored, only their
#   salted hashes: the document answers "did it change", never "to what".
#
# app_processes: the processes an application runs beside its web process (a
#   queue worker, a scheduler), each a unit of its own from the same release
#   (noust.core.app_process_store is its only reader and writer). ``instances``
#   is how many copies run (0 keeps the definition and stops them); the limits are
#   the process's own. ``source`` says where the definition came from: the
#   operator's wins over the repository's, which is synchronised at every deploy.
#
# app_mounts: the files and directories an application is given beyond its repository
#   (noust.core.app_mount_store is its only reader and writer). A ``file`` is a
#   configuration file Noust keeps for the application and links into every
#   release (``target`` is its path in the application; on a container
#   application, where it is mounted in the container); its content is the file on
#   disk, never a column. A ``path`` is a directory or file of the server bound
#   into a container application (``source`` on the host, ``target`` in the
#   container), read-only unless asked.
#
# log_drains: where the logs of applications are shipped to besides the journal
#   (noust.core.log_drain_store is its only reader and writer): a Loki, an Axiom
#   dataset, an HTTP endpoint (Fluent Bit, Vector, Logstash), an OTLP collector or
#   a remote syslog. One row per destination, server-wide; ``apps`` narrows it to
#   some applications (empty is every one). ``settings`` holds what is not secret
#   (URL, labels, transport); the token or password is in the secret store under
#   ``log-drains/<name>``. ``cursors`` is how far each source was shipped (the
#   journal's cursor, a stack's last timestamp), so a destination that was down
#   receives what it missed when it is back.
#
# app_hardening: how much of systemd's sandbox an application's unit runs in
#   (noust.core.unit_hardening_store is its only reader and writer): ``off``,
#   ``default`` or ``strict``, and the extra directories the unit may write to
#   (a JSON list). No row means the application predates hardening and its unit
#   is as it always was: a redeploy never hardens it by itself, an operator
#   does, behind the health gate.
#
# app_unit_tuning: what an application's unit is tuned with beyond memory, CPU and tasks
#   (noust.core.unit_tuning_store is its only reader and writer): a soft memory limit, swap, CPU and
#   I/O weight, a set of CPUs, ulimits, the capabilities the process keeps and the devices it may
#   open. ``options`` is one JSON document validated by noust.managers.unit_tuning. No row means
#   the unit is as it always was.
#
# app_adoption: what a site Noust adopted is (noust.core.native_adoption_store is its only reader
#   and writer): ``proxy`` (a web server in front of a unit the operator wrote), ``php`` or
#   ``static`` (the web server alone). ``unit`` is the operator's unit, the one thing Noust may
#   start, stop, restart and read the logs of; ``site_file`` is the operator's site, ``root`` the
#   directory, ``upstream`` what the site hands requests to. An application of the ``adopted``
#   type always has its row: an adoption that cannot write it removes the application it made.
#
# app_disk: what an application may take of the disk and what it last took (noust.core.app_disk_store
#   is its only reader and writer, checklist B-63). ``soft_mb`` is the limit Noust holds it to: a
#   deploy that would build a new release is refused while the application is over it. It is not a
#   kernel quota. ``usage`` is the last measurement as one JSON document, taken by a job because
#   nothing measures a tree in a request, and ``measured_at`` says how old it is. ``kernel_project``
#   and ``kernel_mount`` say that the limit was also given to the kernel as a project quota of that
#   id on that mount (noust.managers.kernel_quota); NULL when it was not. No row means no limit and
#   no measurement.
#
# app_replicas: the extra copies of an application's web process (noust.core.app_replica_store
#   is its only reader and writer): the ports reserved for them, in order, and how many run.
#   No row means one copy, the application's own unit. nginx balances over the unit and the
#   copies through the upstream file; the ports count as the application's for whoever picks one.
#
# compose_release_images: the images a Compose stack keeps for each release it served
#   (noust.core.compose_release_store is its only reader and writer), so going back
#   to it recreates the containers from them instead of building again. The release
#   itself is a row of ``releases``, as a container application's is; this says which
#   image each service ran (``reference`` is the name the compose file looks it up by,
#   ``tag`` the name the release keeps it under, ``image_id`` its immutable id).
RUNTIME_SQL = """
CREATE TABLE IF NOT EXISTS app_health (
    app_id INTEGER PRIMARY KEY REFERENCES apps(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'http' CHECK (kind IN ('http', 'command')),
    method TEXT,
    scheme TEXT,
    host TEXT,
    port INTEGER,
    command TEXT,
    service TEXT,
    interval_seconds INTEGER,
    retries INTEGER,
    start_period INTEGER,
    updated_by TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_applied_config (
    app_id INTEGER PRIMARY KEY REFERENCES apps(id) ON DELETE CASCADE,
    deployment_id INTEGER,
    document TEXT NOT NULL,
    taken_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_processes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    command TEXT NOT NULL,
    instances INTEGER NOT NULL DEFAULT 1 CHECK (instances BETWEEN 0 AND 32),
    memory_max_mb INTEGER,
    cpu_quota_percent INTEGER,
    tasks_max INTEGER,
    source TEXT NOT NULL DEFAULT 'operator' CHECK (source IN ('operator', 'repository')),
    updated_by TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (app_id, name)
);

CREATE TABLE IF NOT EXISTS app_mounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('file', 'path')),
    target TEXT NOT NULL,
    source TEXT,
    mode INTEGER NOT NULL DEFAULT 384,
    read_only INTEGER NOT NULL DEFAULT 1,
    updated_by TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (app_id, target)
);

CREATE TABLE IF NOT EXISTS log_drains (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('loki', 'axiom', 'newrelic', 'http', 'otlp', 'syslog')),
    settings TEXT NOT NULL DEFAULT '{}',
    apps TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1,
    cursors TEXT NOT NULL DEFAULT '{}',
    last_ok_at TEXT,
    last_error TEXT,
    last_error_at TEXT,
    shipped INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_hardening (
    app_id INTEGER PRIMARY KEY REFERENCES apps(id) ON DELETE CASCADE,
    profile TEXT NOT NULL CHECK (profile IN ('off', 'default', 'strict')),
    writable_paths TEXT NOT NULL DEFAULT '[]',
    changed_by TEXT,
    changed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_unit_tuning (
    app_id INTEGER PRIMARY KEY REFERENCES apps(id) ON DELETE CASCADE,
    options TEXT NOT NULL DEFAULT '{}',
    changed_by TEXT,
    changed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_adoption (
    app_id INTEGER PRIMARY KEY REFERENCES apps(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('proxy', 'php', 'static')),
    unit TEXT,
    webserver TEXT NOT NULL,
    site_file TEXT NOT NULL,
    root TEXT,
    upstream TEXT,
    adopted_by TEXT,
    adopted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_disk (
    app_id INTEGER PRIMARY KEY REFERENCES apps(id) ON DELETE CASCADE,
    soft_mb INTEGER CHECK (soft_mb IS NULL OR soft_mb >= 64),
    usage TEXT,
    measured_at TEXT,
    kernel_project INTEGER,
    kernel_mount TEXT,
    changed_by TEXT,
    changed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_replicas (
    app_id INTEGER PRIMARY KEY REFERENCES apps(id) ON DELETE CASCADE,
    ports TEXT NOT NULL DEFAULT '[]',
    running INTEGER NOT NULL DEFAULT 0,
    changed_by TEXT,
    changed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS compose_release_images (
    app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    release_id TEXT NOT NULL,
    service TEXT NOT NULL,
    reference TEXT NOT NULL,
    image_id TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (app_id, release_id, service)
);
"""
RUNTIME_COLUMNS: list[tuple[str, str, str]] = []
# Owned by the access-control lane (noust.core.access is its only reader and writer).
#
# custom_roles: a role an administrator defines as a set of the permissions that
#   exist (I-07). ``name`` is a slug that never equals a built-in role's;
#   ``permissions`` is a JSON list. Nothing here decides what the permissions
#   mean: noust.web.permissions.access validates a set (separation of duties,
#   the permissions a team can own) before it is written.
# access_bindings: a role given to an account at a scope (I-03): the whole
#   server (``instance``, which adds to the account's own role), a ``team``,
#   a ``project`` or an ``environment``. ``scope_id`` is the row's id in its
#   table (0 for the server). The role is a built-in team role or a custom
#   role's name; a team's own built-in roles stay in team_members, so a
#   binding at team scope is a custom role added to a membership. One binding
#   per account and scope: changing the role replaces it.
RBAC_SQL = """
CREATE TABLE IF NOT EXISTS custom_roles (
    name TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    permissions TEXT NOT NULL,
    created_at REAL NOT NULL,
    created_by TEXT,
    updated_at REAL NOT NULL,
    updated_by TEXT
);
CREATE TABLE IF NOT EXISTS access_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('instance', 'team', 'project', 'environment')),
    scope_id INTEGER NOT NULL DEFAULT 0,
    role TEXT NOT NULL,
    created_at REAL NOT NULL,
    created_by TEXT,
    UNIQUE (account_id, scope_kind, scope_id)
);
CREATE INDEX IF NOT EXISTS idx_access_bindings_account ON access_bindings(account_id);
CREATE INDEX IF NOT EXISTS idx_access_bindings_scope ON access_bindings(scope_kind, scope_id);
"""
RBAC_COLUMNS: list[tuple[str, str, str]] = []


# Owned by the environments lane (noust.core.variables, noust.core.app_tags,
# noust.core.preview_store): the variables an application is given besides its
# own file, the tags it carries, and what its previews are made from.
#
# shared_variables: a variable defined once for a scope and used by reference
#   (``{{project.NAME}}``) from the variables of an application. ``scope`` says
#   whose it is: the whole server, a team, a project, an environment, or the
#   set the previews of one application are given (``preview``). Exactly one of
#   the four owner columns is set, the one the scope names, and ``owner`` repeats
#   its id (0 for the server) so one unique index covers every scope. The owner
#   columns cascade: deleting a project or an application takes its variables
#   with it, and a row can never name an owner that is gone. ``secret`` is the
#   operator's mark (NULL: the one classifier decides); ``audience`` says whether
#   an application that uses the variable hands it to its build, its runtime or
#   both.
# app_env_variables: what the store knows of one variable of an application
#   beyond the ``.env`` file, which holds the resolved runtime value. The
#   ``expression`` is the text the value is made from when it has references in
#   it (re-resolved at every deploy, so a rotated secret reaches the next
#   release); ``audience`` keeps a build-only variable out of the unit's
#   environment file and a runtime-only one out of the build; ``build_value`` is
#   where a build-only variable's value lives, since it is in no ``.env``.
# app_tags: the free labels an application carries, to find it and to deploy a
#   group of them. Lower case, like a node's labels, so ``Prod`` and ``prod`` are
#   never two groups.
# secret_providers: the secret managers Noust resolves references against. A
#   provider's token is never a column: it lives in the secret store under
#   ``secret-providers/<name>``. ``team_id`` restricts who may reference it (NULL
#   is the whole server).
# preview_options / preview_origins: what 4.0 adds to previews without touching
#   the 2.2 tables. ``inherit_env`` is ``all`` (the 2.2 behaviour, minus
#   ``exclude_env``) or ``allowlist`` (only ``allow_env``, the default for
#   previews turned on from 4.0); ``data_mode`` says whether a preview shares its
#   application's databases (``shared``, the 2.2 behaviour) or gets its own.
#   ``preview_origins`` says what a preview was made from (a pull request, or a
#   branch or a tag an operator named), which variables the policy gave it and
#   which were made for it alone (``given_env``, ``owned_env``: a later change
#   of the policy takes back exactly the first, and never the second), and the
#   databases made for it (``engine/name``), dropped with it.
ENVIRONMENTS_SQL = """
CREATE TABLE IF NOT EXISTS shared_variables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL CHECK (scope IN ('server', 'team', 'project', 'environment', 'preview')),
    owner INTEGER NOT NULL,
    team_id INTEGER REFERENCES teams(id) ON DELETE CASCADE,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    environment_id INTEGER REFERENCES environments(id) ON DELETE CASCADE,
    app_id INTEGER REFERENCES apps(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    value TEXT NOT NULL,
    secret INTEGER,
    audience TEXT NOT NULL DEFAULT 'both' CHECK (audience IN ('both', 'build', 'runtime')),
    created_at TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT NOT NULL,
    updated_by TEXT,
    UNIQUE (scope, owner, name),
    CHECK (
        (scope = 'server' AND team_id IS NULL AND project_id IS NULL
            AND environment_id IS NULL AND app_id IS NULL)
        OR (scope = 'team' AND team_id IS NOT NULL AND project_id IS NULL
            AND environment_id IS NULL AND app_id IS NULL)
        OR (scope = 'project' AND project_id IS NOT NULL AND team_id IS NULL
            AND environment_id IS NULL AND app_id IS NULL)
        OR (scope = 'environment' AND environment_id IS NOT NULL AND team_id IS NULL
            AND project_id IS NULL AND app_id IS NULL)
        OR (scope = 'preview' AND app_id IS NOT NULL AND team_id IS NULL
            AND project_id IS NULL AND environment_id IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS app_env_variables (
    app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    audience TEXT NOT NULL DEFAULT 'both' CHECK (audience IN ('both', 'build', 'runtime')),
    expression TEXT,
    build_value TEXT,
    PRIMARY KEY (app_id, name)
);

CREATE TABLE IF NOT EXISTS app_tags (
    app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (app_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_app_tags_tag ON app_tags(tag);

CREATE TABLE IF NOT EXISTS secret_providers (
    name TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    address TEXT,
    settings TEXT NOT NULL DEFAULT '{}',
    team_id INTEGER REFERENCES teams(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS preview_options (
    app_domain TEXT PRIMARY KEY,
    inherit_env TEXT NOT NULL DEFAULT 'all' CHECK (inherit_env IN ('all', 'allowlist')),
    allow_env TEXT NOT NULL DEFAULT '[]',
    data_mode TEXT NOT NULL DEFAULT 'shared' CHECK (data_mode IN ('shared', 'empty')),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS preview_origins (
    domain TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'pr' CHECK (kind IN ('pr', 'branch', 'tag')),
    ref TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    given_env TEXT NOT NULL DEFAULT '[]',
    owned_env TEXT NOT NULL DEFAULT '[]',
    databases TEXT NOT NULL DEFAULT '[]'
);
"""
ENVIRONMENTS_COLUMNS: list[tuple[str, str, str]] = []

# Owned by the delivery lane of the P2 wave (noust.core.app_rollout_store,
# noust.core.deploy_freeze): what an application is told about being delivered.
#
# app_rollout: one row per application that was given anything. ``history_keep`` is
#   how many deployments of the application's history are kept (NULL: the server's
#   ``deploy.history_keep``); ``stop_timeout`` and ``stop_signal`` are the unit's
#   ``TimeoutStopSec`` and ``KillSignal`` (NULL: systemd's); ``pause_on_rollback`` says
#   whether going back pauses what deploys the application by itself (a push, a tag,
#   a schedule) so the next one does not undo it, and ``paused_*`` is that pause while it
#   lasts. A table of its own so the full-row write of a deploy cannot put back what an
#   operator changed meanwhile.
# deploy_freezes: a stretch in which nothing is deployed. ``app_id`` NULL freezes the
#   whole server. ``starts_at`` may be in the future (a window an operator announces);
#   ``ends_at`` NULL lasts until somebody lifts it, and lifting keeps the row.
ROLLOUT_SQL = """
CREATE TABLE IF NOT EXISTS app_rollout (
    app_id INTEGER PRIMARY KEY REFERENCES apps(id) ON DELETE CASCADE,
    history_keep INTEGER,
    stop_timeout INTEGER,
    stop_signal TEXT,
    pause_on_rollback INTEGER NOT NULL DEFAULT 1,
    paused_at TEXT,
    paused_by TEXT,
    paused_reason TEXT,
    updated_by TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deploy_freezes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id INTEGER REFERENCES apps(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    ends_at TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    lifted_at TEXT,
    lifted_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_deploy_freezes_app ON deploy_freezes(app_id);

CREATE TABLE IF NOT EXISTS update_schedules (
    app_id INTEGER PRIMARY KEY REFERENCES apps(id) ON DELETE CASCADE,
    calendar TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    backup_first INTEGER NOT NULL DEFAULT 1,
    only_if_new INTEGER NOT NULL DEFAULT 1,
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_run_at TEXT,
    last_outcome TEXT,
    last_detail TEXT,
    last_deployment_id INTEGER
);

CREATE TABLE IF NOT EXISTS preview_policy (
    app_domain TEXT PRIMARY KEY,
    domain_template TEXT,
    noindex INTEGER NOT NULL DEFAULT 1,
    fork_policy TEXT NOT NULL DEFAULT 'refuse' CHECK (fork_policy IN ('refuse', 'approve')),
    data_seed TEXT NOT NULL DEFAULT 'none' CHECK (data_seed IN ('none', 'clone')),
    updated_at TEXT NOT NULL
);
"""
# update_schedules: when an application is updated by itself (checklist B-23). One row per
#   application: a systemd calendar expression, whether the update is refused when its
#   backup does not happen (``backup_first``), whether it is skipped when the branch (or the
#   tags it follows) has nothing new (``only_if_new``), and how the last run ended. The timer
#   that runs it is ``noust-update-<app>.timer``; the row is the truth about what it is for.
# preview_policy: what the previews of one application are called and told, and what it takes to
#   build one (preview_options, beside it, says what a preview is given). ``domain_template``
#   names them (NULL: pr-<n>-<name>.<base>), ``noindex`` tells search engines to leave them
#   alone, ``fork_policy`` says whether a pull request from a fork or from someone not trusted
#   with the repository is refused or waits for an operator's approval of its commit, and
#   ``data_seed`` whether a database of the preview's own starts as a copy of the application's.
# preview_origins.approved_sha / approved_by: the commit of a pull request an operator
#   approved to be built, and who; a push of another commit asks again.
ROLLOUT_COLUMNS: list[tuple[str, str, str]] = [
    ("preview_origins", "approved_sha", "TEXT"),
    ("preview_origins", "approved_by", "TEXT"),
]


# fleet_builders: the server that builds an application for the others (A-49,
# noust.fleet.builders is its only reader and writer). A central builds nothing itself;
# this says which of its servers compiles the application, once, and the servers that run
# it receive the release built there instead of each building its own. One row per
# application, the central's own record like a placement: the application and its build
# stay on the servers, and a row goes with its node.
SHIPPING_SQL = """
CREATE TABLE IF NOT EXISTS fleet_builders (
    domain TEXT PRIMARY KEY,
    node TEXT NOT NULL REFERENCES nodes(name) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT NOT NULL
);
"""
SHIPPING_COLUMNS: list[tuple[str, str, str]] = []


# Owned by the access lane of 4.0 (noust.core.teams.nodes, noust.core.accounts.reset,
# noust.core.resource_tags, noust.core.accounts.oauth_github).
#
# node_teams: the team a server of the fleet is assigned to. One row per node, so a node
#   belongs to one team or to nobody (the instance's); a row goes with its node, and a team
#   that still has a server cannot be deleted (RESTRICT, and the manager says so first). It is
#   a table of its own, not a column of ``nodes``, so that ``save_node`` (which rewrites the
#   registry's columns whenever a node answers) can never put an owner back.
# account_password_resets: a link mailed to an account's address to choose a new password.
#   Like an invitation only the SHA-256 of the code is stored; ``used_at`` spends it, and a
#   new request spends the ones before it.
# resource_tags: the free labels a database or a systemd service carries (M-07), as ``app_tags``
#   are carried by an application. ``kind`` is ``database`` (``name`` is ``engine/database``) or
#   ``service`` (``name`` is the unit); nothing here is an application, so a team reaches a
#   database's tags through the database and a service's through the server.
# accounts.email: where an invitation and a password reset are mailed; never used to sign in
#   or to link an identity (the identity providers vouch for their own addresses).
# oidc_providers.kind: ``oidc`` (a provider with a discovery document) or ``github`` (OAuth 2.0
#   without OpenID Connect: fixed endpoints, and the person read from the GitHub API).
ACCESS6_SQL = """
CREATE TABLE IF NOT EXISTS node_teams (
    node TEXT PRIMARY KEY REFERENCES nodes(name) ON DELETE CASCADE,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE RESTRICT,
    assigned_at TEXT NOT NULL,
    assigned_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_node_teams_team ON node_teams(team_id);

CREATE TABLE IF NOT EXISTS account_password_resets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL UNIQUE,
    expires_at REAL NOT NULL,
    used_at REAL,
    created_at REAL NOT NULL,
    requested_ip TEXT
);
CREATE INDEX IF NOT EXISTS idx_account_password_resets_account
    ON account_password_resets(account_id);

CREATE TABLE IF NOT EXISTS resource_tags (
    kind TEXT NOT NULL CHECK (kind IN ('database', 'service')),
    name TEXT NOT NULL,
    tag TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (kind, name, tag)
);
CREATE INDEX IF NOT EXISTS idx_resource_tags_tag ON resource_tags(tag);
"""
ACCESS6_COLUMNS: list[tuple[str, str, str]] = [
    ("accounts", "email", "TEXT"),
    ("oidc_providers", "kind", "TEXT NOT NULL DEFAULT 'oidc'"),
]


# Owned by the federation lane (noust.core.accounts.scim_store and its siblings for SAML
# and LDAP): the identity sources that are not OpenID Connect.
#
# scim_sources: one row per identity provider that pushes users and groups to this server
#   (SCIM 2.0, RFC 7644). The bearer token is never a column: only its SHA-256 and the last
#   characters (``token_hint``, to tell tokens apart in a list). A source can create accounts
#   as ``viewer``, ``operator`` or ``admin`` at most, and never reaches an account it did not
#   provision. ``group_mappings`` is the same JSON list the OIDC providers keep.
# scim_users: the roster. ``id`` is the id the provider knows the user by; ``account_id`` is
#   the account it provisioned (invited, with no credential, until the person's first
#   single sign-in activates it). ``disabled_by_scim`` says the provider turned the account
#   off, so that only the provider turns it on again and an administrator's own decision is
#   never overridden. ``mapped_role`` is the instance role a group mapping last gave. A SCIM
#   user is deliberately not a row of account_identities: it is not a way to sign in.
# scim_groups, scim_group_members: the groups the provider pushed and who is in them, so that
#   leaving a group takes effect when the provider says so, not at the next sign-in.
# saml_providers: a SAML 2.0 identity provider people sign in with. Everything in the row is
#   public: ``certificates`` are the provider's *signing* certificates (a JSON list of PEM), the
#   only keys that verify its signatures; a key a message carries is never used. A sign-in in
#   flight is a row of oidc_flows (its ``nonce`` is the AuthnRequest's ID, its ``code_verifier``
#   holds the entity id the assertion must be for).
# saml_assertions: the assertions already accepted, by the digest of the provider and the
#   assertion's ID, kept until they could no longer be valid, so that one cannot be presented
#   twice.
# ldap_directories: a directory (Active Directory, OpenLDAP, FreeIPA) people sign in against with
#   the username and password they have there. The service account's password is never a column:
#   it lives in the secret store under ``ldap/<name>``. ``user_filter`` carries the one
#   placeholder ``{username}``, filled with what a person typed after it is escaped; the
#   directory's own identifier (``subject_attribute``) is what identifies a person, never the
#   name they typed or the e-mail. The admission columns are those every identity source has.
FEDERATION_SQL = """
CREATE TABLE IF NOT EXISTS scim_sources (
    name TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    token_hash TEXT NOT NULL,
    token_hint TEXT NOT NULL,
    token_created_at REAL NOT NULL,
    last_used_at REAL,
    last_used_ip TEXT,
    default_role TEXT NOT NULL DEFAULT 'viewer'
        CHECK (default_role IN ('viewer', 'operator', 'admin')),
    default_team TEXT,
    default_team_role TEXT,
    link_by_email INTEGER NOT NULL DEFAULT 0,
    allowed_domains TEXT NOT NULL DEFAULT '[]',
    group_sync INTEGER NOT NULL DEFAULT 0,
    group_mappings TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    created_by TEXT,
    updated_at REAL NOT NULL,
    updated_by TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_scim_sources_token ON scim_sources(token_hash);
CREATE TABLE IF NOT EXISTS scim_users (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL REFERENCES scim_sources(name) ON DELETE CASCADE,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    external_id TEXT,
    user_name TEXT NOT NULL,
    disabled_by_scim INTEGER NOT NULL DEFAULT 0,
    mapped_role TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (source, user_name),
    UNIQUE (source, account_id)
);
CREATE INDEX IF NOT EXISTS idx_scim_users_external ON scim_users(source, external_id);
CREATE TABLE IF NOT EXISTS scim_groups (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL REFERENCES scim_sources(name) ON DELETE CASCADE,
    display_name TEXT NOT NULL,
    external_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (source, display_name)
);
CREATE TABLE IF NOT EXISTS scim_group_members (
    group_id TEXT NOT NULL REFERENCES scim_groups(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES scim_users(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, user_id)
);
CREATE TABLE IF NOT EXISTS saml_providers (
    name TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    idp_entity_id TEXT NOT NULL,
    sso_url TEXT NOT NULL,
    certificates TEXT NOT NULL DEFAULT '[]',
    sp_entity_id TEXT NOT NULL DEFAULT '',
    subject_attribute TEXT NOT NULL DEFAULT '',
    email_attribute TEXT NOT NULL DEFAULT 'email',
    name_attribute TEXT NOT NULL DEFAULT 'displayName',
    username_attribute TEXT NOT NULL DEFAULT '',
    groups_attribute TEXT NOT NULL DEFAULT 'groups',
    clock_skew_seconds INTEGER NOT NULL DEFAULT 60,
    provisioning TEXT NOT NULL DEFAULT 'off'
        CHECK (provisioning IN ('off', 'invite', 'auto')),
    default_role TEXT NOT NULL DEFAULT 'viewer'
        CHECK (default_role IN ('viewer', 'operator', 'admin')),
    default_team TEXT,
    default_team_role TEXT,
    link_by_email INTEGER NOT NULL DEFAULT 0,
    allowed_domains TEXT NOT NULL DEFAULT '[]',
    require_email_verified INTEGER NOT NULL DEFAULT 0,
    assume_email_verified INTEGER NOT NULL DEFAULT 1,
    require_amr TEXT NOT NULL DEFAULT '[]',
    group_sync INTEGER NOT NULL DEFAULT 0,
    group_mappings TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    created_by TEXT,
    updated_at REAL NOT NULL,
    updated_by TEXT
);
CREATE TABLE IF NOT EXISTS saml_assertions (
    assertion_hash TEXT PRIMARY KEY,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_saml_assertions_expires ON saml_assertions(expires_at);
CREATE TABLE IF NOT EXISTS ldap_directories (
    name TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    host TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 636,
    tls TEXT NOT NULL DEFAULT 'ldaps' CHECK (tls IN ('ldaps', 'starttls', 'none')),
    ca_file TEXT,
    bind_dn TEXT NOT NULL DEFAULT '',
    user_base TEXT NOT NULL,
    user_filter TEXT NOT NULL,
    subject_attribute TEXT NOT NULL DEFAULT 'entryUUID',
    email_attribute TEXT NOT NULL DEFAULT 'mail',
    name_attribute TEXT NOT NULL DEFAULT 'displayName',
    username_attribute TEXT NOT NULL DEFAULT 'uid',
    groups_attribute TEXT NOT NULL DEFAULT 'memberOf',
    group_base TEXT NOT NULL DEFAULT '',
    group_filter TEXT NOT NULL DEFAULT '',
    timeout_seconds INTEGER NOT NULL DEFAULT 10,
    provisioning TEXT NOT NULL DEFAULT 'off'
        CHECK (provisioning IN ('off', 'invite', 'auto')),
    default_role TEXT NOT NULL DEFAULT 'viewer'
        CHECK (default_role IN ('viewer', 'operator', 'admin')),
    default_team TEXT,
    default_team_role TEXT,
    link_by_email INTEGER NOT NULL DEFAULT 0,
    allowed_domains TEXT NOT NULL DEFAULT '[]',
    require_email_verified INTEGER NOT NULL DEFAULT 0,
    assume_email_verified INTEGER NOT NULL DEFAULT 1,
    group_sync INTEGER NOT NULL DEFAULT 0,
    group_mappings TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    created_by TEXT,
    updated_at REAL NOT NULL,
    updated_by TEXT
);
"""
FEDERATION_COLUMNS: list[tuple[str, str, str]] = []


# Owned by the per-name certificates lane (noust.core.domain_certificates): whether a name of an
# application is covered by the lineage under the primary domain (``shared``, what every name was
# before) or holds a certificate of its own (``own``: a lineage named after the name, a server block
# for it, and the shared lineage stops naming it). A column of ``domains`` and not a table of its
# own: the domain row is the thing the choice is about, it goes with it, and the application's
# full-row write of a redeploy never touches it. The value is validated by
# ``NoustStore.set_domain_certificate``; how the own lineage is proven is a ``certificate_settings``
# row keyed by the name, the same record an application's certificate has.
CERTS_SQL = ""
CERTS_COLUMNS: list[tuple[str, str, str]] = [
    ("domains", "certificate", "TEXT NOT NULL DEFAULT 'shared'"),
]


# Owned by the arrival lane (the first deploy of a person with no domain): how the certificate of
# a deployment ended. ``certificate_state`` is ``issued`` or ``failed`` (empty when the
# application asked for none, and on every deployment from before), and ``certificate_output`` is
# what the authority said when it refused, word for word. A certificate that is refused never
# fails a deployment: the application serves over HTTP, and these two columns are how the console
# and ``noust`` say so afterwards. Columns of ``deployments`` and not a table: the answer is about
# that attempt and goes with its row. Validated by ``NoustStore.record_deployment_certificate``.
ARRIVAL_SQL = ""
ARRIVAL_COLUMNS: list[tuple[str, str, str]] = [
    ("deployments", "certificate_state", "TEXT"),
    ("deployments", "certificate_output", "TEXT"),
]


def _fragments() -> list[str]:
    return [
        TEAMS_SQL,
        BUILD_SQL,
        CONTAINER_SQL,
        RECIPES_SQL,
        DELIVERY_SQL,
        SOURCES_SQL,
        DATABASES_SQL,
        DBINSTANCES_SQL,
        BACKUPS_SQL,
        ACCESS_SQL,
        NETWORK_SQL,
        NOTIFY_SQL,
        SSO_SQL,
        FLEET_SQL,
        RUNTIME_SQL,
        RBAC_SQL,
        ENVIRONMENTS_SQL,
        FEDERATION_SQL,
        ROLLOUT_SQL,
        SHIPPING_SQL,
        ACCESS6_SQL,
        CERTS_SQL,
        ARRIVAL_SQL,
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
        *DBINSTANCES_COLUMNS,
        *BACKUPS_COLUMNS,
        *ACCESS_COLUMNS,
        *NETWORK_COLUMNS,
        *NOTIFY_COLUMNS,
        *SSO_COLUMNS,
        *FLEET_COLUMNS,
        *RUNTIME_COLUMNS,
        *RBAC_COLUMNS,
        *ENVIRONMENTS_COLUMNS,
        *FEDERATION_COLUMNS,
        *ROLLOUT_COLUMNS,
        *SHIPPING_COLUMNS,
        *ACCESS6_COLUMNS,
        *CERTS_COLUMNS,
        *ARRIVAL_COLUMNS,
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
