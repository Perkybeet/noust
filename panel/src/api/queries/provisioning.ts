/**
 * What a new database for an application can be created on: each engine flavour with the
 * version that runs and the versions on offer (`GET /api/databases/provisioning/choices`), and
 * what creating one on a chosen version would write (`.../plan`). Nothing here changes anything.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

export type ProvisioningChoices = ResponseOf<"/api/databases/provisioning/choices", "get">;
export type EngineChoice = ProvisioningChoices["choices"][number];
export type VersionOffer = EngineChoice["versions"][number];
export type ProvisioningPlan = ResponseOf<"/api/databases/provisioning/plan", "get">;

export const provisioningKeys = {
  choices: ["databases", "provisioning", "choices"] as const,
  plan: (domain: string, engine: string, version: string | null) => ["databases", "provisioning", "plan", domain, engine, version] as const,
};

/** The flavours a database can be created on, and each one's versions. */
export const provisioningChoicesQuery = () =>
  queryOptions({
    queryKey: provisioningKeys.choices,
    queryFn: ({ signal }) => request("get", "/api/databases/provisioning/choices", { signal }),
    staleTime: 30_000,
  });

/** What creating a database for an application on an engine and version would write. */
export const provisioningPlanFor = (domain: string, engine: string, version: string | null) =>
  queryOptions({
    queryKey: provisioningKeys.plan(domain, engine, version),
    queryFn: ({ signal }) =>
      request("get", "/api/databases/provisioning/plan", { query: { domain, engine, ...(version !== null ? { version } : {}) }, signal }),
    staleTime: 30_000,
  });
