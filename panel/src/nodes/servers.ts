/**
 * What the shell needs to know about the servers of this central: the list (the fleet's own
 * query, shared), each node's reachability as a console state, and how two versions compare.
 */

import { useQuery } from "@tanstack/react-query";

import { sessionQuery } from "../api/queries/auth";
import type { Status } from "../components/ui/StatusPill";
import { nodeStatus, nodesQuery } from "../features/fleet/nodes";
import type { NodeRecord, NodeStatus } from "../features/fleet/nodes";
import type { PlainKey } from "../i18n";

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
