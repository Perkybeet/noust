# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_store.py`` that other tests build on."""

#: The v1 schema exactly as the 1.2.x releases shipped it, frozen here so the
#: migration is always exercised against what is actually deployed on servers,
#: not against whatever SCHEMA_SQL has since become.
V1_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS apps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL UNIQUE,
    app_type TEXT NOT NULL DEFAULT 'unknown',
    source TEXT,
    branch TEXT,
    port INTEGER,
    app_path TEXT NOT NULL,
    webserver TEXT NOT NULL DEFAULT 'nginx',
    ssl_enabled INTEGER NOT NULL DEFAULT 1,
    ssl_certificate TEXT,
    ssl_key TEXT,
    status TEXT NOT NULL DEFAULT 'unknown',
    is_static INTEGER NOT NULL DEFAULT 0,
    env_vars TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    deployed_at TEXT
);

CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id INTEGER,
    domain TEXT NOT NULL UNIQUE,
    webserver TEXT NOT NULL DEFAULT 'nginx',
    config_path TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    is_static INTEGER NOT NULL DEFAULT 0,
    document_root TEXT,
    proxy_port INTEGER,
    ssl_enabled INTEGER NOT NULL DEFAULT 0,
    ssl_certificate TEXT,
    ssl_key TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (app_id) REFERENCES apps(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id INTEGER,
    name TEXT NOT NULL UNIQUE,
    unit_file TEXT NOT NULL,
    working_directory TEXT NOT NULL,
    command TEXT NOT NULL,
    user TEXT NOT NULL DEFAULT 'www-data',
    "group" TEXT NOT NULL DEFAULT 'www-data',
    enabled INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'inactive',
    port INTEGER,
    environment TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (app_id) REFERENCES apps(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS databases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id INTEGER,
    name TEXT NOT NULL,
    engine TEXT NOT NULL,
    host TEXT NOT NULL DEFAULT 'localhost',
    port INTEGER,
    username TEXT,
    encoding TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (app_id) REFERENCES apps(id) ON DELETE SET NULL,
    UNIQUE(name, engine)
);

CREATE TABLE IF NOT EXISTS database_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    database_id INTEGER,
    username TEXT NOT NULL,
    engine TEXT NOT NULL,
    host TEXT NOT NULL DEFAULT 'localhost',
    privileges TEXT NOT NULL DEFAULT 'ALL',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (database_id) REFERENCES databases(id) ON DELETE CASCADE,
    UNIQUE(username, engine, host)
);

CREATE INDEX IF NOT EXISTS idx_apps_domain ON apps(domain);
CREATE INDEX IF NOT EXISTS idx_apps_status ON apps(status);
CREATE INDEX IF NOT EXISTS idx_sites_domain ON sites(domain);
CREATE INDEX IF NOT EXISTS idx_sites_app_id ON sites(app_id);
CREATE INDEX IF NOT EXISTS idx_services_app_id ON services(app_id);
CREATE INDEX IF NOT EXISTS idx_services_name ON services(name);
CREATE INDEX IF NOT EXISTS idx_databases_engine ON databases(engine);
CREATE INDEX IF NOT EXISTS idx_databases_app_id ON databases(app_id);
"""

#: The deployments table exactly as schema v2 shipped it, frozen for the same
#: reason V1_SCHEMA_SQL is: the v2-to-v3 migration must run against what is
#: deployed, not against whatever DEPLOYMENTS_SCHEMA_SQL becomes later.
V2_DEPLOYMENTS_SQL = """
CREATE TABLE IF NOT EXISTS deployments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('queued', 'running', 'success', 'failed', 'rolled_back')),
    triggered_by TEXT NOT NULL CHECK (triggered_by IN ('panel', 'cli', 'webhook')),
    git_commit TEXT,
    git_branch TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_s REAL,
    log_path TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_deployments_domain_started
    ON deployments(domain, started_at DESC);
"""

#: The jobs table exactly as the 1.6.x releases shipped it, frozen here for the
#: same reason V1_SCHEMA_SQL is.
V4_JOBS_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    progress INTEGER NOT NULL DEFAULT 0,
    total_steps INTEGER NOT NULL DEFAULT 100,
    domain TEXT,
    error TEXT,
    result_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    started_at TEXT,
    finished_at TEXT,
    log_path TEXT
);
"""
