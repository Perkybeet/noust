/**
 * The lists that read every server at once, as the central answers them (`GET /api/views/*`, docs/api.md,
 * "A resource across the servers"): the same envelope for each resource, the rows of every server with
 * the server they came from and their page there, how each server answered, and what the operator's role
 * could not read, grouped. A server that is down is a row of `nodes`, never an error of the page.
 *
 * Rows are open objects in the contract (each server's own API decides their fields), so a page reads
 * them field by field through the helpers of its own resource (`features/apps/viewData.ts`).
 */

import { keepPreviousData, queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

/** The envelope every view answers with. */
export type ViewEnvelope = ResponseOf<"/api/views/apps", "get">;
/** How one server answered a view. */
export type ViewNode = ViewEnvelope["nodes"][number];
/** A server the operator may filter a view by: `selector` is what to send as `node` for it alone. */
export type ViewServer = ViewEnvelope["servers"][number];
/** What a role could not read on some servers, grouped by the reason (docs/api.md). */
export type ViewRefusal = ViewEnvelope["refusals"][number];

/** A hit of the search across the servers: what it is, its name, which server it is on and its page there. */
export type SearchHit = NonNullable<ResponseOf<"/api/views/search", "get">["items"]>[number];

export const viewKeys = {
  all: ["views"] as const,
  apps: (nodes: readonly string[]) => ["views", "apps", ...nodes] as const,
  search: (text: string, limit: number) => ["views", "search", text, limit] as const,
};

/** How often a view is read again while it is on screen; the central caches each server's answer about as long. */
export const VIEW_POLL_MS = 15_000;

/**
 * The applications of every server (or of those named: `@central` is this central itself, a node's name
 * its own). A refresh that fails keeps the last answer on screen, and a view never retries on its own: a
 * central that could not put an answer together says why at once.
 */
export const appsViewQuery = (nodes: readonly string[] = []) =>
  queryOptions({
    queryKey: viewKeys.apps(nodes),
    queryFn: ({ signal }) => request("get", "/api/views/apps", { query: nodes.length > 0 ? { node: [...nodes] } : {}, signal }),
    refetchInterval: VIEW_POLL_MS,
    placeholderData: keepPreviousData,
    retry: false,
  });

/** Asks every server again, whatever the central's cache holds: the operator's "Refresh". */
export function refreshAppsView(): Promise<ViewEnvelope> {
  return request("get", "/api/views/apps", { query: { refresh: true } });
}

/**
 * What the words match on any server (`GET /api/views/search`): an application, a database, a site or a
 * certificate, by its name, by what its record says and by the server it is on. Done by the central, which asks each
 * server, so the console never reads every list to find one thing. A server that is down is still found by its name.
 */
export const searchViewQuery = (text: string, limit: number) =>
  queryOptions({
    queryKey: viewKeys.search(text, limit),
    queryFn: ({ signal }) => request("get", "/api/views/search", { query: { q: text, limit, kind: ["app", "database", "site", "certificate"] }, signal }),
    staleTime: 30_000,
    retry: false,
  });
