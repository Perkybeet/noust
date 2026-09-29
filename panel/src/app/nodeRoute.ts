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
 * Some pages are the central's only (its settings, the fleet, the GitHub callback, sign-in):
 * they never carry a node, in either direction.
 */

import type { LocationRewrite } from "@tanstack/react-router";

/** The search parameter the router keeps the selected server in. */
export const NODE_SEARCH_KEY = "node";

/** Pages that belong to the central whatever server is selected: they never carry a node. */
export const CENTRAL_ONLY_PATHS: readonly string[] = ["/settings", "/fleet", "/servers", "/integrations", "/login", "/__design"];

const NODE_PATH = /^\/n\/([^/]+)(\/.*)?$/;

function under(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

/** True for a console path that is the central's only (`/settings/security`, `/fleet`). */
export function isCentralOnlyPath(pathname: string): boolean {
  return CENTRAL_ONLY_PATHS.some((prefix) => under(pathname, prefix));
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
 * database) or a page of the central's only.
 *
 * @param pathname The router's pathname (without `/n/...`).
 * @param routeId The id of the deepest matched route, such as `/_console/apps/$domain/logs`.
 */
export function switchTarget(pathname: string, routeId: string | undefined): string {
  if (isCentralOnlyPath(pathname)) return "/";
  if (routeId === undefined || routeId.includes("$") || routeId === "__root__") return "/";
  return pathname;
}
