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

# Owned by the functions lane: the tasks that run once and retire themselves (item 164).
# cron_once_history: what a one-time task leaves when it has run and its units are gone, kept
#   thirty days (noust.core.cron_once_history, the only writer): what it was, the moment it was
#   asked for (``run_at``, ISO 8601 with the server's offset), when it started and ended, how it
#   ended in systemd's own word, whether it came late (the server was off), and the last lines it
#   printed, masked. One row per task: ``(name, run_at)`` is unique, so a hook that runs twice
#   writes once. ``app_domain`` is empty for a task of no application.
TASKS_SQL = """
CREATE TABLE IF NOT EXISTS cron_once_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    command TEXT NOT NULL,
    app_domain TEXT NOT NULL DEFAULT '',
    run_as TEXT NOT NULL DEFAULT '',
    working_directory TEXT NOT NULL DEFAULT '',
    timeout INTEGER,
    service TEXT NOT NULL DEFAULT '',
    run_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    result TEXT NOT NULL,
    exit_code INTEGER,
    late INTEGER NOT NULL DEFAULT 0,
    output TEXT NOT NULL DEFAULT '',
    retired_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cron_once_history_task ON cron_once_history(name, run_at);
CREATE INDEX IF NOT EXISTS idx_cron_once_history_retired ON cron_once_history(retired_at);
"""
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
# retention or its operator let it go, so that a server that still has it is not asked for it again;
# ``meta`` is the small facts a restore needs, and what a retention groups by);
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
    meta TEXT NOT NULL DEFAULT '{}',
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
# caller_policy: the cells of the approval policy the operator chose (``noust.core.caller_policy``,
#   the only writer): a class of caller that is not a person, an environment (``production`` or
#   ``other``), a permission that changes something, and one of ``alone``, ``propose`` or ``never``.
#   A cell nobody chose has no row, and is what the release ships (and what the security profile asks).
#   The database refuses a class, an environment or a value outside the closed lists.
# credential_clients: the name and version a credential's client said of itself (the ``clientInfo`` of
#   MCP's ``initialize``, or the product token of ``User-Agent``), at most a few per credential and
#   labelled as what the client said, never as a fact (``noust.core.credential_clients``).
# approval_requests, new columns: who asked and why it waits, so a person deciding sees the class of
#   caller (``caller``), what held it (``origin``: ``rule`` for the second-person rule, ``policy`` for
#   this policy, ``both``), the environment of the resource and the application it names, the
#   permissions the call needs (JSON, what a person must hold to decide a policy request) and the
#   client the credential said it was (JSON). A row from before 4.3 has none of them, and is a ``rule``.
AGENTS_SQL = """
CREATE TABLE IF NOT EXISTS caller_policy (
    caller TEXT NOT NULL CHECK (caller IN ('token', 'assistant')),
    environment TEXT NOT NULL CHECK (environment IN ('production', 'other')),
    permission TEXT NOT NULL,
    value TEXT NOT NULL CHECK (value IN ('alone', 'propose', 'never')),
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (caller, environment, permission)
);
CREATE TABLE IF NOT EXISTS credential_clients (
    credential TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('client_info', 'user_agent')),
    name TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT '',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (credential, source, name, version)
);
CREATE INDEX IF NOT EXISTS idx_credential_clients_seen ON credential_clients(credential, last_seen);
"""
AGENTS_COLUMNS: list[Column] = [
    ("approval_requests", "caller", "TEXT"),
    ("approval_requests", "origin", "TEXT"),
    ("approval_requests", "environment", "TEXT"),
    ("approval_requests", "domain", "TEXT"),
    ("approval_requests", "needs", "TEXT"),
    ("approval_requests", "client", "TEXT"),
]

# Owned by the assistant's base lane: the optional assistant's settings, its provider and
# the limits it runs under, what a person said to it, and what it cost (item 171). Times are ISO
# 8601, UTC.
# assistant_settings: one row (``id`` is 1, made when the assistant is first configured): off
#   until ``enabled``, the provider (``preset``, ``base_url``, ``model``), its key sealed in
#   ``api_key`` (noust.core.assistant.settings seals it and nothing else writes the column) and
#   when it was set, where the data ends up (``location``, with the reason the operator gave for
#   a provider outside under the ENS profile in ``flow_reason``), whether personal data is
#   disguised, the limits and the price table as JSON, and what the probe last found.
# assistant_conversations / assistant_messages: what a person said to it and what it answered,
#   per account, already masked and bounded when they are written; ``markers`` is the key to the
#   disguise of emails and addresses, which never leaves the server. Kept ``retention.assistant_days``.
# assistant_usage: one row per step of a conversation with the tokens it used and what that cost,
#   NULL when the model has no price. Not deleted with a conversation: a person who deletes theirs
#   does not give the month's spending back.
# assistant_actions: one row per action that changes something, for the limit per hour.
# assistant_turns: one row per question and its answer: where it was asked from (``page``), which
#   server it was about, which model answered and where that model is, what it read (``steps``), what
#   it was not offered (``not_offered``), what it said (``answer``, with the lines of the system it
#   quoted in ``citations``), what stopped it, and what it used. Read by its author alone.
# assistant_proposals: a call the assistant prepared for a person to carry out, exactly (method,
#   path, query, body), with its fingerprint (the one the approvals use), the request id the
#   person's browser sends with it so that the audit trail proves it was made, its class (``ask``,
#   ``you``, ``two`` or ``alone`` for one it did itself), its state and what came of it. Lives as
#   long as its conversation.
ASSISTANT_SQL = """
CREATE TABLE IF NOT EXISTS assistant_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 0,
    preset TEXT NOT NULL DEFAULT '',
    base_url TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    api_key TEXT NOT NULL DEFAULT '',
    key_set_at TEXT,
    location TEXT NOT NULL DEFAULT 'unknown',
    flow_reason TEXT NOT NULL DEFAULT '',
    mask_personal INTEGER NOT NULL DEFAULT 1,
    limits TEXT NOT NULL DEFAULT '{}',
    prices TEXT NOT NULL DEFAULT '{}',
    currency TEXT NOT NULL DEFAULT 'USD',
    probe TEXT NOT NULL DEFAULT '{}',
    probed_at TEXT,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS assistant_conversations (
    id TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    markers TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assistant_conversations_account
    ON assistant_conversations(account_id, updated_at);
CREATE TABLE IF NOT EXISTS assistant_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    tool_calls TEXT,
    tool_call_id TEXT,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_assistant_messages_seq
    ON assistant_messages(conversation_id, seq);
CREATE TABLE IF NOT EXISTS assistant_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    account_id INTEGER NOT NULL,
    at TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    estimated INTEGER NOT NULL DEFAULT 0,
    cost REAL
);
CREATE INDEX IF NOT EXISTS idx_assistant_usage_conversation ON assistant_usage(conversation_id);
CREATE INDEX IF NOT EXISTS idx_assistant_usage_at ON assistant_usage(at);
CREATE TABLE IF NOT EXISTS assistant_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    conversation_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assistant_actions_account ON assistant_actions(account_id, at);
CREATE TABLE IF NOT EXISTS assistant_turns (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    account_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'running',
    question TEXT NOT NULL DEFAULT '',
    page TEXT NOT NULL DEFAULT '{}',
    server TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT 'unknown',
    answer TEXT NOT NULL DEFAULT '',
    citations TEXT NOT NULL DEFAULT '{}',
    steps TEXT NOT NULL DEFAULT '[]',
    not_offered TEXT NOT NULL DEFAULT '[]',
    warnings TEXT NOT NULL DEFAULT '[]',
    stop TEXT,
    error TEXT,
    usage TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    ended_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_assistant_turns_seq
    ON assistant_turns(conversation_id, seq);
CREATE TABLE IF NOT EXISTS assistant_proposals (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    account_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    tool TEXT NOT NULL,
    klass TEXT NOT NULL,
    state TEXT NOT NULL,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    query TEXT NOT NULL DEFAULT '[]',
    body TEXT,
    fingerprint TEXT NOT NULL,
    request_id TEXT NOT NULL,
    permission TEXT NOT NULL DEFAULT '',
    environment TEXT NOT NULL DEFAULT 'production',
    server TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '{}',
    effect TEXT NOT NULL DEFAULT '',
    why TEXT NOT NULL DEFAULT '{}',
    facts TEXT NOT NULL DEFAULT '[]',
    plan TEXT,
    needs TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    result TEXT,
    output TEXT,
    status INTEGER,
    approval INTEGER,
    job_id TEXT,
    verified INTEGER NOT NULL DEFAULT 0,
    told INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_assistant_proposals_conversation
    ON assistant_proposals(conversation_id, seq);
"""
ASSISTANT_COLUMNS: list[Column] = []

# Owned by the Docker lane that updates images and edits a stack's Compose (item 189).
# docker_image_checks: what the last look at the registries found of each image a container runs, one
#   row per image reference, replaced whole by the next look (``noust.core.docker_image_checks``, the
#   only reader and writer). ``state`` is ``current``, ``newer`` or ``unknown`` (the registry could not be
#   asked, and ``detail`` says why); ``change`` says what the new version is to the one that runs
#   (``patch``, ``minor``, ``major`` or ``unknown``) and ``floating`` that the tag is one that moves
#   (``latest``). Nothing is asked of a registry unless the operator asks: the summary of the zone only
#   reads these rows.
# docker_stack_versions: the text of a stack's Compose document each time Noust kept it, as a site's
#   file has its history (``noust.core.docker_stack_versions``, the only reader and writer): which
#   number it has for the stack, its hash and size, how it came to be (``found``, ``edit``, ``revert`` or
#   ``restore``), who saved it, why and when. The text can hold a value somebody typed into the
#   document, so a copy of the store masks it (``core/store_secrets.py``).
DOCKER_SQL = """
CREATE TABLE IF NOT EXISTS docker_image_checks (
    reference TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    local_digest TEXT,
    remote_digest TEXT,
    current_version TEXT,
    latest_version TEXT,
    latest_created TEXT,
    change TEXT NOT NULL DEFAULT '',
    floating INTEGER NOT NULL DEFAULT 0,
    method TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS docker_stack_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    version INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    source TEXT NOT NULL,
    actor TEXT,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_docker_stack_versions_stack
    ON docker_stack_versions(domain, version);
"""
DOCKER_COLUMNS: list[Column] = []


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
        DOCKER_SQL,
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
        *DOCKER_COLUMNS,
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
