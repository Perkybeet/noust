import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

export type ZeroDowntime = ResponseOf<"/api/apps/{domain}/zero-downtime", "get">;
export type ZeroDowntimeInstance = NonNullable<ZeroDowntime["instances"]>[number];

/**
 * Under the app's own prefix (`["app", domain, ...]`), so a job on the app finishing - the
 * switch itself, a deploy that moved traffic to the other colour - refreshes it with the rest.
 */
export const zeroDowntimeKey = (domain: string) => ["app", domain, "zero-downtime"] as const;

/**
 * Whether the app activates without a cut: when on, both instances and the one serving; when
 * off, whether it could be turned on and, if not, why. Reads systemd and nginx; changes nothing.
 */
export const zeroDowntimeQuery = (domain: string) =>
  queryOptions({
    queryKey: zeroDowntimeKey(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/zero-downtime", { params: { domain }, signal }),
  });
