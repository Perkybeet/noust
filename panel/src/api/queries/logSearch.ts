/**
 * One search over the logs of every application the caller sees (the journal and the
 * containers at once), and how long the journal keeps what it has. Changing the retention
 * restarts journald, so it asks for sudo mode.
 */

import { keepPreviousData, queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type LogSearchResult = ResponseOf<"/api/logs/search", "get">;
export type LogSearchLine = LogSearchResult["lines"][number];
export type LogRetention = ResponseOf<"/api/logs/retention", "get">;
export type LogRetentionBody = BodyOf<"/api/logs/retention", "put">;

/** What the search asks: the words, the window, the level and the applications. */
export interface LogSearchParams {
  q: string;
  apps: readonly string[];
  since: string;
  level: string | undefined;
  limit: number;
}

export const logKeys = {
  search: (params: LogSearchParams) => ["logs", "search", params] as const,
  retention: ["logs", "retention"] as const,
};

/** A search, read again when its question changes; the last answer stays on screen meanwhile. */
export const logSearchQuery = (params: LogSearchParams) =>
  queryOptions({
    queryKey: logKeys.search(params),
    queryFn: ({ signal }) =>
      request("get", "/api/logs/search", {
        query: {
          q: params.q,
          ...(params.apps.length > 0 ? { app: [...params.apps] } : {}),
          since: params.since,
          ...(params.level !== undefined ? { level: params.level } : {}),
          limit: params.limit,
        },
        signal,
      }),
    placeholderData: keepPreviousData,
  });

export const logRetentionQuery = () =>
  queryOptions({
    queryKey: logKeys.retention,
    queryFn: ({ signal }) => request("get", "/api/logs/retention", { signal }),
  });

/** Keeps the logs this long and at most this big; what is left out stays. Needs sudo mode. */
export function saveLogRetention(body: LogRetentionBody) {
  return request("put", "/api/logs/retention", { body });
}

/** Goes back to journald's own defaults. Needs sudo mode. */
export function clearLogRetention() {
  return request("delete", "/api/logs/retention");
}
