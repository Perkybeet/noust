/**
 * The selected server in the URL: every console page also exists under `/n/{node}/...`
 * (`/n/web-2/apps/shop.example.com/logs`), so a link to a node's page can be shared.
 *
 * The route tree is not duplicated. The router rewrites the address both ways (TanStack
 * Router's `rewrite`): `/n/web-2/apps` is read as `/apps?node=web-2`, and any location with a
 * `node` search parameter is written back as `/n/web-2/...`. The root route declares `node`
 * and retains it on every navigation (`retainSearchParams`), so each existing `<Link>`, every
 * `navigate()` and every tab keeps the operator on the server they are on, typed as before,
 * with no feature aware of it. Leaving a node is an explicit `node: undefined`.
 *
 * Every page belongs to one of three contexts, and the address says which (`contextOf`):
 *
 * - **a server**: every page of a server, this one's at `/...` and a node's at `/n/<node>/...`;
 * - **all servers**: the Fleet (`/fleet/...`), the whole fleet at once;
 * - **this central**: what belongs to the central whatever server is selected - its servers,
 *   accounts, security, tokens and seal, and the GitHub callback.
 *
 * The last two never carry a node, in either direction; the server the operator came from is
 * remembered apart (nodes/lastServer.ts), so leaving them goes back to it.
 */

import type { LocationRewrite } from "@tanstack/react-router";

/** The search parameter the router keeps the selected server in. */
export const NODE_SEARCH_KEY = "node";

/** The pages of the whole fleet at once ("All servers"). */
export const FLEET_PATHS: readonly string[] = ["/fleet"];

/**
 * The central's own pages ("This central"): its settings that no server can hold for it, and
 * the GitHub App's fixed callback. A node refuses a central on its sign-in, tokens and
 * two-factor on purpose, so these can never be a node's.
 */
export const CENTRAL_PATHS: readonly string[] = [
  "/settings/servers",
  "/settings/central",
  "/settings/accounts",
  "/settings/security",
  "/settings/tokens",
  "/settings/approvals",
  "/settings/audit",
  "/settings/compliance",
  "/integrations",
  "/servers",
];

/** Pages outside any server: signing in and the design gallery. */
const UNSCOPED_PATHS: readonly string[] = ["/login", "/welcome", "/setup", "/invite", "/__design"];

/** Pages that never carry a node: the fleet's, the central's and those outside any server. */
export const CENTRAL_ONLY_PATHS: readonly string[] = [...FLEET_PATHS, ...CENTRAL_PATHS, ...UNSCOPED_PATHS];

const NODE_PATH = /^\/n\/([^/]+)(\/.*)?$/;

function under(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

/** True for a console path that never carries a node (`/settings/security`, `/fleet`). */
export function isCentralOnlyPath(pathname: string): boolean {
  return CENTRAL_ONLY_PATHS.some((prefix) => under(pathname, prefix));
}

/** Which of the three contexts a page is in. */
export type ConsoleContext = { kind: "server"; node: string | null } | { kind: "fleet" } | { kind: "central" };

/**
 * The context of a page, from the router's pathname (without `/n/...`) and the node it names:
 * the fleet's pages are "All servers", the central's own "This central", every other page a
 * server's (this one's when `node` is null).
 */
export function contextOf(pathname: string, node: string | null): ConsoleContext {
  if (FLEET_PATHS.some((prefix) => under(pathname, prefix))) return { kind: "fleet" };
  if (isCentralOnlyPath(pathname)) return { kind: "central" };
  return { kind: "server", node };
}

/** Called with the node an old `/n/<node>/<central page>` address named, as it is dropped. */
let onDroppedNode: ((node: string) => void) | null = null;

/**
 * Tells the rewrite what to do with the node an address named on a page that cannot carry
 * one: remember it, so that leaving the page goes back to that server. Returns a function that
 * restores the previous listener.
 */
export function onNodeDropped(listener: ((node: string) => void) | null): () => void {
  const previous = onDroppedNode;
  onDroppedNode = listener;
  return () => {
    onDroppedNode = previous;
  };
}

function decodeSegment(value: string): string | null {
  try {
    const decoded = decodeURIComponent(value);
    return decoded === "" ? null : decoded;
  } catch {
    // A malformed escape is not a server name; the page answers "not found" instead.
    return null;
  }
}

/**
 * The node an address-bar-form console path names, and the plain path without it:
 * `/n/web-2/apps` is `{ node: "web-2", pathname: "/apps" }`. For a caller that has to build
 * `navigate({ to, search })` itself rather than hand the address to `navigate({ href })`:
 * TanStack Router's `buildLocation` rewrites only the pathname half of a location built from
 * an `href` (`nodeRewrite.input`'s own pathname survives, but what it added to the query
 * string does not), so a "next" address typed or documented in the `/n/{node}/...` form -
 * LoginForm's, in particular - loses the node silently if handed to `navigate({ href })`
 * directly. A path already in the router's own form (`/apps?node=web-2`, wherever it came
 * from) needs none of this: `navigate({ href })` keeps its query string exactly.
 */
export function nodeOfConsolePath(pathname: string): { node: string | null; pathname: string } {
  const match = NODE_PATH.exec(pathname);
  if (match === null) return { node: null, pathname };
  const node = decodeSegment(match[1] ?? "");
  return node === null ? { node: null, pathname } : { node, pathname: match[2] ?? "/" };
}

/**
 * The server a search object names: the router's parsed search, where a numeric-looking
 * name may already have been read as a number.
 */
export function nodeFromSearch(search: unknown): string | null {
  if (typeof search !== "object" || search === null) return null;
  const value: unknown = (search as Record<string, unknown>)[NODE_SEARCH_KEY];
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return typeof value === "string" && value !== "" ? value : null;
}

/** The root route's `validateSearch`: `node` is a server name or absent. */
export function validateNodeSearch(search: Record<string, unknown>): { node?: string | undefined } {
  const node = nodeFromSearch(search);
  return node === null ? {} : { node };
}

/** Reads the search parameter as the router wrote it (JSON-quoted when it looked like a number). */
function readNodeParam(url: URL): string | null {
  const raw = url.searchParams.get(NODE_SEARCH_KEY);
  if (raw === null || raw === "") return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed === "string") return parsed === "" ? null : parsed;
  } catch {
    // Not JSON: the plain name, which is how the router writes an ordinary one.
  }
  return raw;
}

/** The address bar's form of a console path on a server: `/n/web-2/apps`, or the path itself here. */
export function serverPath(node: string | null, pathname: string): string {
  if (node === null || isCentralOnlyPath(pathname)) return pathname;
  return `/n/${encodeURIComponent(node)}${pathname === "/" ? "" : pathname}`;
}

/** The router's rewrite between the address bar (`/n/web-2/apps`) and its routes (`/apps?node=web-2`). */
export const nodeRewrite: LocationRewrite = {
  input: ({ url }) => {
    const match = NODE_PATH.exec(url.pathname);
    if (match === null) {
      // `?node=` on a page of the central's only (an old link, a hand-typed address) is dropped.
      if (url.searchParams.has(NODE_SEARCH_KEY) && isCentralOnlyPath(url.pathname)) {
        url.searchParams.delete(NODE_SEARCH_KEY);
        return url;
      }
      return undefined;
    }
    const node = decodeSegment(match[1] ?? "");
    if (node === null) return undefined;
    const rest = match[2] ?? "/";
    url.pathname = rest;
    if (isCentralOnlyPath(rest)) {
      url.searchParams.delete(NODE_SEARCH_KEY);
      // The operator was on that server: the page they return to from here is its own.
      onDroppedNode?.(node);
    } else {
      // Quoted as JSON, so the router's search parser keeps a name like "123" a string.
      url.searchParams.set(NODE_SEARCH_KEY, JSON.stringify(node));
    }
    return url;
  },
  output: ({ url }) => {
    if (!url.searchParams.has(NODE_SEARCH_KEY)) return undefined;
    const node = readNodeParam(url);
    url.searchParams.delete(NODE_SEARCH_KEY);
    url.pathname = serverPath(node, url.pathname);
    return url;
  },
};

/**
 * Where switching to another server lands, from the page the operator is on: the same page
 * when it is one every server has (`/apps` becomes `/n/web-2/apps`), the server's overview
 * when it is about one thing that the other server may not have (an application, a
 * database) or a page of the fleet's or the central's.
 *
 * @param pathname The router's pathname (without `/n/...`).
 * @param routeId The id of the deepest matched route, such as `/_console/apps/$domain/logs`.
 */
export function switchTarget(pathname: string, routeId: string | undefined): string {
  if (isCentralOnlyPath(pathname)) return returnTarget(pathname);
  if (routeId === undefined || routeId.includes("$") || routeId === "__root__") return "/";
  return pathname;
}

/**
 * The page of a server that stands for a fleet or central page, for going back to the server
 * the operator came from: its own settings from the central's settings, its overview from
 * anywhere else.
 */
export function returnTarget(pathname: string): string {
  return under(pathname, "/settings") ? "/settings" : "/";
}
