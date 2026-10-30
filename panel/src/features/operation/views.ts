/**
 * One resource across the servers (`/api/views/<resource>`), for the four areas of what happens and
 * what is kept safe: Logs, Alerts, Activity and Backups. The central asks each server as the
 * operator and answers the rows of all of them, each with the server it is on; naming servers
 * filters them and never changes the page. A server that refuses the operator is an outcome, not a
 * failure of the page, and the ones that refused alike come grouped (`refusals`) with what is
 * missing, as a sentence with a code.
 *
 * Rows are open objects in the contract (each server's own API decides their fields), so they are
 * read field by field through the readers of `features/fleet/data.ts`, which every fleet page uses.
 */

import { keepPreviousData, queryOptions } from "@tanstack/react-query";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { request } from "../../api/client";
import type { ResponseOf } from "../../api/client";
import type { NodeOutcome } from "../fleet/data";

export type ViewAnswer = ResponseOf<"/api/views/activity", "get">;
export type ViewServer = ViewAnswer["servers"][number];
export type ViewNode = ViewAnswer["nodes"][number];
export type Refusal = ViewAnswer["refusals"][number];

/** The views these areas read. */
export type ViewResource = "activity" | "backups" | "alerts" | "logs";

/** How often each view is read again while it is on screen; the central caches about as long. */
const POLL_MS: Readonly<Record<ViewResource, number>> = {
  activity: 30_000,
  backups: 60_000,
  alerts: 20_000,
  logs: 0,
};

export interface ViewParams {
  /** Only these servers (`@central` is this central itself); every server when empty. */
  nodes?: readonly string[];
  /** Events per server, for Activity. */
  limit?: number;
  /** The search, for Logs. */
  q?: string;
  since?: string;
  until?: string;
  level?: string;
}

export const viewKeys = {
  all: ["views"] as const,
  one: (resource: ViewResource, params: ViewParams) => ["views", resource, params.nodes ?? [], params] as const,
};

/** The query string of a view: only what was asked, `node` repeated. */
function queryOf(params: ViewParams): Record<string, string | number | string[]> {
  const out: Record<string, string | number | string[]> = {};
  if (params.nodes !== undefined && params.nodes.length > 0) out["node"] = [...params.nodes];
  if (params.limit !== undefined) out["limit"] = params.limit;
  if (params.q !== undefined) out["q"] = params.q;
  if (params.since !== undefined) out["since"] = params.since;
  if (params.until !== undefined) out["until"] = params.until;
  if (params.level !== undefined) out["level"] = params.level;
  return out;
}

const PATHS = {
  activity: "/api/views/activity",
  backups: "/api/views/backups",
  alerts: "/api/views/alerts",
  logs: "/api/views/logs",
} as const;

/**
 * One view. A refresh that fails keeps the last answer on screen, and a view never retries on its
 * own: a central that could not put an answer together says why at once. `live` asks again every few
 * seconds (a log followed).
 */
export const viewQuery = (resource: ViewResource, params: ViewParams = {}, options: { live?: number | false } = {}) =>
  queryOptions({
    queryKey: viewKeys.one(resource, params),
    queryFn: ({ signal }): Promise<ViewAnswer> => request("get", PATHS[resource], { query: queryOf(params), signal }),
    refetchInterval: options.live ?? (POLL_MS[resource] > 0 ? POLL_MS[resource] : false),
    placeholderData: keepPreviousData,
    retry: false,
  });

/** Asks every server again, whatever the central's cache holds: the operator's "Refresh". */
export function refreshView(resource: ViewResource, params: ViewParams = {}): Promise<ViewAnswer> {
  return request("get", PATHS[resource], { query: { ...queryOf(params), refresh: true } });
}

/** One view with each server's outcome at hand by name. */
export function useView(resource: ViewResource, params: ViewParams = {}, options: { live?: number | false; enabled?: boolean } = {}) {
  const query = useQuery({ ...viewQuery(resource, params, options), enabled: options.enabled ?? true });
  const outcomes = useMemo(() => new Map<string, NodeOutcome>((query.data?.nodes ?? []).map((outcome) => [outcome.name, outcome])), [query.data]);
  return { ...query, outcomes };
}

/** The servers that refused the operator, by name: the ones a page does not list as troubled. */
export function refusedServers(view: Pick<ViewAnswer, "refusals"> | undefined): ReadonlySet<string> {
  return new Set((view?.refusals ?? []).flatMap((group) => group.servers));
}

/** A server's value in the URL: `@central` for this central, its name otherwise. */
export const CENTRAL_SELECTOR = "@central";
