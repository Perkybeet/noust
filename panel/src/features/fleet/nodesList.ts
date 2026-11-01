/**
 * The list of servers this central manages and the keys of its queries, on their own: the fleet's route asks for the list
 * before it draws its frame, and what a route's options import is loaded with the first page. The rest of the calls on a
 * server (register, test, remove) stay in nodes.ts, which re-exports these.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../../api/client";

export const nodeKeys = {
  all: ["nodes"] as const,
  list: ["nodes", "list"] as const,
  key: (name: string) => ["nodes", "key", name] as const,
};

/** Every server; polled, since a tunnel's state changes without the console doing anything. */
export const nodesQuery = () =>
  queryOptions({
    queryKey: nodeKeys.list,
    queryFn: ({ signal }) => request("get", "/api/nodes", { signal }),
    refetchInterval: 15_000,
  });
