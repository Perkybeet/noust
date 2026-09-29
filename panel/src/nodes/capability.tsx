/**
 * What a node's Noust offers, read from its own OpenAPI document through the central's
 * proxy: a node on another version may lack a page's endpoints, and the console says so
 * instead of failing on every call.
 *
 *     const capability = useNodeCapability("POST /api/apps/{domain}/previews");
 *     <NodeCapabilityGate capability="GET /api/zero-downtime">...</NodeCapabilityGate>
 */

import { useQuery } from "@tanstack/react-query";
import { queryOptions } from "@tanstack/react-query";
import { Info } from "lucide-react";
import type { ReactNode } from "react";

import { api } from "../api/client";
import { useT } from "../i18n";
import { cx } from "../lib/cx";
import { useServerList } from "./servers";
import { useNode } from "./useNode";

const METHODS = ["get", "post", "put", "patch", "delete"] as const;

/** A node's operations, compiled once per node and version from its OpenAPI document. */
export interface NodeOperations {
  /** Every `operationId`. */
  ids: ReadonlySet<string>;
  /** Every `METHOD /path` pair, the method upper-case and the path as the schema writes it. */
  routes: ReadonlySet<string>;
  /** Every path, whatever the method. */
  paths: ReadonlySet<string>;
}

/** Reads the operations an OpenAPI document declares. Anything that is not one reads as none. */
export function compileOperations(document: unknown): NodeOperations {
  const ids = new Set<string>();
  const routes = new Set<string>();
  const paths = new Set<string>();
  const declared: unknown = typeof document === "object" && document !== null ? (document as Record<string, unknown>)["paths"] : null;
  if (typeof declared === "object" && declared !== null) {
    for (const [path, item] of Object.entries(declared as Record<string, unknown>)) {
      if (typeof item !== "object" || item === null) continue;
      for (const method of METHODS) {
        const operation: unknown = (item as Record<string, unknown>)[method];
        if (typeof operation !== "object" || operation === null) continue;
        paths.add(path);
        routes.add(`${method.toUpperCase()} ${path}`);
        const id: unknown = (operation as Record<string, unknown>)["operationId"];
        if (typeof id === "string") ids.add(id);
      }
    }
  }
  return { ids, routes, paths };
}

/**
 * Whether the operations include a capability: an operationId (`list_apps_api_apps_get`), a
 * path (`/api/apps/{domain}/previews`, any method), or a method and a path
 * (`POST /api/apps/{domain}/previews`).
 */
export function offers(operations: NodeOperations, capability: string): boolean {
  const wanted = capability.trim();
  if (wanted.startsWith("/")) return operations.paths.has(wanted);
  const routed = /^([A-Za-z]+)\s+(\/\S*)$/.exec(wanted);
  if (routed?.[1] !== undefined && routed[2] !== undefined) return operations.routes.has(`${routed[1].toUpperCase()} ${routed[2]}`);
  return operations.ids.has(wanted);
}

/**
 * A node's operations. The key names the version, and the cache keeps it per node (every
 * entry is partitioned by server, api/nodeScope.ts): read once per node and version, and
 * again when the node is upgraded.
 */
export const nodeOperationsQuery = (version: string | null) =>
  queryOptions({
    queryKey: ["capabilities", version ?? "unknown"] as const,
    queryFn: async ({ signal }) => compileOperations(await api<unknown>("GET", "/api/openapi.json", undefined, { signal })),
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: Number.POSITIVE_INFINITY,
    retry: false,
  });

export interface NodeCapability {
  /**
   * `available` on this server (the console was built for its API) or when the node declares
   * it; `missing` when it does not; `checking` while its schema is read; `unknown` when the
   * schema could not be read, in which case the page runs and the node's own answer decides.
   */
  status: "available" | "missing" | "checking" | "unknown";
  /** The node asked about, or null for this server. */
  node: string | null;
  /** That node's Noust version, when the central knows it. */
  version: string | null;
}

/** Whether the server on screen offers a capability (see `offers` for the forms it takes). */
export function useNodeCapability(capability: string): NodeCapability {
  const { node } = useNode();
  const { nodes, loaded, failed } = useServerList();
  const version = node === null ? null : (nodes.find((candidate) => candidate.name === node)?.version ?? null);
  // After the list, which names the version: otherwise the first read is keyed "unknown" and
  // the schema is fetched twice.
  const operations = useQuery({ ...nodeOperationsQuery(version), enabled: node !== null && (loaded || failed) });
  if (node === null) return { status: "available", node, version };
  if (operations.data) return { status: offers(operations.data, capability) ? "available" : "missing", node, version };
  if (operations.isError) return { status: "unknown", node, version };
  return { status: "checking", node, version };
}

/** "Not available on web-2 (Noust 3.0.1)", with what to do about it. */
export function NotAvailableOnNode({ node, version, className }: { node: string; version: string | null; className?: string }) {
  const t = useT();
  return (
    <section
      aria-labelledby="not-on-node-title"
      className={cx("flex max-w-[72ch] items-start gap-3 rounded-card border border-border bg-surface-raised px-4 py-3.5", className)}
    >
      <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-fg-muted" />
      <div className="flex min-w-0 flex-col gap-1">
        <h2 id="not-on-node-title" className="text-14 font-medium text-fg">
          {version === null ? t("fleet.capability.notAvailableNoVersion", { node }) : t("fleet.capability.notAvailable", { node, version })}
        </h2>
        <p className="text-13 text-pretty text-fg-muted">{t("fleet.capability.explanation", { node })}</p>
      </div>
    </section>
  );
}

/**
 * Renders `children` when the server on screen offers `capability`, and says it is not
 * available on that node when it does not. Nothing is rendered while the node's schema is
 * read, so a page never fires requests a node would answer 404.
 */
export function NodeCapabilityGate({ capability, children }: { capability: string; children: ReactNode }) {
  const t = useT();
  const { status, node, version } = useNodeCapability(capability);
  if (status === "missing" && node !== null) return <NotAvailableOnNode node={node} version={version} />;
  if (status === "checking" && node !== null) {
    return (
      <div aria-busy="true">
        <span className="sr-only">{t("fleet.capability.checking", { node })}</span>
      </div>
    );
  }
  return <>{children}</>;
}
