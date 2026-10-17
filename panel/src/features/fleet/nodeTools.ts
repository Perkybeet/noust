/**
 * What a server's menu in the Fleet can do beyond the basics: how this central connects to it,
 * its inventory (to take it to another central) and the machine it is at a cloud provider. The
 * calls are the central's own (`/api/nodes/{node}/ssh`, `.../inventory`, `/api/fleet/cloud/servers/{node}/link`).
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../../api/client";
import type { BodyOf, ResponseOf } from "../../api/client";
import { nodeKeys } from "./nodes";

export type ConnectionOptions = ResponseOf<"/api/nodes/{node}/ssh", "get">;
export type ConnectionBody = BodyOf<"/api/nodes/{node}/ssh", "put">;
export type InventoryReport = ResponseOf<"/api/nodes/{node}/inventory", "post">;
export type LinkBody = BodyOf<"/api/fleet/cloud/servers/{node}/link", "post">;

export const connectionKey = (node: string) => [...nodeKeys.all, "ssh", node] as const;

/** How the central connects to a server beyond its address. */
export const connectionQuery = (node: string) =>
  queryOptions({
    queryKey: connectionKey(node),
    queryFn: ({ signal }) => request("get", "/api/nodes/{node}/ssh", { params: { node }, signal }),
    retry: false,
  });

/** Sets the connection options and tries them: the old ones are put back when the server does not answer. */
export function saveConnection(node: string, body: ConnectionBody): Promise<ConnectionOptions> {
  return request("put", "/api/nodes/{node}/ssh", { params: { node }, body });
}

/** What this central knows about a server, to take to another central. No secret is in it. */
export function fetchInventory(node: string): Promise<Record<string, unknown>> {
  return request("get", "/api/nodes/{node}/inventory", { params: { node } });
}

/** Records, for a server just added here, an inventory another central exported for it. */
export function applyInventory(node: string, document: Record<string, unknown>, includeSsh: boolean): Promise<InventoryReport> {
  return request("post", "/api/nodes/{node}/inventory", { params: { node }, body: { document, include_ssh: includeSsh } });
}

/** Links a server of the fleet to its machine at a provider. Sudo mode. */
export function linkMachine(node: string, body: LinkBody) {
  return request("post", "/api/fleet/cloud/servers/{node}/link", { params: { node }, body });
}

/** A bastion as typed: `user@host` or `user@host:port`, where host is a name or an address. */
export const BASTION = /^[a-z_][a-z0-9_-]{0,31}@(?:\[[0-9a-fA-F:]+\]|[A-Za-z0-9][A-Za-z0-9.-]{0,252})(?::\d{1,5})?$/;

/** A host key fingerprint as `ssh-keygen -l` prints it. */
export const FINGERPRINT = /^SHA256:[A-Za-z0-9+/]{43}=?$/;

/** An ed25519 host key line. */
export const ED25519_LINE = /^ssh-ed25519 [A-Za-z0-9+/]{40,}={0,2}(?: \S+)?$/;

/** The wait for the connection as typed: whole seconds from 1 to 300, or nothing for the default. */
export function parseTimeout(value: string): number | null | undefined {
  const text = value.trim();
  if (text === "") return null;
  if (!/^\d{1,3}$/.test(text)) return undefined;
  const seconds = Number(text);
  return seconds >= 1 && seconds <= 300 ? seconds : undefined;
}

/** What was typed for a bastion's host key: a fingerprint or a key line; undefined when it is neither. */
export function parseBastionKey(value: string): { fingerprint: string } | { host_key: string } | null | undefined {
  const text = value.trim();
  if (text === "") return null;
  if (FINGERPRINT.test(text)) return { fingerprint: text };
  if (ED25519_LINE.test(text)) return { host_key: text };
  return undefined;
}

/** The two ends of the part of an inventory that was recorded or left out. */
export type InventoryPart = "labels" | "placements" | "monitoring" | "ssh" | "cloud";

export function isInventoryPart(part: string): part is InventoryPart {
  return part === "labels" || part === "placements" || part === "monitoring" || part === "ssh" || part === "cloud";
}
