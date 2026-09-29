/**
 * The fleet, read: one snapshot per server - this one first, then every server the central
 * manages - with its reachability, its machine readings and what needs attention on it.
 *
 * Every server is read through the central's proxy with its own requests, and the readings
 * are polled rather than streamed. A browser keeps at most six HTTP/1.1 connections per origin
 * and the console's own `/events` already holds one; an EventSource per server would starve
 * every other request of the page as soon as the fleet has five servers.
 */

import { useQueries, useQuery } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import { useMemo } from "react";

import { isApiError } from "../../api/client";
import { appsQuery } from "../../api/queries/apps";
import type { AppList } from "../../api/queries/apps";
import { sessionQuery } from "../../api/queries/auth";
import { certsQuery } from "../../api/queries/certs";
import type { CertList } from "../../api/queries/certs";
import type { DeploymentList } from "../../api/queries/deployments";
import { servicesQuery } from "../../api/queries/services";
import type { ServiceList } from "../../api/queries/services";
import { machineQuery } from "../../api/queries/system";
import type { Machine } from "../../api/queries/system";
import { RECENT_DEPLOYS, recentDeploysQuery } from "../apps/data";
import { centralOf, isCentralLockedError } from "../central/central";
import { CERT_WARNING_DAYS, collectAttention } from "../overview/attention";
import type { AttentionItem } from "../overview/attention";
import { nodeGet, nodeStatus, nodesQuery, tunnelOf } from "./nodes";
import type { NodeRecord } from "./nodes";

/** How often a server's machine readings are read again. */
export const MACHINE_POLL_MS = 15_000;
/** How often the slower sources (apps, deploys, certificates, units) are read again. */
export const DETAIL_POLL_MS = 60_000;

/**
 * Whether a server answers, as the fleet shows it: what the central recorded, sharpened by
 * what the last read through its tunnel said. `locked` is the central's own state: with its
 * secrets sealed it cannot open any tunnel, whatever the server would say.
 */
export type Reachability = "reachable" | "unreachable" | "refused" | "unknown" | "locked";

/** A source of "needs attention" that could not be read on a server. */
export interface UncheckedSource {
  source: "apps" | "deploys" | "certificates" | "units";
  error: unknown;
}

export interface FleetServer {
  /** `null` for this server; the server's name on this central otherwise. */
  node: string | null;
  /** What the rows call it: this machine's hostname, or the server's name. */
  name: string;
  record: NodeRecord | null;
  reachability: Reachability;
  version: string | null;
  /** The version differs from this central's. */
  versionMismatch: boolean;
  machine: Machine | undefined;
  /** Why the machine could not be read, when it could not. */
  machineError: unknown;
  /** ssh's or the proxy's own words for why the server cannot be reached, verbatim. */
  unreachableOutput: string | null;
  /** The central's own sentence and fix for it, when the proxy gave one. */
  unreachableError: unknown;
  attention: AttentionItem[];
  unchecked: UncheckedSource[];
  /** Certificates expiring within the attention window, or expired; null until read. */
  certsExpiring: number | null;
  lastSeen: string | null;
  /** This server is a hub: it deploys nothing, so it has no apps or certificates to read. */
  hub: boolean;
  loading: boolean;
}

/** A key for a server that cannot collide: this machine's hostname may also name a server. */
export function serverKey(server: Pick<FleetServer, "node">): string {
  return server.node === null ? "local" : `node:${server.node}`;
}

interface Sources {
  machine: UseQueryResult<Machine>;
  apps: UseQueryResult<AppList>;
  deploys: UseQueryResult<DeploymentList>;
  certs: UseQueryResult<CertList>;
  units: UseQueryResult<ServiceList>;
}

const fleetKeys = {
  machine: (node: string) => ["fleet", node, "machine"] as const,
  apps: (node: string) => ["fleet", node, "apps"] as const,
  deploys: (node: string) => ["fleet", node, "deploys"] as const,
  certs: (node: string) => ["fleet", node, "certs"] as const,
  units: (node: string) => ["fleet", node, "units"] as const,
};

function expiring(certs: CertList | undefined): number | null {
  if (certs === undefined) return null;
  return certs.certificates.filter((cert) => {
    const days = cert.days_remaining;
    return days !== null && days !== undefined && days < CERT_WARNING_DAYS;
  }).length;
}

function unreachableOf(error: unknown): { output: string | null; code: "unreachable" | "refused" | null } {
  if (!isApiError(error)) return { output: null, code: null };
  if (error.error === "node_unreachable") return { output: error.output ?? error.detail, code: "unreachable" };
  if (error.error === "node_refused") return { output: error.output ?? error.detail, code: "refused" };
  return { output: null, code: null };
}

function uncheckedOf(sources: Sources): UncheckedSource[] {
  const failed: UncheckedSource[] = [];
  if (sources.apps.isError) failed.push({ source: "apps", error: sources.apps.error });
  if (sources.deploys.isError) failed.push({ source: "deploys", error: sources.deploys.error });
  if (sources.certs.isError) failed.push({ source: "certificates", error: sources.certs.error });
  if (sources.units.isError) failed.push({ source: "units", error: sources.units.error });
  return failed;
}

function attentionOf(sources: Sources): AttentionItem[] {
  return collectAttention({
    apps: sources.apps.data?.apps,
    deployments: sources.deploys.data?.items,
    certificates: sources.certs.data?.certificates,
    machine: sources.machine.data,
    units: sources.units.data?.services,
  });
}

/** A server's machine readings, behind the proxy, polled. */
function nodeMachineQuery(node: string, enabled: boolean) {
  return {
    queryKey: fleetKeys.machine(node),
    queryFn: ({ signal }: { signal: AbortSignal }) => nodeGet<Machine>(node, "/api/system/machine", signal),
    enabled,
    refetchInterval: MACHINE_POLL_MS,
  };
}

const DEPLOY_QUERY = new URLSearchParams({ limit: String(RECENT_DEPLOYS.limit) }).toString();

/** The four slower sources of a server's "needs attention", in the order `Sources` reads them. */
function nodeDetailQueries(node: string, enabled: boolean) {
  return [
    {
      queryKey: fleetKeys.apps(node),
      queryFn: ({ signal }: { signal: AbortSignal }) => nodeGet<AppList>(node, "/api/apps", signal),
      enabled,
      refetchInterval: DETAIL_POLL_MS,
    },
    {
      queryKey: fleetKeys.deploys(node),
      queryFn: ({ signal }: { signal: AbortSignal }) => nodeGet<DeploymentList>(node, `/api/deployments?${DEPLOY_QUERY}`, signal),
      enabled,
      refetchInterval: DETAIL_POLL_MS,
    },
    {
      queryKey: fleetKeys.certs(node),
      queryFn: ({ signal }: { signal: AbortSignal }) => nodeGet<CertList>(node, "/api/certs", signal),
      enabled,
      refetchInterval: DETAIL_POLL_MS,
    },
    {
      queryKey: fleetKeys.units(node),
      queryFn: ({ signal }: { signal: AbortSignal }) => nodeGet<ServiceList>(node, "/api/services?noust_only=true", signal),
      enabled,
      refetchInterval: DETAIL_POLL_MS,
    },
  ];
}

export interface Fleet {
  /** This server first, then every managed server by name. */
  servers: FleetServer[];
  /** The list of managed servers could not be read. */
  nodesError: unknown;
  nodesPending: boolean;
  refetchNodes: () => void;
  /** This central's version, which every server is compared with. */
  centralVersion: string | null;
}

/** Reads every server of the fleet. */
export function useFleet(): Fleet {
  const session = useQuery(sessionQuery());
  const central = centralOf(session.data);
  const hub = central.role === "hub";
  const nodes = useQuery(nodesQuery());
  const records = useMemo(
    () => [...(nodes.data?.items ?? [])].sort((a, b) => a.name.localeCompare(b.name)),
    [nodes.data],
  );

  // This server's reads are the console's own queries, shared with the overview's cache; a
  // hub has no apps, deploys or certificates to read, and says so instead of failing.
  const local: Sources = {
    machine: useQuery({ ...machineQuery(), refetchInterval: MACHINE_POLL_MS }),
    apps: useQuery({ ...appsQuery(), enabled: !hub }),
    deploys: useQuery({ ...recentDeploysQuery(), enabled: !hub }),
    certs: useQuery({ ...certsQuery(), enabled: !hub }),
    units: useQuery({ ...servicesQuery(), enabled: !hub }),
  };

  // Machine first; the rest only once the server has answered, so a server that is down costs
  // one failing request per poll, not five.
  const machines = useQueries({ queries: records.map((record) => nodeMachineQuery(record.name, !central.locked)) });
  const details = useQueries({
    queries: records.flatMap((record, index) => nodeDetailQueries(record.name, !central.locked && machines[index]?.isSuccess === true)),
  });

  const centralVersion = session.data?.version ?? null;

  const localServer: FleetServer = {
    node: null,
    name: session.data?.hostname ?? "",
    record: null,
    reachability: "reachable",
    version: centralVersion,
    versionMismatch: false,
    machine: local.machine.data,
    machineError: local.machine.isError ? local.machine.error : null,
    unreachableOutput: null,
    unreachableError: null,
    attention: hub ? [] : attentionOf(local),
    unchecked: hub ? [] : uncheckedOf(local),
    certsExpiring: hub ? null : expiring(local.certs.data),
    lastSeen: null,
    hub,
    loading: local.machine.isPending,
  };

  const remote = records.flatMap((record, index): FleetServer[] => {
    const machine = machines[index];
    // useQueries answers one result per query, in order: four per server after its machine.
    if (machine === undefined) return [];
    const [apps, deploys, certs, units] = details.slice(index * 4, index * 4 + 4) as [
      UseQueryResult<AppList>,
      UseQueryResult<DeploymentList>,
      UseQueryResult<CertList>,
      UseQueryResult<ServiceList>,
    ];
    const sources: Sources = { machine, apps, deploys, certs, units };
    const recorded = nodeStatus(record);
    const tunnel = tunnelOf(record);
    const failure = unreachableOf(machine.error);
    // A read through the tunnel is fresher than the record; the record says why when the
    // read failed in a way that is not the tunnel's.
    // The session may not know yet that the central was locked; the proxy's 423 does.
    const reachability: Reachability = central.locked || isCentralLockedError(machine.error)
      ? "locked"
      : (failure.code ?? (machine.isSuccess ? "reachable" : recorded));
    const down = reachability === "unreachable" || reachability === "refused";
    const version = record.version ?? null;
    const server: FleetServer = {
      node: record.name,
      name: record.name,
      record,
      reachability,
      version,
      versionMismatch: version !== null && centralVersion !== null && version !== centralVersion,
      machine: machine.data,
      machineError: machine.isError && failure.code === null ? machine.error : null,
      unreachableOutput: down ? (failure.output ?? tunnel.lastError) : null,
      unreachableError: down && failure.code !== null ? machine.error : null,
      attention: reachability === "reachable" ? attentionOf(sources) : [],
      unchecked: reachability === "reachable" ? uncheckedOf(sources) : [],
      certsExpiring: reachability === "reachable" ? expiring(certs.data) : null,
      lastSeen: record.last_seen ?? null,
      hub: false,
      loading: !central.locked && machine.isPending,
    };
    return [server];
  });
  const servers = [localServer, ...remote];

  return {
    servers,
    nodesError: nodes.isError ? nodes.error : null,
    nodesPending: nodes.isPending,
    refetchNodes: () => void nodes.refetch(),
    centralVersion,
  };
}
