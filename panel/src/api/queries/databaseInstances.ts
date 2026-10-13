/**
 * Reads for the engines Noust runs itself in containers beside the host's (instances), and for
 * moving a database or a whole engine to another one. Mutations live beside the dialogs that
 * call them, like the rest of the databases area.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

export type InstanceList = ResponseOf<"/api/databases/instances", "get">;
export type DbInstance = InstanceList["instances"][number];
export type InstanceBudget = InstanceList["budget"];
export type InstanceCatalog = ResponseOf<"/api/databases/instances/catalog", "get">;
export type CatalogEngine = InstanceCatalog["engines"][number];
export type MigrationList = ResponseOf<"/api/databases/instances/migrations", "get">;
export type Migration = MigrationList["migrations"][number];
export type MigratePlan = ResponseOf<"/api/databases/instances/migrate/plan", "post">;
export type MovePlan = ResponseOf<"/api/databases/moves/plan", "post">;

export const instanceKeys = {
  all: ["databases", "instances"] as const,
  list: ["databases", "instances", "list"] as const,
  catalog: ["databases", "instances", "catalog"] as const,
  migrations: ["databases", "instances", "migrations"] as const,
};

/** The instances Noust runs, and how the server's memory is divided among them. */
export const instancesQuery = () =>
  queryOptions({
    queryKey: instanceKeys.list,
    queryFn: ({ signal }) => request("get", "/api/databases/instances", { signal }),
  });

/** What Noust can run as an instance: each engine, its versions and their digests. It changes with Noust. */
export const instanceCatalogQuery = () =>
  queryOptions({
    queryKey: instanceKeys.catalog,
    queryFn: ({ signal }) => request("get", "/api/databases/instances/catalog", { signal }),
    staleTime: 10 * 60_000,
  });

/** The moves of an engine to a newer version, newest first, and which can still be rolled back. */
export const migrationsQuery = () =>
  queryOptions({
    queryKey: instanceKeys.migrations,
    queryFn: ({ signal }) => request("get", "/api/databases/instances/migrations", { signal }),
  });
