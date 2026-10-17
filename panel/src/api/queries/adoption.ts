import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

export type NativeSites = ResponseOf<"/api/apps/native-sites", "get">;
export type NativeSite = NativeSites["sites"][number];
export type SiteAdoption = ResponseOf<"/api/apps/{domain}/adoption", "get">;
export type SiteAdoptionPlan = ResponseOf<"/api/apps/adopt-site", "post">;

/**
 * Under the app's own prefix like everything read about one app, so a job on the app finishing
 * refreshes it with the rest. The sites that could be adopted belong to no app.
 */
export const adoptionKeys = {
  sites: ["native-sites"] as const,
  of: (domain: string) => ["app", domain, "adoption"] as const,
};

/** The sites on the server that no application records, and what each one is. Changes nothing. */
export const nativeSitesQuery = () =>
  queryOptions({
    queryKey: adoptionKeys.sites,
    queryFn: ({ signal }) => request("get", "/api/apps/native-sites", { signal }),
  });

/** What an adopted application is: the operator's unit, site and directory. */
export const adoptionQuery = (domain: string) =>
  queryOptions({
    queryKey: adoptionKeys.of(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/adoption", { params: { domain }, signal }),
  });
