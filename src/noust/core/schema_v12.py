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

import re
import sqlite3
from collections.abc import Callable

#: The schema version this module defines.
VERSION = 12

# Identity: accounts, roles, invitations, approvals (backlog 30, ENS G01/G02/G08).
# Timestamps are UNIX seconds (REAL), like the console's session database, so
# a lock or an invitation is compared with the clock without parsing. Accounts
# are never implied by a row elsewhere: noust.core.accounts is the only writer.
ACCOUNTS_SQL = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL CHECK (role IN ('viewer', 'operator', 'admin', 'security', 'auditor')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'disabled', 'locked', 'invited')),
    person_ref TEXT,
    password_hash TEXT,
    password_changed_at REAL,
    totp_secret TEXT,
    totp_pending_secret TEXT,
    totp_last_steps TEXT NOT NULL DEFAULT '{}',
    backup_codes TEXT NOT NULL DEFAULT '[]',
    last_login_at REAL,
    last_login_ip TEXT,
    failures_since_login INTEGER NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_failed_at REAL,
    last_failed_ip TEXT,
    locked_until REAL,
    locked_reason TEXT,
    notice_version TEXT,
    notice_accepted_at REAL,
    created_at REAL NOT NULL,
    created_by TEXT,
    updated_at REAL NOT NULL,
    disabled_at REAL,
    disabled_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_accounts_person_ref ON accounts(person_ref);
CREATE TABLE IF NOT EXISTS account_invitations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL UNIQUE,
    expires_at REAL NOT NULL,
    used_at REAL,
    created_at REAL NOT NULL,
    created_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_account_invitations_account ON account_invitations(account_id);
CREATE TABLE IF NOT EXISTS account_sod_exceptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_ref TEXT NOT NULL COLLATE NOCASE,
    reason TEXT NOT NULL,
    expires_at REAL NOT NULL,
    created_at REAL NOT NULL,
    created_by TEXT,
    revoked_at REAL
);
-- Passkeys (backlog 48, noust.core.accounts.passkeys). account_id NULL is
-- the master token's own passkeys. user_handle is the WebAuthn user.id,
-- random per server and per owner: a constant one would let a passkey
-- registered on another server reached as localhost replace this one in
-- the authenticator. rp_id binds the credential to the name it was made
-- under. public_key is the COSE key as the authenticator sent it.
CREATE TABLE IF NOT EXISTS account_passkeys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
    credential_id BLOB NOT NULL UNIQUE,
    user_handle BLOB NOT NULL,
    rp_id TEXT NOT NULL,
    public_key BLOB NOT NULL,
    alg INTEGER NOT NULL,
    sign_count INTEGER NOT NULL DEFAULT 0,
    backup_eligible INTEGER NOT NULL DEFAULT 0,
    backup_state INTEGER NOT NULL DEFAULT 0,
    transports TEXT NOT NULL DEFAULT '[]',
    aaguid TEXT,
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    created_by TEXT,
    last_used_at REAL,
    last_used_ip TEXT,
    clone_warning_at REAL
);
CREATE INDEX IF NOT EXISTS idx_account_passkeys_account ON account_passkeys(account_id);
-- A WebAuthn challenge is signed, not stored, when it is issued, so the
-- anonymous sign-in options write nothing. Its nonce is recorded here when
-- it is spent, until it would have expired anyway: the primary key is what
-- refuses a second use.
CREATE TABLE IF NOT EXISTS webauthn_spent (
    nonce BLOB PRIMARY KEY,
    expires_at REAL NOT NULL
);
-- Four-eyes approvals (ENS G08, noust.core.accounts.approvals). A request
-- is the snapshot of one call: its method, path, a display copy of its
-- parameters with secrets redacted, and the fingerprint of the exact call
-- that approval then allows once. Requester and decider are named, not
-- referenced: the record outlives the accounts.
CREATE TABLE IF NOT EXISTS approval_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('infrastructure', 'role_change')),
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    parameters TEXT NOT NULL DEFAULT '{}',
    fingerprint TEXT NOT NULL,
    reason TEXT,
    state TEXT NOT NULL DEFAULT 'requested'
        CHECK (state IN ('requested', 'approved', 'rejected', 'expired', 'executed')),
    requester_kind TEXT NOT NULL,
    requester_id TEXT,
    requester_name TEXT NOT NULL,
    requester_role TEXT,
    requester_person TEXT,
    -- The account behind the requester: itself, or an API token's owner.
    requester_account TEXT,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    decided_at REAL,
    decider_kind TEXT,
    decider_id TEXT,
    decider_name TEXT,
    decider_role TEXT,
    decision_comment TEXT,
    execute_by REAL,
    executed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_approval_requests_state ON approval_requests(state, expires_at);
"""
ACCOUNTS_COLUMNS: list[tuple[str, str, str]] = [
    # Added before 3.1 shipped; listed so a development store stamped 12
    # without it is completed at start (store._complete_v12).
    ("approval_requests", "requester_account", "TEXT"),
]

# Audit v2 (ENS G03-G05) needs nothing here: the chained log file is the record and the
# queue, its shipping cursors sit beside it and reviews are events in it (noust.core.audit),
# so the trail works even when the store does not.
AUDIT_SQL = ""
AUDIT_COLUMNS: list[tuple[str, str, str]] = []

# The server: accepted risks, scheduled reboots, pending host changes (backlog 32, 46).
# Two areas write here, each appending its own statements: the hardening checks
# (accepted risks) and the power schedule below. A statement ends with ';' on
# its own line, which is how the store's script runner finds the boundary.
SERVER_SQL = """
-- Power schedule: a reboot or shutdown the operator asked for. The row carries
-- the boot it was requested in, so the first start after the machine came back
-- can tell "the reboot happened" from "something else restarted the console".
CREATE TABLE IF NOT EXISTS server_power_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL CHECK (action IN ('reboot', 'poweroff')),
    scheduled_for TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    requested_by TEXT,
    message TEXT,
    boot_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled'
        CHECK (status IN ('scheduled', 'cancelled', 'completed', 'lost')),
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_server_power_status ON server_power_actions(status);

-- Hardening checks: a risk the operator accepted instead of fixing it (ENS asks
-- for the exception to be documented). Every acceptance has a reason, a person
-- and an end; a revoked or expired row stays, so the history is the audit trail.
CREATE TABLE IF NOT EXISTS server_accepted_risks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    check_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    accepted_by TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    revoked_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_server_accepted_risks_check
    ON server_accepted_risks(check_id, revoked_at);
"""
SERVER_COLUMNS: list[tuple[str, str, str]] = []

# The fleet: node labels, access ceilings, fleet jobs (backlog 33, 45).
# On a node, fleet_access is its one ceiling for every central it trusts (a
# single row; none means the default, admin without host access). On a central,
# the access_* columns of nodes are the ceiling each node last published
# (GET /api/auth/fleet/self), NULL until one answers: shown and respected there,
# enforced by the node.
FLEET_SQL = """
CREATE TABLE IF NOT EXISTS fleet_access (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    level TEXT NOT NULL CHECK (level IN ('read', 'deploy', 'admin')),
    host_access INTEGER NOT NULL DEFAULT 0 CHECK (host_access IN (0, 1)),
    updated_at TEXT NOT NULL,
    updated_by TEXT
);

-- Node labels (on a central): key=value pairs an operator puts on a node, so
-- a fleet action can aim at a group (env=prod) instead of a list of names.
-- A table of their own, not a column of nodes, whose rows the store reads
-- with cls(**row). They go with the node.
CREATE TABLE IF NOT EXISTS node_labels (
    node TEXT NOT NULL REFERENCES nodes(name) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (node, key)
);

-- Fleet jobs (on a central): what a bulk action was asked to do, and what
-- happened on each node, with the node's own words. Kept apart from the jobs
-- table so a job run from the terminal is recorded the same way, and so the
-- failed nodes of a job can be retried after the central restarted. The node
-- column is a name, not a reference: the history outlives a removed node.
CREATE TABLE IF NOT EXISTS fleet_jobs (
    job_id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    request TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'succeeded', 'failed', 'aborted', 'interrupted')),
    created_at TEXT NOT NULL,
    created_by TEXT,
    owner_pid INTEGER,
    finished_at TEXT,
    retry_of TEXT
);
CREATE INDEX IF NOT EXISTS idx_fleet_jobs_created ON fleet_jobs(created_at);
CREATE TABLE IF NOT EXISTS fleet_job_nodes (
    job_id TEXT NOT NULL REFERENCES fleet_jobs(job_id) ON DELETE CASCADE,
    node TEXT NOT NULL,
    position INTEGER NOT NULL,
    batch INTEGER NOT NULL,
    state TEXT NOT NULL,
    reason TEXT,
    step TEXT,
    node_jobs TEXT NOT NULL DEFAULT '[]',
    items TEXT NOT NULL DEFAULT '[]',
    error TEXT,
    output TEXT,
    started_at TEXT,
    ended_at TEXT,
    PRIMARY KEY (job_id, node)
);

-- An outage the central was told to expect (on a central): a node asked to
-- reboot through it is not alerted about while it is down, until expires_at.
-- One row per node, removed once the node answers after due_at; it goes with
-- the node.
CREATE TABLE IF NOT EXISTS node_expected_outages (
    node TEXT PRIMARY KEY REFERENCES nodes(name) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('reboot')),
    due_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    requested_by TEXT,
    requested_at TEXT NOT NULL
);
"""
FLEET_COLUMNS: list[tuple[str, str, str]] = [
    (
        "nodes",
        "access_level",
        "TEXT CHECK (access_level IS NULL OR access_level IN ('read', 'deploy', 'admin'))",
    ),
    ("nodes", "host_access", "INTEGER CHECK (host_access IS NULL OR host_access IN (0, 1))"),
    ("nodes", "access_read_at", "TEXT"),
]

# Unprivileged builds: per-application sandbox state (backlog 44).
#
# build_sandbox: one row per application whose builds have a regime of their
# own. No row, or mode 'legacy', is an application from before 3.1: it builds
# as it always did (as root) and is warned about until an operator tests and
# enables the sandbox. 'on' builds in the sandbox; 'off' is an operator's
# explicit, audited decision to build as root, with its reason. The last trial
# build is kept with it. The row is state, not history: it goes with its
# application.
#
# compose_exceptions: the Docker Compose stacks allowed privileged containers
# or the Docker socket, each with who allowed it and why. Keyed by domain, not
# by application, because a new stack has to be allowed before its first
# deployment creates it.
#
# app_branch_pins: the applications whose branch an operator chose on purpose
# (``noust app branch``, ``noust update --branch``), with when and who. Without
# a row, apps.branch only records what the application was last seen
# following, and an update keeps following its checkout rather than switching
# production to that name. A table and not a column on apps, whose rows the
# store reads with App(**row).
BUILD_SQL = """
CREATE TABLE IF NOT EXISTS build_sandbox (
    app_id INTEGER PRIMARY KEY REFERENCES apps(id) ON DELETE CASCADE,
    mode TEXT NOT NULL DEFAULT 'legacy' CHECK (mode IN ('on', 'off', 'legacy')),
    network TEXT NOT NULL DEFAULT 'full' CHECK (network IN ('full', 'strict')),
    pty INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    changed_by TEXT,
    changed_at TEXT,
    tested_at TEXT,
    tested_commit TEXT,
    test_passed INTEGER,
    test_detail TEXT
);

CREATE TABLE IF NOT EXISTS compose_exceptions (
    domain TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    allowed_by TEXT NOT NULL,
    allowed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_branch_pins (
    domain TEXT PRIMARY KEY,
    branch TEXT,
    pinned_at TEXT NOT NULL,
    pinned_by TEXT
);
"""
BUILD_COLUMNS: list[tuple[str, str, str]] = []

# Databases: links to applications, backup policies, saved queries (backlog 38).
# database_links: which variable of which application's environment carries a
# database's connection string, so unlinking, rotating a password or dropping
# the database knows what to rewrite. database_accounts: what Noust did to an
# account (the profile it applied, when it last set its password); the engine
# stays the truth for what the account can do. Both are new tables rather than
# columns on databases/database_users, whose rows the store reads with
# cls(**row): a column the dataclass lacks would break every read.
DATABASES_SQL = """
CREATE TABLE IF NOT EXISTS database_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id INTEGER NOT NULL,
    engine TEXT NOT NULL,
    db_name TEXT NOT NULL,
    username TEXT,
    env_var TEXT NOT NULL DEFAULT 'DATABASE_URL',
    extra_vars INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (app_id) REFERENCES apps(id) ON DELETE CASCADE,
    UNIQUE(app_id, engine, db_name)
);
CREATE INDEX IF NOT EXISTS idx_database_links_target ON database_links(engine, db_name);
CREATE TABLE IF NOT EXISTS database_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    engine TEXT NOT NULL,
    username TEXT NOT NULL,
    host TEXT NOT NULL DEFAULT 'localhost',
    db_name TEXT,
    profile TEXT,
    password_changed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(engine, username, host)
);
CREATE TABLE IF NOT EXISTS database_backup_policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    engine TEXT NOT NULL,
    db_name TEXT NOT NULL,
    schedule TEXT NOT NULL,
    retention_count INTEGER,
    retention_days INTEGER,
    destinations TEXT NOT NULL DEFAULT '[]',
    dump_format TEXT,
    verify_restore INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,
    last_status TEXT,
    last_error TEXT,
    last_dump TEXT,
    last_success_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(engine, db_name)
);
CREATE TABLE IF NOT EXISTS database_dumps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    engine TEXT NOT NULL,
    file_name TEXT NOT NULL,
    db_name TEXT,
    origin TEXT NOT NULL DEFAULT 'unknown',
    size INTEGER,
    sha256 TEXT,
    verified_at TEXT,
    verify_status TEXT,
    verify_method TEXT,
    verify_detail TEXT,
    restore_tested_at TEXT,
    restore_test_status TEXT,
    restore_test_detail TEXT,
    remote_copies TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(engine, file_name)
);
CREATE INDEX IF NOT EXISTS idx_database_dumps_target ON database_dumps(engine, db_name);
CREATE TABLE IF NOT EXISTS database_query_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    engine TEXT NOT NULL,
    db_name TEXT NOT NULL,
    statement TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'read',
    kind TEXT NOT NULL DEFAULT 'query',
    outcome TEXT NOT NULL DEFAULT 'ok',
    row_count INTEGER,
    duration_ms REAL,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_database_query_history_owner
    ON database_query_history(owner, engine, db_name, id);
CREATE TABLE IF NOT EXISTS database_saved_queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    engine TEXT NOT NULL,
    db_name TEXT NOT NULL DEFAULT '',
    statement TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(owner, engine, db_name, name)
);
"""
DATABASES_COLUMNS: list[tuple[str, str, str]] = []

# Metrics: rollup tiers and their maxima (backlog 39, 51). Deliberately empty:
# the history is not in this store. It lives in ``metrics.db``, beside
# ``observations.db``, because the ``noust-monitor`` daemon writes it every five
# seconds while the console reads it, and a write that frequent must not queue
# behind the store's transactions. That database versions itself
# (``PRAGMA user_version``, ``noust.monitor.timeseries.SCHEMA_VERSION``): the
# maximum column, the newest-reading table, the collector lease and the
# access-log cursors are its schema 2.
METRICS_SQL = ""
METRICS_COLUMNS: list[tuple[str, str, str]] = []

# Notifications and webhooks: deliveries received and sent (backlog 49, 52).
#
# webhook_deliveries: what each application's deploy webhook received, the last
# few per application (noust.core.webhook_deliveries is the only writer and
# prunes to a bound). Timestamps are ISO-8601 text like the deployments'.
# ``count`` folds a burst of identical refusals (wrong signature, lockout) into
# one row, so a flood of guesses neither grows the table nor pushes the real
# deliveries out of it. The row goes with its application.
NOTIFICATIONS_SQL = """
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    received_at TEXT NOT NULL,
    provider TEXT,
    event TEXT,
    outcome TEXT NOT NULL,
    branch TEXT,
    detail TEXT,
    job_id TEXT,
    delivery_id TEXT,
    count INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_app ON webhook_deliveries(app_id, id);
"""
NOTIFICATIONS_COLUMNS: list[tuple[str, str, str]] = []


# ENS evidence (G15): the inventory an auditor asks for (op.exp.1, mp.info.2):
# who answers for each application, how critical it is, how its information is
# classified. A table of its own rather than columns of apps, whose rows the
# store reads with App(**row); the row goes with its application.
# noust.core.ens.inventory is its only writer.
ENS_SQL = """
CREATE TABLE IF NOT EXISTS app_inventory (
    app_id INTEGER PRIMARY KEY REFERENCES apps(id) ON DELETE CASCADE,
    owner TEXT,
    criticality TEXT
        CHECK (criticality IS NULL OR criticality IN ('low', 'medium', 'high')),
    classification TEXT
        CHECK (classification IS NULL
               OR classification IN ('public', 'internal', 'restricted', 'confidential')),
    notes TEXT,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);
"""
ENS_COLUMNS: list[tuple[str, str, str]] = []


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
        ENS_SQL,
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
        *ENS_COLUMNS,
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
    Whether a store lacks any table, index or column v12 defines.

    Only reads, so a complete store is checked without a write lock.

    Args:
        cursor: A cursor on the store.

    Returns:
        True when :func:`apply_v12` would add something.
    """
    cursor.execute("SELECT type, name FROM sqlite_master WHERE type IN ('table', 'index')")
    present = {(str(kind), str(name)) for kind, name in cursor.fetchall()}
    for fragment in _fragments():
        for kind, name in _CREATED.findall(fragment):
            if (kind.lower(), name) not in present:
                return True
    return any(column not in _existing_columns(cursor, table) for table, column, _ in _columns())


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
