import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

/** Everything the Overview shows, in one answer: the key figures, attention and activity. */
export type Overview = ResponseOf<"/api/overview", "get">;
export type OverviewAttentionItem = NonNullable<Overview["attention"]>[number];
export type OverviewAttentionReason = OverviewAttentionItem["reasons"][number];
export type OverviewActivityEntry = NonNullable<Overview["activity"]>[number];

export const overviewKeys = {
  all: ["overview"] as const,
};

/** How often the Overview reads itself again; the server keeps an answer about as long. */
export const OVERVIEW_POLL_MS = 15_000;

export const overviewQuery = () =>
  queryOptions({
    queryKey: overviewKeys.all,
    queryFn: ({ signal }) => request("get", "/api/overview", { signal }),
    refetchInterval: OVERVIEW_POLL_MS,
  });
