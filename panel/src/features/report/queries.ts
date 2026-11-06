import { queryOptions } from "@tanstack/react-query";

import { request } from "../../api/client";
import type { ResponseOf } from "../../api/client";

export type SupportReport = ResponseOf<"/api/system/support-report", "get">;

export const reportKeys = {
  support: (hideDomains: boolean) => ["report", "support", { hideDomains }] as const,
};

/**
 * The support report, collected for a report of a problem. It is made fresh each time (it is what the server
 * says of itself now) and is not kept after the dialog closes: it is a copy of the server, and nothing keeps it
 * longer than the person needs to read it.
 */
export const supportReportQuery = (hideDomains: boolean) =>
  queryOptions({
    queryKey: reportKeys.support(hideDomains),
    queryFn: ({ signal }) => request("get", "/api/system/support-report", { query: { hide_domains: hideDomains }, signal }),
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
