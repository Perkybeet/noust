/**
 * The fake API the databases pages need in a test: two engines running, a third stopped, a
 * handful of databases, one with a policy and dumps. Tests add or replace the routes of what
 * they exercise.
 */

import type {
  BackupPolicy,
  Database,
  DatabaseBackup,
  DatabaseOverview,
  Engine,
  PolicyList,
} from "../../api/queries/databases";
import { json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";

export const ENGINES: Engine[] = [
  {
    name: "postgresql",
    display_name: "PostgreSQL",
    installed: true,
    version: "16.4",
    running: true,
    port: 5432,
    service: "postgresql",
    capabilities: ["dump", "metrics", "profiles", "read_only", "sql", "tables", "users"],
    support: { family: "postgresql", version: "16.4", major: "16", end_of_life: "2028-11-09", status: "supported", message: "Supported." },
    warnings: [],
    stored_account: false,
  },
  {
    name: "mysql",
    display_name: "MySQL/MariaDB",
    installed: true,
    version: "8.0.39",
    running: true,
    port: 3306,
    service: "mysql",
    capabilities: ["dump", "metrics", "profiles", "read_only", "sql", "tables", "users"],
    support: { family: "mysql", version: "8.0.39", major: "8.0", end_of_life: "2026-04-30", status: "ended", message: "Ended." },
    warnings: [],
    stored_account: true,
  },
  { name: "redis", display_name: "Redis", installed: true, version: "7.0.15", running: false, port: 6379, service: "redis-server", capabilities: ["dump", "keys", "metrics"], support: null, warnings: [], stored_account: false },
  { name: "mongodb", display_name: "MongoDB", installed: false, version: null, running: false, port: 27017, service: "mongod", capabilities: ["documents", "dump"], support: null, warnings: [], stored_account: false },
];

function database(name: string, engine: string, extra: Partial<Database> = {}): Database {
  return {
    name,
    engine,
    size: "46.0 MB",
    tables: 0,
    owner: engine === "postgresql" ? "wasm_app" : null,
    encoding: "UTF8",
    tracked: true,
    missing: false,
    unverified: false,
    app: null,
    apps: [],
    username: name,
    engine_version: engine === "postgresql" ? "16.4" : "8.0.39",
    last_backup: null,
    ...extra,
  };
}

export const DATABASES: Database[] = [
  database("example_production", "postgresql", { app: "shop.example.com", apps: ["shop.example.com"], last_backup: "2026-09-29T19:00:00+00:00" }),
  database("example_staging", "postgresql", { tracked: false, size: "8.7 MB" }),
  database("shop_wp", "mysql", { size: "50.0 MB" }),
];

export const POLICY: BackupPolicy = {
  engine: "postgresql",
  database: "example_production",
  configured: true,
  schedule: "*-*-* 02:00:00",
  schedule_alias: "daily",
  retention_count: 7,
  retention_days: 30,
  destinations: [{ name: "offsite-sftp", retention_count: 30, retention_days: 90, exists: true, encrypted: false }],
  dump_format: null,
  verify_restore: true,
  enabled: true,
  timer: { installed: true, next_run: "Wed 2026-09-30 02:00:00 UTC", last_run: "Tue 2026-09-29 02:00:00 UTC" },
  last_run_at: "2026-09-29T02:00:04+00:00",
  last_status: "ok",
  last_error: null,
  last_dump: "postgresql-example_production-20260929_020000.dump",
  last_success_at: "2026-09-29T02:00:04+00:00",
  created_at: "2026-09-01T00:00:00+00:00",
  updated_at: "2026-09-01T00:00:00+00:00",
};

export const POLICIES: PolicyList = {
  policies: [POLICY],
  total: 1,
  unprotected: [
    { engine: "postgresql", database: "example_staging" },
    { engine: "mysql", database: "shop_wp" },
  ],
};

export function dump(name: string, extra: Partial<DatabaseBackup> = {}): DatabaseBackup {
  return {
    path: `/var/backups/noust/databases/${name}`,
    name,
    database: "example_production",
    engine: "postgresql",
    size: 4_109_312,
    size_human: "3.9 MB",
    created: "2026-09-29T02:00:00+00:00",
    compressed: false,
    format: "custom",
    kind: "scheduled",
    sha256: "9b73",
    verified_at: "2026-09-29T02:00:05+00:00",
    verify_status: "ok",
    verify_method: "pg_restore --list",
    verify_detail: "8 objects listed.",
    restore_tested_at: null,
    restore_test_status: null,
    restore_test_detail: null,
    destinations: [],
    age_seconds: 3600,
    ...extra,
  };
}

export const DUMPS: DatabaseBackup[] = [
  dump("postgresql-example_production-20260929_020000.dump", { destinations: [{ destination: "offsite-sftp", folder: "databases", pushed_at: "2026-09-29T02:01:00+00:00", verified_by: "sha256" }] }),
  dump("postgresql-example_production-20260921_020000.dump", { created: "2026-09-21T02:00:00+00:00", kind: "manual", verify_status: "failed", verify_detail: "The file does not start like a pg_dump dump." }),
];

export const OVERVIEW: DatabaseOverview = {
  database: DATABASES[0] ?? database("example_production", "postgresql"),
  display_name: "PostgreSQL",
  port: 5432,
  service: "postgresql",
  capabilities: ["dump", "metrics", "profiles", "read_only", "sql", "tables", "users"],
  support: ENGINES[0]?.support ?? { family: "postgresql", status: "unknown", message: "" },
  warnings: [],
  access: [
    { username: "example_production", host: "localhost", profile: "read_write", privileges: [], internal: false, managed: true, apps: ["shop.example.com"], password_changed_at: "2026-09-20T10:00:00+00:00" },
    { username: "postgres", host: "localhost", profile: "read_write", privileges: [], internal: true, managed: false, apps: [], password_changed_at: null },
  ],
  links: [
    {
      domain: "shop.example.com",
      engine: "postgresql",
      database: "example_production",
      username: "example_production",
      env_var: "DATABASE_URL",
      extra_vars: false,
      url: "postgresql://example_production:********@localhost:5432/example_production",
      exists: true,
      size: "46.0 MB",
      engine_version: "16.4",
      created_at: "2026-09-20T10:00:00+00:00",
    },
  ],
  backups: 2,
};

/** Every route the databases area reads, answered from the fixtures above. */
export function databaseRoutes(extra: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  const base = "/api/databases/databases/postgresql/example_production";
  return {
    ...signedInRoutes(),
    "GET /api/databases/engines": () => json(200, { engines: ENGINES }),
    "GET /api/databases/databases": () => json(200, { databases: DATABASES, total: DATABASES.length }),
    "GET /api/databases/backup-policies": () => json(200, POLICIES),
    "GET /api/databases/backups": () => json(200, { backups: DUMPS, total: DUMPS.length }),
    "GET /api/databases/exposure": () => json(200, { exposed: [] }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0 }),
    [`GET ${base}/overview`]: () => json(200, OVERVIEW),
    [`GET ${base}`]: () => json(200, DATABASES[0]),
    [`GET ${base}/metrics`]: () =>
      json(200, { engine: "postgresql", database: "example_production", size_bytes: 48_218_931, connections: 6, server_connections: 9, max_connections: 100, cache_hit_ratio: 99.87, transactions: 1_829_384, deadlocks: 0, keys: null, tables: [], series: {} }),
    "GET /api/databases/backup-policies/postgresql/example_production": () => json(200, POLICY),
    ...extra,
  };
}
