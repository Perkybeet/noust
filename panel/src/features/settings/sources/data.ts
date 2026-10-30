/**
 * The code a central lends its servers (`/api/fleet/sources`, item 90): the token each server was leased for
 * the repositories it deploys, and the pushes the central passed on. What is true is the server's and GitHub's
 * to say; this reads it and offers the two things a person does: renew the tokens now, and retry a push.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../../../api/client";
import type { ResponseOf } from "../../../api/client";
import type { Status } from "../../../components/ui/StatusPill";
import type { PlainKey } from "../../../i18n";

export type SourcesStatus = ResponseOf<"/api/fleet/sources", "get">;
export type NodeSources = SourcesStatus["nodes"][number];
export type Deliveries = ResponseOf<"/api/fleet/sources/deliveries", "get">;
export type Delivery = Deliveries["items"][number];

export const sourcesKeys = {
  all: ["fleet", "sources"] as const,
  status: ["fleet", "sources", "status"] as const,
  failed: ["fleet", "sources", "failed"] as const,
};

/** How each server reaches its repositories, and the counts of the pushes passed on. */
export const sourcesQuery = () =>
  queryOptions({
    queryKey: sourcesKeys.status,
    queryFn: ({ signal }) => request("get", "/api/fleet/sources", { signal }),
    refetchInterval: 60_000,
  });

/** The pushes that did not arrive, for the ones that can be tried again. */
export const failedDeliveriesQuery = () =>
  queryOptions({
    queryKey: sourcesKeys.failed,
    queryFn: ({ signal }) => request("get", "/api/fleet/sources/deliveries", { query: { state: "failed", limit: 10 }, signal }),
  });

/** Leases the tokens again now, for every server or the ones named. */
export function renewLeases(nodes?: readonly string[]) {
  return request("post", "/api/fleet/sources/leases", { body: { force: true, ...(nodes !== undefined ? { nodes: [...nodes] } : {}) } });
}

export function retryDelivery(id: string) {
  return request("post", "/api/fleet/sources/deliveries/{forward_id}/retry", { params: { forward_id: id } });
}

/** A server's state as the page says it: the word and the state it is drawn with. A well one has no colour. */
export function stateView(state: string): { label: PlainKey; pill: Status | null } {
  switch (state) {
    case "leased":
      return { label: "connections.state.leased", pill: null };
    case "none":
      return { label: "connections.state.none", pill: "stopped" };
    case "no_app":
      return { label: "connections.state.no_app", pill: "stopped" };
    case "independent":
      return { label: "connections.state.independent", pill: "stopped" };
    case "unsupported":
      return { label: "fleet.outcome.unsupported", pill: "stopped" };
    case "ceiling":
      return { label: "connections.state.ceiling", pill: "warning" };
    case "unreachable":
      return { label: "connections.state.unreachable", pill: "failed" };
    case "failed":
      return { label: "common.statusPill.failed", pill: "failed" };
    default:
      return { label: "connections.state.unknown", pill: "stopped" };
  }
}

/** The soonest a server's leases end, in seconds, or null when it holds none that have a time. */
export function soonest(node: Pick<NodeSources, "leases">): number | null {
  const times = (node.leases ?? []).map((lease) => lease.expires_in).filter((seconds): seconds is number => typeof seconds === "number");
  return times.length === 0 ? null : Math.min(...times);
}

/** How many repositories hold a token across the leases of a server. */
export function repositoriesLeased(node: Pick<NodeSources, "leases">): number {
  return new Set((node.leases ?? []).flatMap((lease) => lease.repositories)).size;
}
