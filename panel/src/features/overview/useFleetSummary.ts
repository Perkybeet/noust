/**
 * The fleet, summarised for the Overview of a central: one line per server with whether it
 * answers, which Noust it runs, its applications and what needs attention on it.
 *
 * Read from `GET /api/fleet/summary`, the central's one view of every server: each row carries
 * that server's own `GET /api/overview` verbatim (a central re-implements nothing a node does),
 * and each server's outcome says why a row is missing or stale, in ssh's or the node's words.
 */

import { useQuery } from "@tanstack/react-query";
import { queryOptions } from "@tanstack/react-query";

import { request } from "../../api/client";
import type { ResponseOf } from "../../api/client";
import { nodesQuery } from "../fleet/nodes";

export type FleetReachability = "reachable" | "unreachable" | "refused" | "unknown" | "locked";

export interface FleetSummaryRow {
  /** Unique: this server is `local`, a node is `node:<name>`. */
  key: string;
  /** `null` for this server. */
  node: string | null;
  name: string;
  reachability: FleetReachability;
  version: string | null;
  apps: { running: number; failed: number } | null;
  attention: { count: number; worst: "fail" | "warn" | null } | null;
  /** Why the server could not be read, in ssh's or the node's own words. */
  error: string | null;
  /** It answered, but runs a Noust too old to summarise itself. */
  older: boolean;
  loading: boolean;
}

export interface FleetSummary {
  rows: FleetSummaryRow[];
  servers: number;
  reachable: number;
  unreachable: number;
}

type FleetView = ResponseOf<"/api/fleet/summary", "get">;
type Outcome = FleetView["nodes"][number];

/** How often the fleet's summary is read again; the central caches each server's answer. */
export const FLEET_SUMMARY_POLL_MS = 30_000;

export const fleetSummaryQuery = () =>
  queryOptions({
    queryKey: ["fleet", "summary"] as const,
    queryFn: ({ signal }) => request("get", "/api/fleet/summary", { signal }),
    refetchInterval: FLEET_SUMMARY_POLL_MS,
  });

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function count(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

const REACHABILITY: readonly FleetReachability[] = ["reachable", "unreachable", "refused", "unknown", "locked"];

/** One row of the fleet's summary, read field by field: the contract types rows as open dictionaries. */
export function summaryRow(item: Record<string, unknown>, outcome: Outcome | undefined): FleetSummaryRow {
  const local = item["local"] === true;
  const name = text(item["name"]) ?? text(item["node"]) ?? "";
  const apps = record(item["apps"]);
  const overview = record(item["overview"]);
  const attention = overview !== null && Array.isArray(overview["attention"]) ? (overview["attention"] as unknown[]) : null;
  const worst = record(attention?.[0])?.["severity"];
  const status = outcome?.status ?? "ok";
  let reachability = REACHABILITY.find((word) => word === item["reachability"]) ?? "unknown";
  if (status === "unreachable") reachability = outcome?.code === "node_refused" ? "refused" : "unreachable";
  else if (status === "ok" || status === "stale" || status === "unsupported") reachability = "reachable";
  const failed = status === "unreachable" || status === "error" || status === "forbidden";
  return {
    key: local ? "local" : `node:${name}`,
    node: local ? null : name,
    name,
    reachability,
    version: text(item["version"]) ?? outcome?.version ?? null,
    apps: apps === null ? null : { running: count(apps["running"]) ?? 0, failed: count(apps["failed"]) ?? 0 },
    attention:
      overview === null
        ? null
        : { count: count(overview["attention_total"]) ?? attention?.length ?? 0, worst: worst === "fail" || worst === "warn" ? worst : null },
    error: failed ? (outcome?.error_verbatim ?? outcome?.message ?? null) : null,
    older: status === "unsupported" && overview === null,
    loading: false,
  };
}

/** The fleet's rows, this server first, then every server by name. */
export function summaryOf(view: FleetView): FleetSummary {
  const outcomes = new Map(view.nodes.map((outcome) => [outcome.local ? "@local" : outcome.name, outcome]));
  const rows = view.items
    .map((item) => summaryRow(item, outcomes.get(item["local"] === true ? "@local" : String(item["node"] ?? item["name"]))))
    .sort((a, b) => (a.node === null ? -1 : b.node === null ? 1 : a.name.localeCompare(b.name)));
  const reachable = rows.filter((row) => row.reachability === "reachable").length;
  const unreachable = rows.filter((row) => row.reachability === "unreachable" || row.reachability === "refused").length;
  return { rows, servers: rows.length, reachable, unreachable };
}

/** The fleet block's four states: its rows, loading (its room kept), or why it could not be read. */
export interface FleetSummaryState {
  summary: FleetSummary | null;
  loading: boolean;
  error: unknown;
  retry: () => void;
}

/**
 * Reads the fleet this console manages. Null on a server that manages no other: the Overview
 * then has no fleet block at all, and asks for nothing.
 */
export function useFleetSummary(): FleetSummaryState | null {
  const nodes = useQuery(nodesQuery());
  const central = (nodes.data?.items.length ?? 0) > 0;
  const view = useQuery({ ...fleetSummaryQuery(), enabled: central });
  if (!central) return null;
  return {
    summary: view.data === undefined ? null : summaryOf(view.data),
    loading: view.isPending,
    error: view.data === undefined && view.isError ? view.error : null,
    retry: () => void view.refetch(),
  };
}
