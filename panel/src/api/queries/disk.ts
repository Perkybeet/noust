import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

export type AppDisk = ResponseOf<"/api/apps/{domain}/disk", "get">;
export type DiskUsage = NonNullable<AppDisk["usage"]>;
export type DiskPart = DiskUsage["parts"][number];

/** Under the app's own prefix like everything read about one app. */
export const diskKey = (domain: string) => ["app", domain, "disk"] as const;

/**
 * What the app takes of the disk as it was last measured, the limit its deploys are held to and
 * whether its filesystem could hold a kernel quota. Measures nothing: that is a job.
 */
export const diskQuery = (domain: string) =>
  queryOptions({
    queryKey: diskKey(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/disk", { params: { domain }, signal }),
  });
