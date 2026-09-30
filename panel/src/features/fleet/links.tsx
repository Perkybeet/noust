import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { nodeOfConsolePath } from "../../app/nodeRoute";

export interface ServerLinkProps {
  /** The server: a node's name, or null for this one. */
  node: string | null;
  /** The page, as on any server: `/apps/shop.example.com/diagnose`. */
  path: string;
  className?: string;
  children: ReactNode;
  "aria-label"?: string;
  onClick?: () => void;
}

/**
 * A link to any page of a server of the fleet, parameters filled in: the router writes it as
 * `/n/<server>/...` (app/nodeRoute.ts). NodeLink covers the pages without parameters.
 */
export function ServerLink({ node, path, className, children, "aria-label": ariaLabel, onClick }: ServerLinkProps) {
  return (
    <Link
      // Built at run time for any server; the router matches it like a typed path.
      to={path}
      // Only the central's own pages render these, where no server is selected to carry over.
      search={node === null ? {} : { node }}
      className={className}
      {...(ariaLabel !== undefined ? { "aria-label": ariaLabel } : {})}
      {...(onClick !== undefined ? { onClick } : {})}
    >
      {children}
    </Link>
  );
}

/**
 * Where a console address leads, as the router wants it: `/n/web-2/backups?domain=x` is the
 * page `/backups` with `{ node: "web-2", domain: "x" }`. The fleet's rows carry their page on
 * their server in the address bar's form (`href`), which is what a person would share.
 */
export function linkOfHref(href: string): { to: string; search: Record<string, string | undefined> } {
  const url = new URL(href, "http://console.invalid");
  const { node, pathname } = nodeOfConsolePath(url.pathname);
  const search: Record<string, string | undefined> = {};
  url.searchParams.forEach((value, key) => {
    search[key] = value;
  });
  search["node"] = node ?? undefined;
  return { to: pathname, search };
}

export interface HrefLinkProps {
  /** A console address, as the fleet's rows give it: `/n/web-2/apps/shop.example.com`. */
  href: string;
  className?: string;
  children: ReactNode;
  "aria-label"?: string;
}

/** A link to a fleet row's page on its own server. */
export function HrefLink({ href, className, children, "aria-label": ariaLabel }: HrefLinkProps) {
  const { to, search } = linkOfHref(href);
  return (
    <Link to={to} search={search as never} className={className} {...(ariaLabel !== undefined ? { "aria-label": ariaLabel } : {})}>
      {children}
    </Link>
  );
}
