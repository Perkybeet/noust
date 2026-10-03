/**
 * Reads for the databases area: engines, databases and what each one holds (its tables, its
 * rows, its keys), its accounts, its backups and their policy, how to reach it and how it is
 * doing. Every read is a GET the central's proxy forwards to a node like any other, so every
 * page works on a node as it does here. Mutations live beside the pages that call them, in
 * `features/databases`, the same split `features/apps` uses.
 *
 * Keys: everything about one database sits under `databaseKeys.database(engine, name)`, so
 * what a finished job changed is read again with one invalidation.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, QueryOf, ResponseOf } from "../client";

export type EngineList = ResponseOf<"/api/databases/engines", "get">;
export type Engine = EngineList["engines"][number];
export type SupportNotice = NonNullable<Engine["support"]>;
export type EngineCatalog = ResponseOf<"/api/databases/engines/catalog", "get">;
export type FlavourChoice = EngineCatalog["flavours"][number];
export type VersionChoice = NonNullable<FlavourChoice["versions"]>[number];
export type EngineSettings = ResponseOf<"/api/databases/engines/{engine}/settings", "get">;
export type EngineSetting = EngineSettings["settings"][number];
export type EngineSettingsOutcome = ResponseOf<"/api/databases/engines/{engine}/settings", "put">;
export type DatabaseList = ResponseOf<"/api/databases/databases", "get">;
export type Database = DatabaseList["databases"][number];
export type DatabaseOverview = ResponseOf<"/api/databases/databases/{engine}/{name}/overview", "get">;
export type AccessEntry = DatabaseOverview["access"][number];
export type DatabaseLink = DatabaseOverview["links"][number];
export type UserList = ResponseOf<"/api/databases/users/{engine}", "get">;
export type DatabaseUser = UserList["users"][number];
export type DatabaseBackupList = ResponseOf<"/api/databases/backups", "get">;
export type DatabaseBackup = DatabaseBackupList["backups"][number];
export type PolicyList = ResponseOf<"/api/databases/backup-policies", "get">;
export type BackupPolicy = ResponseOf<"/api/databases/backup-policies/{engine}/{database}", "get">;
export type PolicyBody = BodyOf<"/api/databases/backup-policies/{engine}/{database}", "put">;
export type RemoteDumpList = ResponseOf<"/api/databases/backups/remote", "get">;
export type RemoteDump = RemoteDumpList["dumps"][number];
export type Exposure = ResponseOf<"/api/databases/exposure", "get">;
export type ExposedPort = Exposure["exposed"][number];
export type ConnectInfo = ResponseOf<"/api/databases/databases/{engine}/{name}/connect", "get">;
export type ConnectParams = NonNullable<QueryOf<"/api/databases/databases/{engine}/{name}/connect", "get">>;
export type SchemaList = ResponseOf<"/api/databases/databases/{engine}/{name}/schemas", "get">;
export type RelationList = ResponseOf<"/api/databases/databases/{engine}/{name}/relations", "get">;
export type RelationSummary = RelationList["relations"][number];
export type RelationDetail = ResponseOf<"/api/databases/databases/{engine}/{name}/relation", "get">;
export type RelationColumn = RelationDetail["columns"][number];
export type RowsPage = ResponseOf<"/api/databases/databases/{engine}/{name}/rows", "get">;
export type RowsParams = NonNullable<QueryOf<"/api/databases/databases/{engine}/{name}/rows", "get">>;
export type TableRow = RowsPage["rows"][number];
export type RowChange = ResponseOf<"/api/databases/databases/{engine}/{name}/rows", "patch">;
export type KeysPage = ResponseOf<"/api/databases/databases/{engine}/{name}/keys", "get">;
export type RedisKey = KeysPage["keys"][number];
export type KeyValue = ResponseOf<"/api/databases/databases/{engine}/{name}/key", "get">;
export type DatabaseMetrics = ResponseOf<"/api/databases/databases/{engine}/{name}/metrics", "get">;
export type SlowQueries = ResponseOf<"/api/databases/databases/{engine}/{name}/slow-queries", "get">;
export type QueryResult = ResponseOf<"/api/databases/query", "post">;
export type QueryBody = BodyOf<"/api/databases/query", "post">;
export type ExplainResult = ResponseOf<"/api/databases/query/explain", "post">;
export type HistoryList = ResponseOf<"/api/databases/console/history", "get">;
export type HistoryEntry = HistoryList["entries"][number];
export type SavedList = ResponseOf<"/api/databases/console/saved", "get">;
export type SavedQuery = SavedList["queries"][number];
export type AppDatabases = ResponseOf<"/api/apps/{domain}/databases", "get">;
export type ProvisioningPlan = ResponseOf<"/api/databases/provisioning/plan", "get">;
export type JobAccepted = ResponseOf<"/api/databases/backups", "post">;

export const databaseKeys = {
  all: ["databases"] as const,
  engines: ["databases", "engines"] as const,
  engineLogs: (engine: string) => ["databases", "engines", engine, "logs"] as const,
  engineSettings: (engine: string) => ["databases", "engines", engine, "settings"] as const,
  /** What can be installed here: flavours and versions, which change only when one is installed. */
  installCatalog: ["databases", "engines", "catalog"] as const,
  /** Every list, whatever engine it is filtered to: the prefix of `list`. */
  lists: ["databases", "list"] as const,
  list: (engine: string | null) => ["databases", "list", { engine }] as const,
  /** Everything about one database: its overview, access, data, backups, metrics. */
  database: (engine: string, name: string) => ["databases", "db", engine, name] as const,
  overview: (engine: string, name: string) => ["databases", "db", engine, name, "overview"] as const,
  access: (engine: string, name: string) => ["databases", "db", engine, name, "access"] as const,
  connect: (engine: string, name: string, params: ConnectParams) => ["databases", "db", engine, name, "connect", params] as const,
  catalog: (engine: string, name: string) => ["databases", "db", engine, name, "catalog"] as const,
  relation: (engine: string, name: string, schema: string, relation: string) =>
    ["databases", "db", engine, name, "relation", schema, relation] as const,
  rows: (engine: string, name: string, params: RowsParams) => ["databases", "db", engine, name, "rows", params] as const,
  key: (engine: string, name: string, key: string) => ["databases", "db", engine, name, "key", key] as const,
  metrics: (engine: string, name: string) => ["databases", "db", engine, name, "metrics"] as const,
  slowQueries: (engine: string, name: string) => ["databases", "db", engine, name, "slow"] as const,
  users: (engine: string) => ["databases", "users", engine] as const,
  /** Every dump list: the prefix of `backups`. */
  allBackups: ["databases", "backups"] as const,
  backups: (engine: string | null, database: string | null) => ["databases", "backups", { engine, database }] as const,
  remote: (engine: string, database: string, destination: string) =>
    ["databases", "backups", "remote", engine, database, destination] as const,
  /** Every policy read: the prefix of `policy`. */
  policies: ["databases", "policies"] as const,
  policy: (engine: string, database: string) => ["databases", "policies", engine, database] as const,
  exposure: ["databases", "exposure"] as const,
  history: (engine: string, database: string) => ["databases", "console", "history", engine, database] as const,
  saved: (engine: string, database: string) => ["databases", "console", "saved", engine, database] as const,
  app: (domain: string) => ["databases", "app", domain] as const,
  plan: (domain: string, engine: string) => ["databases", "plan", domain, engine] as const,
};

export const enginesQuery = () =>
  queryOptions({
    queryKey: databaseKeys.engines,
    queryFn: ({ signal }) => request("get", "/api/databases/engines", { signal }),
  });

export const engineLogsQuery = (engine: string) =>
  queryOptions({
    queryKey: databaseKeys.engineLogs(engine),
    queryFn: ({ signal }) => request("get", "/api/databases/engines/{engine}/logs", { params: { engine }, query: { lines: 300 }, signal }),
  });

/** The flavours and versions this server can install, and why the others cannot be. */
export const engineCatalogQuery = () =>
  queryOptions({
    queryKey: databaseKeys.installCatalog,
    queryFn: ({ signal }) => request("get", "/api/databases/engines/catalog", { signal }),
  });

/** An engine's settings: what it runs with, what Noust's file sets, and the advice for this server. */
export const engineSettingsQuery = (engine: string) =>
  queryOptions({
    queryKey: databaseKeys.engineSettings(engine),
    queryFn: ({ signal }) => request("get", "/api/databases/engines/{engine}/settings", { params: { engine }, signal }),
  });

export const databasesQuery = (engine: string | null = null) =>
  queryOptions({
    queryKey: databaseKeys.list(engine),
    queryFn: ({ signal }) => request("get", "/api/databases/databases", { query: engine === null ? {} : { engine }, signal }),
  });

export const databaseOverviewQuery = (engine: string, name: string) =>
  queryOptions({
    queryKey: databaseKeys.overview(engine, name),
    queryFn: ({ signal }) => request("get", "/api/databases/databases/{engine}/{name}/overview", { params: { engine, name }, signal }),
  });

export const accessQuery = (engine: string, name: string) =>
  queryOptions({
    queryKey: databaseKeys.access(engine, name),
    queryFn: ({ signal }) => request("get", "/api/databases/databases/{engine}/{name}/access", { params: { engine, name }, signal }),
  });

export const connectQuery = (engine: string, name: string, params: ConnectParams) =>
  queryOptions({
    queryKey: databaseKeys.connect(engine, name, params),
    queryFn: ({ signal }) => request("get", "/api/databases/databases/{engine}/{name}/connect", { params: { engine, name }, query: params, signal }),
  });

/** Schemas and relations together: the explorer's tree is one read of the catalog. */
export const catalogQuery = (engine: string, name: string) =>
  queryOptions({
    queryKey: databaseKeys.catalog(engine, name),
    queryFn: async ({ signal }) => {
      const [schemas, relations] = await Promise.all([
        request("get", "/api/databases/databases/{engine}/{name}/schemas", { params: { engine, name }, signal }),
        request("get", "/api/databases/databases/{engine}/{name}/relations", { params: { engine, name }, signal }),
      ]);
      return { schemas: schemas.schemas, relations: relations.relations };
    },
  });

export const relationQuery = (engine: string, name: string, schema: string, relation: string) =>
  queryOptions({
    queryKey: databaseKeys.relation(engine, name, schema, relation),
    queryFn: ({ signal }) =>
      request("get", "/api/databases/databases/{engine}/{name}/relation", { params: { engine, name }, query: { schema, relation }, signal }),
  });

export const rowsQuery = (engine: string, name: string, params: RowsParams) =>
  queryOptions({
    queryKey: databaseKeys.rows(engine, name, params),
    queryFn: ({ signal }) => request("get", "/api/databases/databases/{engine}/{name}/rows", { params: { engine, name }, query: params, signal }),
  });

export const keyQuery = (engine: string, name: string, key: RedisKey) =>
  queryOptions({
    queryKey: databaseKeys.key(engine, name, key.hex ?? key.key),
    queryFn: ({ signal }) =>
      request("get", "/api/databases/databases/{engine}/{name}/key", {
        params: { engine, name },
        query: key.hex != null ? { hex: key.hex } : { key: key.key },
        signal,
      }),
  });

export const databaseMetricsQuery = (engine: string, name: string) =>
  queryOptions({
    queryKey: databaseKeys.metrics(engine, name),
    queryFn: ({ signal }) => request("get", "/api/databases/databases/{engine}/{name}/metrics", { params: { engine, name }, signal }),
  });

export const slowQueriesQuery = (engine: string, name: string) =>
  queryOptions({
    queryKey: databaseKeys.slowQueries(engine, name),
    queryFn: ({ signal }) => request("get", "/api/databases/databases/{engine}/{name}/slow-queries", { params: { engine, name }, signal }),
  });

export const databaseUsersQuery = (engine: string) =>
  queryOptions({
    queryKey: databaseKeys.users(engine),
    queryFn: ({ signal }) => request("get", "/api/databases/users/{engine}", { params: { engine }, signal }),
  });

/** Dumps on disk, for one database or a whole engine, with what verification found. */
export const databaseBackupsQuery = (engine: string | null = null, database: string | null = null) =>
  queryOptions({
    queryKey: databaseKeys.backups(engine, database),
    queryFn: ({ signal }) =>
      request("get", "/api/databases/backups", {
        query: { ...(engine === null ? {} : { engine }), ...(database === null ? {} : { database }) },
        signal,
      }),
  });

export const remoteDumpsQuery = (engine: string, database: string, destination: string) =>
  queryOptions({
    queryKey: databaseKeys.remote(engine, database, destination),
    queryFn: ({ signal }) => request("get", "/api/databases/backups/remote", { query: { engine, database, destination }, signal }),
    enabled: destination !== "",
  });

/** Every policy, and the databases no enabled policy covers. */
export const policiesQuery = () =>
  queryOptions({
    queryKey: databaseKeys.policies,
    queryFn: ({ signal }) => request("get", "/api/databases/backup-policies", { signal }),
  });

export const policyQuery = (engine: string, database: string) =>
  queryOptions({
    queryKey: databaseKeys.policy(engine, database),
    queryFn: ({ signal }) =>
      request("get", "/api/databases/backup-policies/{engine}/{database}", { params: { engine, database }, signal }),
  });

export const exposureQuery = () =>
  queryOptions({
    queryKey: databaseKeys.exposure,
    queryFn: ({ signal }) => request("get", "/api/databases/exposure", { signal }),
  });

export const historyQuery = (engine: string, database: string) =>
  queryOptions({
    queryKey: databaseKeys.history(engine, database),
    queryFn: ({ signal }) => request("get", "/api/databases/console/history", { query: { engine, database, limit: 100 }, signal }),
  });

export const savedQueriesQuery = (engine: string, database: string) =>
  queryOptions({
    queryKey: databaseKeys.saved(engine, database),
    queryFn: ({ signal }) => request("get", "/api/databases/console/saved", { query: { engine, database }, signal }),
  });

export const appDatabasesQuery = (domain: string) =>
  queryOptions({
    queryKey: databaseKeys.app(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/databases", { params: { domain }, signal }),
  });

/** What creating a database for an application would write; nothing is created. */
export const provisioningPlanQuery = (domain: string, engine: string) =>
  queryOptions({
    queryKey: databaseKeys.plan(domain, engine),
    queryFn: ({ signal }) => request("get", "/api/databases/provisioning/plan", { query: { domain, engine }, signal }),
    staleTime: 30_000,
  });
