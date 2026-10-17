/**
 * Two views a central puts together from its servers' own answers: one metric of every server
 * over the same window (to compare them), and one search over the logs of every server. Each
 * server answers from what it holds; a server that is down is a row of the answer, never an error
 * of the page (see features/fleet/data.ts).
 */

import { keepPreviousData, queryOptions } from "@tanstack/react-query";

import { request } from "../client";

/** What the comparison can put side by side: the central's own list (noust.fleet.aggregate.FLEET_METRICS). */
export const COMPARE_METRICS = ["cpu", "memory", "swap", "disk", "load", "net_rx", "net_tx"] as const;
export type CompareMetric = (typeof COMPARE_METRICS)[number];

/** The windows the comparison offers. */
export const COMPARE_WINDOWS = ["1h", "6h", "24h", "7d", "30d"] as const;
export type CompareWindow = (typeof COMPARE_WINDOWS)[number];

export const fleetCompareKeys = {
  metrics: (metric: CompareMetric, window: CompareWindow) => ["fleet", "compare", "metrics", metric, window] as const,
  logs: (q: string, since: string, level: string | undefined) => ["fleet", "compare", "logs", q, since, level ?? ""] as const,
};

/** One metric of every server over a window. The last answer stays while another is read. */
export const fleetMetricsQuery = (metric: CompareMetric, window: CompareWindow) =>
  queryOptions({
    queryKey: fleetCompareKeys.metrics(metric, window),
    queryFn: ({ signal }) => request("get", "/api/fleet/metrics", { query: { metric, window }, signal }),
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
    retry: false,
  });

/** The logs of every server's applications that match a search, newest first. */
export const fleetLogsQuery = (params: { q: string; since: string; level: string | undefined; limit: number }) =>
  queryOptions({
    queryKey: fleetCompareKeys.logs(params.q, params.since, params.level),
    queryFn: ({ signal }) =>
      request("get", "/api/fleet/logs", {
        query: { q: params.q, since: params.since, ...(params.level !== undefined ? { level: params.level } : {}), limit: params.limit },
        signal,
      }),
    placeholderData: keepPreviousData,
    retry: false,
  });
