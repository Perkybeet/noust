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
  EngineCatalog,
  EngineSettings,
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
    kind: "host",
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
    kind: "host",
  },
  { name: "redis", display_name: "Redis", installed: true, version: "7.0.15", running: false, port: 6379, service: "redis-server", capabilities: ["dump", "keys", "metrics"], support: null, warnings: [], stored_account: false, kind: "host" },
  { name: "mongodb", display_name: "MongoDB", installed: false, version: null, running: false, port: 27017, service: "mongod", capabilities: ["documents", "dump"], support: null, warnings: [], stored_account: false, kind: "host" },
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

/** PostgreSQL in a Compose stack, as discovery lists it: its key carries the project and service. */
export const CONTAINER_ENGINE: Engine = {
  name: "postgresql@proggest.postgres",
  display_name: "PostgreSQL",
  installed: true,
  version: "16.4",
  running: true,
  port: 5433,
  service: null,
  capabilities: ["dump", "metrics", "profiles", "read_only", "sql", "tables", "users"],
  support: null,
  warnings: [],
  stored_account: false,
  kind: "container",
  container: "proggest-postgres-1",
  project: "proggest",
  compose_service: "postgres",
  image: "postgres:16-alpine",
  app: "proggest.es",
  access: "limited",
};

/** The database inside that container, untracked, and a host database an application's .env names. */
export const CONTAINER_DATABASE: Database = database("proggest", CONTAINER_ENGINE.name, { tracked: false, size: "30.0 MB", engine_version: "16.4" });

export const DETECTED_DATABASE: Database = database("example_staging", "postgresql", { tracked: false, size: "8.7 MB", detected_apps: ["docs.example.com"] });

/** What this server can install: PostgreSQL in several versions, MariaDB blocked beside MySQL. */
export const CATALOG: EngineCatalog = {
  distribution: { id: "ubuntu", codename: "noble", name: "Ubuntu 24.04.1 LTS", known: true },
  apt: true,
  flavours: [
    {
      flavour: "postgresql",
      engine: "postgresql",
      display_name: "PostgreSQL",
      installed: false,
      installable: true,
      blocked: null,
      reason: null,
      versions: [
        { version: "15", source: "upstream", default: false },
        { version: "16", source: "distribution", default: true },
        { version: "17", source: "upstream", default: false },
      ],
    },
    { flavour: "mysql", engine: "mysql", display_name: "MySQL", installed: true, installable: false, blocked: "installed", reason: "MySQL is installed.", versions: [{ version: "8.0", source: "distribution", default: true }] },
    {
      flavour: "mariadb",
      engine: "mysql",
      display_name: "MariaDB",
      installed: false,
      installable: false,
      blocked: "conflict",
      reason: "MySQL is installed, and MariaDB cannot run beside it: they share port 3306, their packages and /var/lib/mysql.",
      versions: [{ version: "10.11", source: "distribution", default: true }],
    },
    {
      flavour: "mongodb",
      engine: "mongodb",
      display_name: "MongoDB",
      installed: false,
      installable: true,
      blocked: null,
      reason: null,
      versions: [
        { version: "7.0", source: "upstream", default: true },
        { version: "8.0", source: "upstream", default: false },
      ],
    },
  ],
};

/** PostgreSQL's settings on a 2 GiB, 2-processor server. */
export const SETTINGS: EngineSettings = {
  engine: "postgresql",
  display_name: "PostgreSQL",
  file: "/etc/postgresql/16/main/conf.d/90-noust.conf",
  running: true,
  memory_bytes: 2 * 1024 ** 3,
  cpus: 2,
  settings: [
    {
      key: "listen_addresses",
      kind: "addresses",
      unit: null,
      description: "The addresses PostgreSQL accepts connections on.",
      current: "localhost",
      configured: null,
      recommended: "localhost",
      restart: true,
      choices: [],
      minimum: null,
      maximum: null,
      listen: true,
      editable: true,
      locked_reason: null,
      source: null,
    },
    {
      key: "port",
      kind: "port",
      unit: null,
      description: "The TCP port.",
      current: "5432",
      configured: null,
      recommended: null,
      restart: true,
      choices: [],
      minimum: 1024,
      maximum: 65535,
      listen: false,
      editable: false,
      locked_reason: "Noust's own client reaches the server on its default port.",
      source: null,
    },
    {
      key: "max_connections",
      kind: "integer",
      unit: null,
      description: "How many connections at once.",
      current: "100",
      configured: null,
      recommended: "100",
      restart: true,
      choices: [],
      minimum: 10,
      maximum: 10000,
      listen: false,
      editable: true,
      locked_reason: null,
      source: null,
    },
    {
      key: "shared_buffers",
      kind: "size",
      unit: "MB",
      description: "Memory PostgreSQL keeps for its own cache.",
      current: "128MB",
      configured: null,
      recommended: "512MB",
      restart: true,
      choices: [],
      minimum: null,
      maximum: null,
      listen: false,
      editable: true,
      locked_reason: null,
      source: null,
    },
    {
      key: "log_min_duration_statement",
      kind: "duration_ms",
      unit: "ms",
      description: "Statements slower than this are logged.",
      current: "-1",
      configured: "500",
      recommended: null,
      restart: false,
      choices: [],
      minimum: -1,
      maximum: null,
      listen: false,
      editable: true,
      locked_reason: null,
      source: null,
    },
  ],
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
    "GET /api/databases/exposure": () => json(200, { exposed: [], firewalled: [] }),
    "GET /api/databases/engines/catalog": () => json(200, CATALOG),
    "GET /api/databases/engines/postgresql/settings": () => json(200, SETTINGS),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0 }),
    [`GET ${base}/overview`]: () => json(200, OVERVIEW),
    [`GET ${base}`]: () => json(200, DATABASES[0]),
    [`GET ${base}/metrics`]: () =>
      json(200, { engine: "postgresql", database: "example_production", size_bytes: 48_218_931, connections: 6, server_connections: 9, max_connections: 100, cache_hit_ratio: 99.87, transactions: 1_829_384, deadlocks: 0, keys: null, tables: [], series: {} }),
    "GET /api/databases/backup-policies/postgresql/example_production": () => json(200, POLICY),
    ...extra,
  };
}
