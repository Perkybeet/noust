import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

/** The end of a site's access or error log, as the web server wrote it, secrets masked. */
export type SiteLog = ResponseOf<"/api/apps/{domain}/site-logs", "get">;

export type SiteLogKind = "access" | "error";

export const siteLogKeys = {
  site: (domain: string) => ["apps", domain, "site-logs"] as const,
  read: (domain: string, kind: SiteLogKind, contains: string) => ["apps", domain, "site-logs", kind, contains] as const,
};

/**
 * The last lines of one of a site's logs, optionally only those with some text. Read again
 * every few seconds for as long as it is on screen.
 */
export const siteLogQuery = (domain: string, kind: SiteLogKind, contains: string) =>
  queryOptions({
    queryKey: siteLogKeys.read(domain, kind, contains),
    queryFn: ({ signal }) =>
      request("get", "/api/apps/{domain}/site-logs", {
        params: { domain },
        query: { kind, lines: 300, ...(contains !== "" ? { contains } : {}) },
        signal,
      }),
    refetchInterval: 5_000,
  });
