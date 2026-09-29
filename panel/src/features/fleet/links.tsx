import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

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
 * `/n/<server>/...` (app/nodeRoute.ts). NodeLink covers the pages without parameters; the
 * fleet's "needs attention" opens an application's diagnosis or a deploy's log on its server.
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
