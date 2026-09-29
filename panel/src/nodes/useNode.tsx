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
import { nodeFromSearch, serverPath, switchTarget } from "../app/nodeRoute";

export interface SelectedNode {
  /** The node's name on this central, or null for this server. */
  node: string | null;
}

const THIS_SERVER: SelectedNode = { node: null };

const NodeContext = createContext<SelectedNode>(THIS_SERVER);

/**
 * Provides the selected server to everything signed in, read from the location on screen.
 * A switch remounts what it wraps: every query observer, the event stream and the log and
 * job sockets start again on the new server, so nothing keeps showing the previous one.
 * Focus, which was on the control that switched, goes to the new page's heading.
 */
export function NodeScope({ children }: { children: ReactNode }) {
  const node = useRouterState({ select: (state) => nodeFromSearch(state.location.search) });
  const value = useMemo<SelectedNode>(() => (node === null ? THIS_SERVER : { node }), [node]);
  const shown = useRef(node);
  useEffect(() => {
    if (shown.current === node) return;
    shown.current = node;
    if (document.querySelector("main [data-page-title]")) focusPageTitle();
    else document.getElementById("main")?.focus();
  }, [node]);
  return (
    <NodeContext value={value}>
      <Fragment key={node ?? ""}>{children}</Fragment>
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
 * Switches the console to another server (null: this one), staying on the same page when
 * every server has it and going to the server's overview otherwise (see `switchTarget`).
 */
export function useSwitchNode(): (target: string | null) => Promise<void> {
  const router = useRouter();
  const navigate = useNavigate();
  return useCallback(
    async (target: string | null) => {
      const { location, matches } = router.state;
      if (nodeFromSearch(location.search) === target) return;
      const to = switchTarget(location.pathname, matches.at(-1)?.routeId);
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
