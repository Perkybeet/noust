/**
 * Moving this server to another one: what it would take (read here), and the copies a
 * preparation made (the result of the job that makes them). Starting the preparation is a
 * mutation beside the dialog that asks for it.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

export type MigrationPlan = ResponseOf<"/api/server/migration/plan", "get">;

export const serverMigrationKeys = {
  plan: (destination: string) => ["server", "migration", "plan", destination] as const,
};

/** What moving would take and what stops it, for a destination. Changes nothing. */
export const migrationPlanQuery = (destination: string) =>
  queryOptions({
    queryKey: serverMigrationKeys.plan(destination),
    queryFn: ({ signal }) => request("get", "/api/server/migration/plan", { query: { destination }, signal }),
    retry: false,
    staleTime: 0,
  });
