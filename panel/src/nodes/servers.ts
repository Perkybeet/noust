/**
 * What the shell needs to know about the servers of this central: the list (the fleet's own
 * query, shared), each node's reachability as a console state, and how two versions compare.
 */

import { useQuery } from "@tanstack/react-query";

import { sessionQuery } from "../api/queries/auth";
import type { Status } from "../components/ui/StatusPill";
import { centralOf } from "../features/central/central";
import { nodeStatus, nodesQuery } from "../features/fleet/nodes";
import type { NodeRecord, NodeStatus } from "../features/fleet/nodes";
import type { PlainKey } from "../i18n";
import { useLastServer } from "./lastServer";

export type { NodeRecord, NodeStatus };

/** Every reachability as a state of the console's vocabulary: its colour, shape and word. */
export const NODE_STATE: Record<NodeStatus, { state: Status; label: PlainKey }> = {
  reachable: { state: "running", label: "fleet.selector.status.reachable" },
  unreachable: { state: "failed", label: "fleet.selector.status.unreachable" },
  refused: { state: "failed", label: "fleet.selector.status.refused" },
  unknown: { state: "unknown", label: "fleet.selector.status.unknown" },
};

export interface ServerList {
  /** The nodes, in the central's order; empty when there are none or they could not be read. */
  nodes: readonly NodeRecord[];
  /** The list could not be read (a server that is no central, a failed request). */
  failed: boolean;
  /** The list has been read. */
  loaded: boolean;
  /** This server's name and version, from the session. */
  hostname: string | null;
  version: string | null;
}

/**
 * The servers the console can switch between. Not polled here: the selector reads it again
 * each time it opens, and the fleet page polls the same entry while it is on screen.
 */
export function useServerList(): ServerList & { refetch: () => void } {
  const list = useQuery({ ...nodesQuery(), refetchInterval: false, retry: false });
  const { data: session } = useQuery(sessionQuery());
  return {
    nodes: list.data?.items ?? [],
    failed: list.isError,
    loaded: list.isSuccess,
    hostname: session?.hostname ?? null,
    version: session?.version ?? null,
    refetch: () => void list.refetch(),
  };
}

export { nodeStatus };

function parts(version: string): number[] | null {
  const match = /^v?(\d+)\.(\d+)(?:\.(\d+))?/.exec(version.trim());
  if (match === null) return null;
  return [Number(match[1]), Number(match[2]), Number(match[3] ?? 0)];
}

/**
 * How a node's version compares with this server's: negative when older, positive when
 * newer, 0 when the same release, null when either cannot be read.
 */
export function compareVersions(node: string | null | undefined, local: string | null | undefined): number | null {
  if (node === null || node === undefined || local === null || local === undefined) return null;
  const a = parts(node);
  const b = parts(local);
  if (a === null || b === null) return null;
  for (let index = 0; index < 3; index += 1) {
    const difference = (a[index] ?? 0) - (b[index] ?? 0);
    if (difference !== 0) return difference;
  }
  return 0;
}

/**
 * Whether this console holds a fleet: a central with servers, or a hub (whose whole purpose
 * is one). Only then are there three contexts to tell apart, and the server named everywhere.
 */
export function useHasFleet(): boolean {
  const { nodes } = useServerList();
  const { data: session } = useQuery(sessionQuery());
  return nodes.length > 0 || centralOf(session).role === "hub";
}

export interface ReturnServer {
  /** The node's name, or null for this server. */
  node: string | null;
  /** What to call it: the node's name, or this server's hostname. */
  name: string;
}

/**
 * The server the fleet's and the central's pages lead back to: the one this tab was last on,
 * while the central still knows it; this server otherwise. Null on a hub that has not been on
 * a server yet: its own pages are the fleet's, so there is nothing to go back to.
 */
export function useReturnServer(): ReturnServer | null {
  const last = useLastServer();
  const servers = useServerList();
  const { data: session } = useQuery(sessionQuery());
  const hub = centralOf(session).role === "hub";
  const here: ReturnServer | null = hub ? null : { node: null, name: servers.hostname ?? "" };
  const node = last?.node ?? null;
  if (node === null) return here;
  // A server the central forgot (removed, renamed) is not somewhere to go back to.
  if (servers.loaded && !servers.nodes.some((candidate) => candidate.name === node)) return here;
  return { node, name: node };
}
