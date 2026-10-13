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
"""
DBINSTANCES_COLUMNS: list[tuple[str, str, str]] = []

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
# The port an alias is proxied to, when it is not the application's own: a name
# answered by another listener of the same application (an admin interface).
# NULL is the application's port.
NETWORK_COLUMNS: list[tuple[str, str, str]] = [
    ("domains", "port", "INTEGER"),
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
#   inside the cooldown is silent, and so is its end.
# notification_state: small facts a check of the monitor must keep between scans
#   (the last cron invocation seen, when a missing backup was last announced).
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
"""
NOTIFY_COLUMNS: list[tuple[str, str, str]] = []

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
"""
FLEET_COLUMNS: list[tuple[str, str, str]] = []


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
