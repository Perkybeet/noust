/**
 * The servers this central manages, as `/api/nodes` knows them, and the calls that register,
 * test and remove one. The shapes come from the OpenAPI contract where it declares them; the
 * tunnel's state and the test's result are read field by field, since the contract types them
 * as open dictionaries.
 */

import { queryOptions } from "@tanstack/react-query";

import { api, request } from "../../api/client";
import type { BodyOf, ResponseOf } from "../../api/client";

export type NodeList = ResponseOf<"/api/nodes", "get">;
export type NodeRecord = NodeList["items"][number];
export type NodeKey = ResponseOf<"/api/nodes/{node}/key", "get">;
export type NodeRemoved = ResponseOf<"/api/nodes/{node}", "delete">;
export type AddNodeBody = BodyOf<"/api/nodes", "post">;

/** What the central last recorded about a server: `unknown` until it has tried. */
export type NodeStatus = "reachable" | "unreachable" | "refused" | "unknown";

export function nodeStatus(node: Pick<NodeRecord, "status">): NodeStatus {
  const status = node.status;
  return status === "reachable" || status === "unreachable" || status === "refused" ? status : "unknown";
}

/** The SSH tunnel to a server, as the central's tunnel manager reports it. */
export interface TunnelState {
  open: boolean;
  localPort: number | null;
  since: string | null;
  /** ssh's own stderr from the last failure, verbatim. */
  lastError: string | null;
  failures: number;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function number(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function tunnelOf(node: Pick<NodeRecord, "tunnel">): TunnelState {
  const raw = node.tunnel ?? {};
  return {
    open: raw["open"] === true,
    localPort: number(raw["local_port"]),
    since: text(raw["since"]),
    lastError: text(raw["last_error"]),
    failures: number(raw["failures"]) ?? 0,
  };
}

/** Where the central's tunnel reaches a server: `user@host` and the port when it is not 22. */
export function sshAddress(node: Pick<NodeRecord, "ssh_user" | "ssh_host" | "ssh_port">): string {
  const host = node.ssh_host.includes(":") ? `[${node.ssh_host}]` : node.ssh_host;
  return `${node.ssh_user}@${host}${node.ssh_port === 22 ? "" : `:${String(node.ssh_port)}`}`;
}

/** A server's name as the central accepts it: 1 to 32 lower-case letters, digits and dashes. */
export const NODE_NAME = /^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$/;

/** The outcome of POST /api/nodes/{node}/test. */
export interface NodeTestResult {
  reachable: boolean;
  status: NodeStatus;
  version: string | null;
  latencyMs: number | null;
  /** What failed, as a sentence from the central. */
  error: string | null;
  /** ssh's or the server's own words, verbatim. */
  details: string | null;
}

export function testResultOf(raw: unknown): NodeTestResult {
  const record = typeof raw === "object" && raw !== null ? (raw as Record<string, unknown>) : {};
  return {
    reachable: record["reachable"] === true,
    status: nodeStatus({ status: typeof record["status"] === "string" ? record["status"] : "unknown" }),
    version: text(record["version"]),
    latencyMs: number(record["latency_ms"]),
    error: text(record["error"]),
    details: text(record["details"]),
  };
}

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

/** The central's key for a server (registered or not yet) and the command that authorizes it. */
export const nodeKeyQuery = (name: string) =>
  queryOptions({
    queryKey: nodeKeys.key(name),
    queryFn: ({ signal }) => request("get", "/api/nodes/{node}/key", { params: { node: name }, signal }),
    staleTime: Number.POSITIVE_INFINITY,
  });

export function fetchNodeKey(name: string): Promise<NodeKey> {
  return request("get", "/api/nodes/{node}/key", { params: { node: name } });
}

/** Registers a server with the join code it printed. Sudo mode. */
export function addNode(body: AddNodeBody): Promise<NodeRecord> {
  return request("post", "/api/nodes", { body });
}

/** Opens the tunnel and asks the server who it is. */
export async function testNode(name: string): Promise<NodeTestResult> {
  return testResultOf(await request("post", "/api/nodes/{node}/test", { params: { node: name } }));
}

/** Removes a server, revoking the central's token there first when it answers. Sudo mode. */
export function removeNode(name: string, revoke = true): Promise<NodeRemoved> {
  return request("delete", "/api/nodes/{node}", { params: { node: name }, query: { revoke } });
}

/**
 * Reads a server's own API through the central's proxy: `/api/system/machine` on `web-2` is
 * `/api/nodes/web-2/api/system/machine` here. For the fleet, which reads every server at once
 * and so cannot rely on the one server a page is about.
 */
export function nodeGet<T>(node: string, path: `/api/${string}`, signal?: AbortSignal): Promise<T> {
  return api<T>("GET", `/api/nodes/${encodeURIComponent(node)}${path}`, undefined, { signal });
}

/** The name a central gives itself on its servers, from the command that authorizes it. */
export function centralNameFrom(authorizeCommand: string): string | null {
  const match = /--name\s+'?([a-z0-9][a-z0-9-]*)'?\s*$/.exec(authorizeCommand.trim());
  return match?.[1] ?? null;
}
