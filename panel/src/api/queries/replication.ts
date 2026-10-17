import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

export type Replication = ResponseOf<"/api/apps/{domain}/replication", "get">;
export type ReplicationFinding = Replication["findings"][number];

/** Under the app's own prefix like everything read about one app. */
export const replicationKey = (domain: string) => ["app", domain, "replication"] as const;

/**
 * Whether the app could run on more than one server, as this server can tell: what a second one
 * would get wrong or do twice. Changes nothing and reaches no other server.
 */
export const replicationQuery = (domain: string) =>
  queryOptions({
    queryKey: replicationKey(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/replication", { params: { domain }, signal }),
  });
