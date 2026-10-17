/**
 * The identity providers that provision accounts (`/api/auth/scim`): which sources there are,
 * what the accounts they create start as, and the token each authenticates with. A token is in
 * the answer to the request that issued it and nowhere else; every rule is the server's.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type ScimSources = ResponseOf<"/api/auth/scim/sources", "get">;
export type ScimSource = ScimSources["sources"][number];
export type ScimSourceSettings = BodyOf<"/api/auth/scim/sources/{name}", "put">;
export type ScimSaved = ResponseOf<"/api/auth/scim/sources/{name}", "put">;
export type ScimUsers = ResponseOf<"/api/auth/scim/sources/{name}/users", "get">;

export const scimKeys = {
  all: ["auth", "scim"] as const,
  sources: ["auth", "scim", "sources"] as const,
};

/** The sources, with how many people each provisioned, and the address to give the provider. */
export const scimSourcesQuery = () =>
  queryOptions({
    queryKey: scimKeys.sources,
    queryFn: ({ signal }) => request("get", "/api/auth/scim/sources", { signal }),
  });

/** Creates a source or changes the settings named; what is left out stays. Needs "Confirm it's you". */
export function saveScimSource(name: string, body: ScimSourceSettings) {
  return request("put", "/api/auth/scim/sources/{name}", { params: { name }, body });
}

/** Removes a source: its token stops working and its roster goes; the accounts stay. */
export function removeScimSource(name: string) {
  return request("delete", "/api/auth/scim/sources/{name}", { params: { name } });
}
