import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

/** What Noust watches by itself and by which channels it says so: the opening of Alerts. */
export type Watching = ResponseOf<"/api/alerts/watching", "get">;
export type Watch = NonNullable<Watching["watches"]>[number];

export const watchingKeys = {
  all: ["alerts", "watching"] as const,
};

/** The server's own answer, which also says what a change of a switch or a channel does to it: it is read again after one. */
export const watchingQuery = () =>
  queryOptions({
    queryKey: watchingKeys.all,
    queryFn: ({ signal }) => request("get", "/api/alerts/watching", { signal }),
    refetchInterval: 60_000,
  });

/** The server a node of an older Noust is: it has no such route, and the page says so instead of a failure. */
export const WATCHING_CAPABILITY = "/api/alerts/watching";
