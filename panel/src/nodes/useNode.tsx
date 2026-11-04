/**
 * The selected server, for React: which one the page is about, links that open a page on
 * another one, and the switch itself.
 *
 * Links inside a page need nothing of this: the router keeps the selected server on every
 * navigation (app/nodeRoute.ts), so `<Link to="/apps/$domain" params={...}>` on web-2 opens
 * web-2's application. These helpers are for the few places that choose a server: the
 * selector, the palette, the fleet's rows.
 */

import { Link, useNavigate, useRouter, useRouterState } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { Fragment, createContext, useCallback, useContext, useEffect, useMemo, useRef } from "react";

import { focusPageTitle } from "../app/focus";
import { areaOf } from "../app/nav";
import { contextOf, nodeFromSearch, serverPath, switchTarget } from "../app/nodeRoute";
import type { ConsoleContext } from "../app/nodeRoute";
import { rememberAll, rememberServer, useLastServer } from "./lastServer";

export interface SelectedNode {
  /** The node's name on this central, or null for this server. */
  node: string | null;
}

const THIS_SERVER: SelectedNode = { node: null };

const NodeContext = createContext<SelectedNode>(THIS_SERVER);

const THIS_SERVER_CONTEXT: ConsoleContext = { kind: "server", node: null };
const FLEET_CONTEXT: ConsoleContext = { kind: "fleet" };
const CENTRAL_CONTEXT: ConsoleContext = { kind: "central" };

const PageContext = createContext<ConsoleContext>(THIS_SERVER_CONTEXT);

/**
 * Provides the selected server to everything signed in, read from the location on screen.
 * A switch remounts what it wraps: every query observer, the event stream and the log and
 * job sockets start again on the new server, so nothing keeps showing the previous one.
 * Focus, which was on the control that switched, goes to the new page's heading.
 *
 * It also says which context the page is in (a server, all servers, or a page of the instance that
 * belongs to no server) and remembers what the operator chose, so the fleet's and the instance's
 * pages can lead back to it and leave the selector where it was.
 */
export function NodeScope({ children }: { children: ReactNode }) {
  const node = useRouterState({ select: (state) => nodeFromSearch(state.location.search) });
  const kind = useRouterState({ select: (state) => contextOf(state.location.pathname, null).kind });
  const value = useMemo<SelectedNode>(() => (node === null ? THIS_SERVER : { node }), [node]);
  const context = useMemo<ConsoleContext>(() => {
    if (kind === "fleet") return FLEET_CONTEXT;
    if (kind === "central") return CENTRAL_CONTEXT;
    return node === null ? THIS_SERVER_CONTEXT : { kind: "server", node };
  }, [kind, node]);
  const shown = useRef(node);
  useEffect(() => {
    if (shown.current === node) return;
    shown.current = node;
    if (document.querySelector("main [data-page-title]")) focusPageTitle();
    else document.getElementById("main")?.focus();
  }, [node]);
  useEffect(() => {
    if (context.kind === "server") rememberServer(context.node);
    else if (context.kind === "fleet") rememberAll();
  }, [context]);
  return (
    <NodeContext value={value}>
      <PageContext value={context}>
        <Fragment key={node ?? ""}>{children}</Fragment>
      </PageContext>
    </NodeContext>
  );
}

/**
 * Provides a given server without the router: for a component rendered alone, in a test or
 * the design gallery. The console itself uses NodeScope, which reads the URL.
 */
export function ProvideNode({ node, children }: { node: string | null; children: ReactNode }) {
  const value = useMemo<SelectedNode>(() => (node === null ? THIS_SERVER : { node }), [node]);
  return <NodeContext value={value}>{children}</NodeContext>;
}

/**
 * The server the page on screen is about: null for this one, and outside the signed-in
 * console (sign-in, a component rendered alone in a test).
 */
export function useNode(): SelectedNode {
  return useContext(NodeContext);
}

/**
 * Which context the page on screen is in: a server's page, the fleet's ("All servers"), or a page of the instance
 * that is no server's (the accounts, the tokens, the compliance: `central`). This server's outside the signed-in
 * console. The selector has two places, not three: an instance page leaves it where it was (`useSelection`).
 */
export function useConsoleContext(): ConsoleContext {
  return useContext(PageContext);
}

/** What the selector says: the whole fleet, or one server (null: this one). */
export type Selection = { kind: "all" } | { kind: "server"; node: string | null };

/**
 * What the selector is on. It follows the page where the page has a place (a server's, or the fleet's), and on a
 * page of the instance, which has none, it stays where the operator had put it: the server they were on, or the
 * whole fleet. A hub has no server of its own, so it starts on the fleet.
 */
export function useSelection(hub = false): Selection {
  const context = useConsoleContext();
  const last = useLastServer();
  if (context.kind === "fleet") return { kind: "all" };
  if (context.kind === "server") return { kind: "server", node: context.node };
  if (last?.all === true || (last === null && hub)) return { kind: "all" };
  return { kind: "server", node: last?.node ?? null };
}

/**
 * Navigation options that open `to` on `target` (null: this server), for `<Link>` and
 * `navigate()`: `<Link {...onServer("web-2", "/apps")}>`.
 */
export function onServer<const To extends string = "/">(target: string | null, to?: To) {
  return { to: (to ?? "/") as To, search: { node: target ?? undefined } };
}

export interface NodeLinks {
  node: string | null;
  /** Options that open `to` (default: the overview) on another server; see `onServer`. */
  onServer: typeof onServer;
  /**
   * The address of a console path as the address bar shows it, for text that leaves the
   * console (a copied link, an email): `/n/web-2/apps/x` on web-2. Links rendered by the
   * router need nothing: they stay on the server by themselves.
   */
  href: (pathname: string, target?: string | null) => string;
}

/** Links between servers, from the one on screen. */
export function useNodeLink(): NodeLinks {
  const { node } = useNode();
  const href = useCallback((pathname: string, target: string | null = node) => serverPath(target, pathname), [node]);
  return { node, onServer, href };
}

/**
 * Where "All servers" opens from a page: the fleet's face of the area the page is in, where the area has one, and the
 * fleet's summary otherwise. The area is kept, as a server's is.
 */
export function allServersTarget(pathname: string): "/fleet" | "/fleet/apps" | "/fleet/certificates" | "/fleet/backups" | "/fleet/servers" | "/fleet/activity" | "/fleet/alerts" | "/fleet/network" | "/fleet/security" {
  const face = areaOf(pathname)?.fleetTo;
  return (face ?? "/fleet") as ReturnType<typeof allServersTarget>;
}

/**
 * Where a server opens from the fleet's pages: the area the fleet's page is the face of, on that server. The summary
 * of the fleet is the server's own home.
 */
function serverTargetFromFleet(pathname: string): string {
  const area = areaOf(pathname);
  return area === undefined ? "/" : area.to;
}

/**
 * Switches the console to another server (null: this one), staying on the same page when every server has it and
 * going to the list of its area otherwise (see `switchTarget`). From the fleet's pages it opens the area the page
 * is the face of, on the server chosen. On a page of the instance, which is no server's, nothing is opened: only
 * the selector moves, and the next page the person opens is the chosen server's.
 */
export function useSwitchNode(): (target: string | null) => Promise<void> {
  const router = useRouter();
  const navigate = useNavigate();
  return useCallback(
    async (target: string | null) => {
      const { location, matches } = router.state;
      const kind = contextOf(location.pathname, null).kind;
      if (kind === "central") {
        rememberServer(target);
        return;
      }
      if (kind === "server" && nodeFromSearch(location.search) === target) return;
      // A resource the other server may not have (an application) leaves the person in its area, not at the server's home.
      const same = switchTarget(location.pathname, matches.at(-1)?.routeId);
      const to = kind === "fleet" ? serverTargetFromFleet(location.pathname) : same === "/" && location.pathname !== "/" ? serverTargetFromFleet(location.pathname) : same;
      await navigate({ to, search: { node: target ?? undefined } });
    },
    [router, navigate],
  );
}

export interface NodeLinkProps {
  /** The server to open: a node's name, or null for this one. */
  node: string | null;
  /** A page every server has, without parameters; the overview by default. */
  to?: "/" | "/apps" | "/databases" | "/services" | "/cron" | "/domains" | "/backups" | "/activity" | "/server";
  children: ReactNode;
  className?: string;
  "aria-label"?: string;
}

/** A link to a page on a given server, such as a fleet row's name opening that node. */
export function NodeLink({ node, to = "/", children, className, "aria-label": label }: NodeLinkProps) {
  return (
    <Link to={to} search={{ node: node ?? undefined }} className={className} {...(label !== undefined ? { "aria-label": label } : {})}>
      {children}
    </Link>
  );
}
