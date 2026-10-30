/**
 * Network and ports, apart from any page: which views it has, how the registry's claims and the
 * firewall's listening ports become one row per port, and the answer to "who uses port N?". Pure,
 * so each rule is tested without drawing a table.
 */

import type { ListeningPort, PortClaim } from "../queries";

export const NETWORK_VIEWS = ["ports", "firewall", "published", "interfaces"] as const;
export type NetworkView = (typeof NETWORK_VIEWS)[number];

export interface NetworkSearch {
  /** The default, ports, is no parameter. */
  view?: Exclude<NetworkView, "ports">;
  /** What is looked up: a port number, or part of what holds it. */
  q?: string;
}

/** Reads `?view=` and `?q=`, dropping anything that is neither a view nor a word of reasonable length. */
export function validateNetworkSearch(search: Record<string, unknown>): NetworkSearch {
  const view = search["view"];
  const q = typeof search["q"] === "string" ? search["q"].trim().slice(0, 60) : "";
  return {
    ...(typeof view === "string" && view !== "ports" && (NETWORK_VIEWS as readonly string[]).includes(view) ? { view: view as Exclude<NetworkView, "ports"> } : {}),
    ...(q !== "" ? { q } : {}),
  };
}

/** What holds a port, as the product says it: the registry's kinds, with the machine's own services named apart. */
export type HolderKind = "web" | "ssh" | "console" | "app" | "stack" | "stream" | "database" | "instance" | "dns" | "process";

export interface Holder {
  kind: HolderKind;
  /** An application's domain, a published port's name, a database, or the process: what a person recognises. */
  owner: string;
  /** Where the claim comes from ("its copy", "zero-downtime"), when it says more. */
  detail: string;
  /** The address it is bound to; null for every address. */
  address: string | null;
}

export interface PortRow {
  /** `tcp:5432`. */
  key: string;
  port: number;
  protocol: "tcp" | "udp";
  /** Declared holders first (Noust knows what they are), then the process the kernel reports. */
  holders: readonly Holder[];
  /** What the firewall says about it, when the port is listening and the firewall was read. */
  firewall: ListeningPort | null;
}

function isMachine(holder: Holder): boolean {
  return holder.kind === "web" || holder.kind === "ssh" || holder.kind === "console";
}

function kindOf(claim: PortClaim): HolderKind {
  if (claim.kind === "system") return claim.owner === "ssh" ? "ssh" : claim.owner === "console" ? "console" : "web";
  switch (claim.kind) {
    case "app":
    case "stack":
    case "stream":
    case "database":
    case "instance":
    case "dns":
      return claim.kind;
    default:
      return "process";
  }
}

/**
 * One row per port and protocol. Where a declared holder exists (an application, a published
 * port, the web server) the process the kernel reports for the same port is the same thing seen
 * from the system, so it is left out: the row says what Noust knows, and a process is shown only
 * for a port nothing in Noust accounts for.
 */
export function portRows(claims: readonly PortClaim[], firewall: readonly ListeningPort[]): PortRow[] {
  const byKey = new Map<string, { port: number; protocol: "tcp" | "udp"; declared: Holder[]; observed: Holder[] }>();
  for (const claim of claims) {
    if (claim.protocol !== "tcp" && claim.protocol !== "udp") continue;
    const key = `${claim.protocol}:${String(claim.port)}`;
    const row = byKey.get(key) ?? { port: claim.port, protocol: claim.protocol, declared: [], observed: [] };
    const holder: Holder = { kind: kindOf(claim), owner: claim.owner, detail: claim.detail, address: claim.address ?? null };
    (holder.kind === "process" ? row.observed : row.declared).push(holder);
    byKey.set(key, row);
  }
  const listening = new Map<string, ListeningPort>(firewall.map((entry) => [`${entry.proto}:${String(entry.port)}`, entry]));
  return [...byKey.entries()]
    // The machine's own services reserve a port over both protocols; the second one is no port in use until something listens on it.
    .filter(([key, row]) => !(row.protocol === "udp" && row.declared.every(isMachine) && !listening.has(key) && row.observed.length === 0))
    .map(([key, row]): PortRow => ({
      key,
      port: row.port,
      protocol: row.protocol,
      holders: row.declared.length > 0 ? row.declared : row.observed,
      firewall: listening.get(key) ?? null,
    }))
    .sort((a, b) => a.port - b.port || a.protocol.localeCompare(b.protocol));
}

/** Whether a port is open by accident: a database or internal service answering everyone, or Docker around the firewall. */
export function isExposed(row: PortRow): boolean {
  const port = row.firewall;
  if (port === null) return false;
  return port.verdict === "docker_bypass" || (port.risky !== "" && port.reachable);
}

/** The words a holder is searched by: what it is called here and in the system. */
export function holderWords(holder: Holder): string {
  return `${holder.kind} ${holder.owner} ${holder.detail}`.toLowerCase();
}

/** The rows a search keeps: a number is a port (or a start of one), anything else is part of what holds it. */
export function matchRows(rows: readonly PortRow[], query: string | undefined): PortRow[] {
  const needle = (query ?? "").trim().toLowerCase();
  if (needle === "") return [...rows];
  const asked = /^\d{1,5}$/.test(needle) ? needle : null;
  return rows.filter((row) => (asked !== null ? String(row.port).startsWith(asked) : row.holders.some((holder) => holderWords(holder).includes(needle))));
}

/** The port a search is exactly about: a whole number between 1 and 65535, else null. */
export function askedPort(query: string | undefined): number | null {
  const needle = (query ?? "").trim();
  if (!/^\d{1,5}$/.test(needle)) return null;
  const port = Number(needle);
  return port >= 1 && port <= 65535 ? port : null;
}

/** Whether nothing holds a port the person asked about: it is free, as far as the server can tell. */
export function isFree(rows: readonly PortRow[], port: number): boolean {
  return !rows.some((row) => row.port === port);
}
