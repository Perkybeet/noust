/**
 * What the proxy's access log says about an application: requests and how they ended by the
 * hour (by the day beyond two days), bytes served, and the pages, networks, status codes and
 * referrers that appear most. Query strings are never kept and a client is its network, so
 * nothing here identifies a person.
 */

import { keepPreviousData, queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

export type AppTraffic = ResponseOf<"/api/apps/{domain}/traffic", "get">;
export type TrafficPoint = AppTraffic["points"][number];

/** The windows the traffic can be read over. */
export const TRAFFIC_WINDOWS = ["24h", "7d", "30d", "90d"] as const;
export type TrafficWindow = (typeof TRAFFIC_WINDOWS)[number];

export const trafficKeys = {
  app: (domain: string, window: TrafficWindow) => ["traffic", domain, window] as const,
};

/** One application's traffic over a window; the last answer stays on screen while a new window is read. */
export const appTrafficQuery = (domain: string, window: TrafficWindow) =>
  queryOptions({
    queryKey: trafficKeys.app(domain, window),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/traffic", { params: { domain }, query: { window }, signal }),
    placeholderData: keepPreviousData,
    refetchInterval: 5 * 60_000,
  });
