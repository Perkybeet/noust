/**
 * What each server holds of the four things that are its own and that the central can read but not carry (code hosts, where
 * logs are copied to, secret managers, deploy keys): read through the server's own route, with the central's proxy, one
 * server at a time. No setting of the fleet is made of them (`PLANNED` in `fleet/settings/kinds.py` names two of them),
 * so a table of servers is the honest way to say them: a row a server, what it holds, whether it can be read.
 */

import { useQueries } from "@tanstack/react-query";

import { request } from "../../../api/client";
import { isApiError } from "../../../api/errors";
import { runOnNode } from "../../../api/nodeScope";
import { nodeStatus } from "../../fleet/nodes";
import type { NodeRecord } from "../../fleet/nodes";

/** The four sections that are each server's own. */
export type OwnSection = "integrations" | "log-shipping" | "secret-managers" | "deploy-keys";

export type OwnState = "loading" | "ok" | "off" | "refused" | "unsupported" | "unknown";

export interface OwnRow {
  node: string;
  state: OwnState;
  /** What the server holds, as names: empty when it holds nothing. */
  names: string[];
  /** The number of deploy keys, which are listed by count and not by name. */
  count?: number;
}

type Tree = Record<string, unknown>;

function isTree(value: unknown): value is Tree {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The objects of the first of these keys that is a list; a server that answers otherwise holds none. */
function listOf(data: unknown, keys: readonly string[]): Tree[] {
  if (!isTree(data)) return [];
  for (const key of keys) {
    const value = data[key];
    if (Array.isArray(value)) return value.filter(isTree);
  }
  return [];
}

function nameOf(item: Tree): string {
  for (const key of ["name", "label", "kind", "provider", "type", "backend"]) {
    const value = item[key];
    if (typeof value === "string" && value !== "") return value;
  }
  return "";
}

const PATH: Record<OwnSection, readonly string[]> = {
  "log-shipping": ["/api/log-drains"],
  "secret-managers": ["/api/secret-providers"],
  "deploy-keys": ["/api/deploy-keys"],
  integrations: ["/api/integrations/github", "/api/integrations/gitlab"],
};

/** What a section's answers say the server holds. */
export function readOwn(section: OwnSection, answers: readonly unknown[]): Pick<OwnRow, "names" | "count"> {
  switch (section) {
    case "log-shipping":
      return { names: listOf(answers[0], ["drains", "items", "destinations"]).map(nameOf).filter((name) => name !== "") };
    case "secret-managers":
      return { names: listOf(answers[0], ["providers", "items"]).map(nameOf).filter((name) => name !== "") };
    case "deploy-keys":
      return { names: [], count: listOf(answers[0], ["keys", "items"]).length };
    case "integrations": {
      const github = answers[0];
      const gitlab = answers[1];
      const names: string[] = [];
      if (isTree(github) && github["configured"] === true) names.push("github");
      if (isTree(gitlab) && listOf(gitlab, ["connections"]).length > 0) names.push("gitlab");
      return { names };
    }
  }
}

/** How a server's refusal to be read says why: an older Noust has no such route, a ceiling refuses, anything else is silence. */
export function stateOfError(error: unknown): OwnState {
  if (isApiError(error)) {
    if (error.status === 404) return "unsupported";
    if (error.status === 403) return "refused";
  }
  return "off";
}

/**
 * One row for each server. A server the central already knows does not answer is not asked, and what the central cannot
 * read is said as it is: it does not answer, it refuses, or its Noust has no such route (a central 4.3 over a node 4.2).
 */
export function useOwnRows(section: OwnSection, nodes: readonly NodeRecord[]): OwnRow[] {
  const results = useQueries({
    queries: nodes.map((node) => ({
      queryKey: ["own-table", section, node.name] as const,
      enabled: nodeStatus(node) === "reachable",
      retry: false,
      staleTime: 30_000,
      queryFn: async ({ signal }: { signal: AbortSignal }): Promise<unknown[]> =>
        Promise.all(PATH[section].map((path) => runOnNode(node.name, () => request("get", path as "/api/log-drains", { signal })))),
    })),
  });
  return nodes.map((node, index): OwnRow => {
    const result = results[index];
    const status = nodeStatus(node);
    if (status === "refused") return { node: node.name, state: "refused", names: [] };
    if (status === "unreachable") return { node: node.name, state: "off", names: [] };
    if (result === undefined || result.isPending) return { node: node.name, state: status === "unknown" ? "unknown" : "loading", names: [] };
    if (result.isError) return { node: node.name, state: stateOfError(result.error), names: [] };
    return { node: node.name, state: "ok", ...readOwn(section, result.data) };
  });
}
