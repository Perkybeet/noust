import { useQuery, useQueries } from "@tanstack/react-query";

import { request } from "../../../api/client";
import { runOnNode } from "../../../api/nodeScope";
import { sessionQuery } from "../../../api/queries/auth";
import { useHasFleet, useServerList } from "../../../nodes/servers";
import { nodeStatus } from "../../fleet/nodes";
import { fleetStatusQuery } from "./data";
import { LIST_PATH, heldBy, onlyHere } from "./onlyHere";
import type { NamedKind } from "./onlyHere";

/** One thing a server keeps for itself and the fleet does not know. */
export interface OnlyHereRow {
  node: string;
  kind: NamedKind;
  name: string;
  detail?: string;
}

export interface OnlyHereState {
  rows: OnlyHereRow[];
  /** The fleet's names or a server's answer are still on their way. */
  loading: boolean;
}

/**
 * What the servers keep for themselves and the fleet does not know, for some kinds: each reachable server is asked its own
 * list of the kind through the central's proxy (the route a setting of the kind is written by), and what it holds that the
 * central has no setting of is what is here. A server that does not answer, refuses, or has no such route holds nothing
 * here: the strip of the page already says why it is not heard. Quiet where there is no fleet and for a person who may not
 * read it.
 *
 * @param kinds - The kinds to read.
 * @param only - One server, for the page that is about it; every server when absent.
 */
export function useOnlyHere(kinds: readonly NamedKind[], only?: string): OnlyHereState {
  const hasFleet = useHasFleet();
  const { data: session } = useQuery(sessionQuery());
  const active = hasFleet && (session?.permissions?.includes("fleet.read") ?? false);
  const status = useQuery({ ...fleetStatusQuery(), enabled: active });
  const { nodes } = useServerList();
  const asked = nodes.filter((node) => nodeStatus(node) === "reachable" && (only === undefined || node.name === only));
  const pairs = asked.flatMap((node) => kinds.map((kind) => ({ node: node.name, kind })));
  const answers = useQueries({
    queries: pairs.map(({ node, kind }) => ({
      queryKey: ["only-here", kind, node] as const,
      enabled: active,
      retry: false,
      staleTime: 30_000,
      queryFn: ({ signal }: { signal: AbortSignal }): Promise<unknown> => runOnNode(node, () => request("get", LIST_PATH[kind] as "/api/registries", { signal })),
    })),
  });
  if (!active) return { rows: [], loading: false };

  const known = new Map<string, Set<string>>();
  for (const setting of status.data?.settings ?? []) {
    const names = known.get(setting.resource) ?? new Set<string>();
    names.add(setting.name);
    known.set(setting.resource, names);
  }
  const rows: OnlyHereRow[] = [];
  pairs.forEach(({ node, kind }, index) => {
    const answer = answers[index];
    if (answer?.data === undefined) return;
    for (const entry of onlyHere(heldBy(kind, answer.data), known.get(kind) ?? new Set<string>())) {
      rows.push({ node, kind, name: entry.name, ...(entry.detail !== undefined ? { detail: entry.detail } : {}) });
    }
  });
  return { rows: status.isPending ? [] : rows, loading: status.isPending || answers.some((answer) => answer.isPending) };
}
