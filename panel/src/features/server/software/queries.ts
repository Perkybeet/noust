/**
 * What the Software section reads and does (4.2, item 118): the registry of what the server is made of, what
 * removing one piece would do before anything is touched, and installing and removing, which are jobs.
 *
 * Everything is under `["server", ...]`, which is what the Server area reads again when a job of its own ends:
 * installing or removing changes what this list says, and nothing here has to remember to ask for it.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../../../api/client";
import type { ResponseOf } from "../../../api/client";

export type SoftwareList = ResponseOf<"/api/server/software", "get">;
export type SoftwareItem = NonNullable<SoftwareList["items"]>[number];
export type Dependent = NonNullable<SoftwareItem["dependents"]>[number];
export type Impact = ResponseOf<"/api/server/software/{component}/impact", "get">;
export type Blocker = NonNullable<Impact["blockers"]>[number];

/** The capability a Noust older than 4.2 lacks: the section says so instead of failing on every call. */
export const SOFTWARE_CAPABILITY = "GET /api/server/software";

export const softwareKeys = {
  list: ["server", "software"] as const,
  impact: (id: string) => ["server", "software", "impact", id] as const,
};

/**
 * Every piece of software Noust manages. The server keeps its answer for a minute; the page asks it again with
 * `refresh` when the person says "Check again" and whenever a job of the Server area ends (the key's prefix).
 */
export const softwareQuery = () =>
  queryOptions({
    queryKey: softwareKeys.list,
    queryFn: ({ signal }) => request("get", "/api/server/software", { signal }),
    staleTime: 30_000,
  });

/** Asks the machine again, whatever the age of what the server holds, and hands the answer to the cache. */
export function recheckSoftware(): Promise<SoftwareList> {
  return request("get", "/api/server/software", { query: { refresh: true } });
}

/**
 * What removing a piece would do and whether it may, changing nothing. It asks the package manager for a
 * simulation, so it takes about a second; never kept, because it is a look at the machine as it is now.
 */
export const impactQuery = (id: string) =>
  queryOptions({
    queryKey: softwareKeys.impact(id),
    queryFn: ({ signal }) => request("get", "/api/server/software/{component}/impact", { params: { component: id }, signal }),
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });

/** Queues an install (a job). Sudo mode; `epel` is the person's yes to enabling EPEL where the piece comes from it. */
export function installSoftware(id: string, epel = false): Promise<ResponseOf<"/api/server/software/{component}/install", "post">> {
  return request("post", "/api/server/software/{component}/install", { params: { component: id }, body: { epel } });
}

/** Queues a removal (a job). Sudo mode; never a purge. `confirmUnknownOrigin` is the yes to something Noust did not install. */
export function removeSoftware(id: string, confirmUnknownOrigin: boolean): Promise<ResponseOf<"/api/server/software/{component}/remove", "post">> {
  return request("post", "/api/server/software/{component}/remove", { params: { component: id }, body: { confirm_unknown_origin: confirmUnknownOrigin } });
}
