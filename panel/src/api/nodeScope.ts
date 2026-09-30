/**
 * Which server the console is talking to, for everything below React.
 *
 * A central serves its nodes' APIs through a proxy: `/api/...` on node `web-2` is
 * `/api/nodes/web-2/api/...` here, its `/events` is `/api/nodes/web-2/events`, and its
 * `/ws/...` is `/ws/nodes/web-2/...`. This module is the one place those three mappings live,
 * so no feature ever builds a node URL: the client, the event stream and the WebSockets ask
 * it, and every page works on a node exactly as it does here.
 *
 * What stays the central's own, whichever server is on screen: the operator's credentials
 * (`/api/auth`, so "Confirm it's you" elevates the central's session, which is the one the
 * proxy checks), the fleet itself (`/api/nodes`, `/api/fleet`, `/api/central`) - the prefixes
 * a node refuses from a central (`noust.web.auth.FLEET_REFUSED_PREFIXES`) - and the approvals
 * a second person gives (`/api/approvals`): a node's call is approved on the central, which
 * vouches for it to the node.
 *
 * The query cache is partitioned the same way: an entry's hash carries the server it was
 * read from, so `["apps"]` on web-2 and `["apps"]` here are two entries, and a refetch always
 * goes back to the server its entry belongs to, even when it runs after the operator switched.
 */

import { hashKey } from "@tanstack/react-query";
import type { QueryKey, QueryPersister } from "@tanstack/react-query";

/** API prefixes that are always the central's, never forwarded to a node. */
export const CENTRAL_API_PREFIXES: readonly string[] = [
  "/api/auth",
  "/api/nodes",
  "/api/fleet",
  "/api/central",
  // A node's call that needs a second person is decided where the accounts live: the central.
  "/api/approvals",
];

/**
 * First elements of query keys whose data is the central's own (see CENTRAL_API_PREFIXES):
 * their entries are shared by every server the console shows, never partitioned.
 */
export const CENTRAL_QUERY_ROOTS: ReadonlySet<string> = new Set(["auth", "nodes", "fleet", "servers", "central", "approvals"]);

function under(path: string, prefix: string): boolean {
  return path === prefix || path.startsWith(`${prefix}/`);
}

function pathOnly(path: string): string {
  const end = path.search(/[?#]/);
  return end === -1 ? path : path.slice(0, end);
}

function segment(node: string): string {
  return encodeURIComponent(node);
}

/** True for an API path that belongs to the central whichever server is selected. */
export function isCentralApiPath(path: string): boolean {
  const bare = pathOnly(path);
  return CENTRAL_API_PREFIXES.some((prefix) => under(bare, prefix));
}

/** `/api/apps` on `web-2` is `/api/nodes/web-2/api/apps`; on this server, or for the central's own paths, unchanged. */
export function nodeApiPath(node: string | null, path: string): string {
  if (node === null || !path.startsWith("/api/") || isCentralApiPath(path)) return path;
  return `/api/nodes/${segment(node)}${path}`;
}

const PROXY_PATH = /^\/api\/nodes\/([^/?#]+)\/(?:api\/|events(?:$|[?#]))/;

/**
 * The node a path of the central's proxy reaches (`/api/nodes/web-2/api/apps` is web-2's),
 * for a caller that built one itself, such as the fleet reading every node at once.
 */
export function nodeOfProxyPath(path: string): string | null {
  const match = PROXY_PATH.exec(path);
  if (match?.[1] === undefined) return null;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return null;
  }
}

/** The event stream of a server: `/events` here, `/api/nodes/{node}/events` for a node. */
export function nodeEventsPath(node: string | null): string {
  return node === null ? "/events" : `/api/nodes/${segment(node)}/events`;
}

/** `/ws/logs/x` on `web-2` is `/ws/nodes/web-2/logs/x`; on this server, unchanged. */
export function nodeSocketPath(node: string | null, path: string): string {
  if (node === null || !path.startsWith("/ws/")) return path;
  return `/ws/nodes/${segment(node)}/${path.slice("/ws/".length)}`;
}

// ---------------------------------------------------------------------------------------
// The active server.

type NodeSource = () => string | null;

const THIS_SERVER: NodeSource = () => null;

let source: NodeSource = THIS_SERVER;
let scoped: { node: string | null } | null = null;

/**
 * Tells the client where the selected server is read from: the router installs a reader of
 * its own location, so a request made while a navigation resolves already goes to the server
 * being navigated to. Returns a function that restores the previous reader.
 */
export function installNodeSource(read: NodeSource): () => void {
  const previous = source;
  source = read;
  return () => {
    source = previous;
  };
}

/** Forgets the installed reader and any scope. For tests. */
export function resetNodeScope(): void {
  source = THIS_SERVER;
  scoped = null;
}

/** The server a request made right now goes to: null for this one. */
export function activeNode(): string | null {
  return scoped === null ? source() : scoped.node;
}

/**
 * Runs `fn` with `node` as the active server, synchronously. For work that belongs to a
 * server other than the one on screen: a cache entry's refetch, an event from a node's
 * stream. A request started inside `fn` resolves its server before its first `await`.
 */
export function runOnNode<T>(node: string | null, fn: () => T): T {
  const previous = scoped;
  scoped = { node };
  try {
    return fn();
  } finally {
    scoped = previous;
  }
}

// ---------------------------------------------------------------------------------------
// The query cache, per server.

const NODE_MARK = "@node";

/** True for a key whose entry is the central's, shared by every server. */
export function isCentralQueryKey(queryKey: QueryKey): boolean {
  const root = queryKey[0];
  return typeof root === "string" && CENTRAL_QUERY_ROOTS.has(root);
}

/**
 * The cache's `queryKeyHashFn`: the key as TanStack Query hashes it, preceded by the active
 * server for anything that is not the central's own. Entries of this server hash exactly as
 * they did before there was a fleet.
 */
export function nodeQueryKeyHash(queryKey: QueryKey): string {
  const node = isCentralQueryKey(queryKey) ? null : activeNode();
  return node === null ? hashKey(queryKey) : hashKey([{ [NODE_MARK]: node }, ...queryKey]);
}

/** The server an entry was hashed for (see nodeQueryKeyHash): null for this one. */
export function nodeOfQueryHash(queryHash: string): string | null {
  if (!queryHash.startsWith(`[{"${NODE_MARK}":`)) return null;
  try {
    const parsed: unknown = JSON.parse(queryHash);
    const first: unknown = Array.isArray(parsed) ? parsed[0] : null;
    const node: unknown = typeof first === "object" && first !== null ? (first as Record<string, unknown>)[NODE_MARK] : null;
    return typeof node === "string" ? node : null;
  } catch {
    // Only this module writes the mark, and always as JSON; anything else is this server's.
    return null;
  }
}

/**
 * The cache's `persister`, used as a wrapper around every query function: it runs the fetch
 * on the server the entry belongs to. Without it a refetch that fires after a switch (a
 * window focus, an invalidation) would read the new server into the old server's entry.
 */
export const fetchOnEntryNode: QueryPersister = (queryFn, context, query) =>
  runOnNode(nodeOfQueryHash(query.queryHash), () => queryFn(context));
