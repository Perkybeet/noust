import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

/** What needs this person, as one list the server joins and ranks: the bell, Home and the sidebar read it. */
export type AttentionList = ResponseOf<"/api/attention", "get">;

export const attentionKeys = {
  all: ["attention"] as const,
  own: ["attention", "own"] as const,
};

/** How often the list reads itself again; the stream's events read it sooner and opening the bell reads it at once. */
export const ATTENTION_POLL_MS = 60_000;

/** Everything that needs this person: the server's own list and what is theirs (requests, a newer Noust, recipes behind). */
export const attentionQuery = () =>
  queryOptions({
    queryKey: attentionKeys.all,
    queryFn: ({ signal }) => request("get", "/api/attention", { signal }),
    refetchInterval: ATTENTION_POLL_MS,
  });

/**
 * Only what is this person's own, without the server's list: what a central adds to the lists of the servers
 * it manages, each of which says its own through its Overview.
 */
export const ownAttentionQuery = () =>
  queryOptions({
    queryKey: attentionKeys.own,
    queryFn: ({ signal }) => request("get", "/api/attention", { query: { only: "own" }, signal }),
    refetchInterval: ATTENTION_POLL_MS,
  });
