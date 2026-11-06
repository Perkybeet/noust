/**
 * Opening a port, apart from any dialog: what is behind the port a person typed, what Noust would do about it, and
 * what is wrong with the form. Pure, so each rule is tested without drawing anything.
 *
 * There is one way to open a port (owner item 119; the atlas counted six places and four dialogs that ended in a
 * firewall rule). What differs is what the machine needs, and the machine says it:
 *
 * - **a rule**: the program listens on every address, or nothing listens yet, and the firewall only has to let the
 *   port in (`POST /api/server/security/firewall/rules`);
 * - **a relay**: the program listens only on 127.0.0.1, which no firewall rule can reach, so nginx receives the traffic
 *   on a port of its own and passes it on (`POST /api/streams`, which makes its own firewall rule).
 */

import type { StreamBody } from "../../api/queries/network";
import { parseAddresses } from "../../lib/addresses";
import type { Holder, PortRow } from "../server/network/data";

/** The most addresses a relay may be restricted to (the server's `MAX_SOURCES`). */
export const MAX_RELAY_SOURCES = 20;

/** How long a quiet client keeps its session through a relay, as the published-ports dialog always asked. */
const RELAY_TIMEOUT_SECONDS = 3600;

export type OpenProto = "tcp" | "udp" | "any";
export type OpenWho = "any" | "some";

/** What the person typed. Every field is text or a choice: the dialog reads them into a plan. */
export interface OpenForm {
  port: string;
  proto: OpenProto;
  who: OpenWho;
  /** The address or network, or the list of them, when `who` is "some". */
  source: string;
  /** The port nginx answers on, when the program listens only inside. */
  outside: string;
}

export const OPEN_PORT_DEFAULTS: OpenForm = { port: "", proto: "tcp", who: "any", source: "", outside: "" };

/** A whole number from 1 to 65535, or null. */
export function parsePort(text: string): number | null {
  const typed = text.trim();
  if (!/^\d{1,5}$/.test(typed)) return null;
  const port = Number(typed);
  return port >= 1 && port <= 65535 ? port : null;
}

/** How a port stands on this machine. */
export type PortReach =
  /** Nothing holds it: it is opened all the same, and answers when something starts. */
  | "free"
  /** A program listens on every address (or on an address the network reaches). */
  | "outside"
  /** A program listens only on this machine (127.0.0.1): no firewall rule gets traffic to it. */
  | "inside";

export interface PortFact {
  reach: PortReach;
  /** The program that listens, as the kernel says it. */
  process: string | null;
  /** What Noust knows it to be (an application, a database, a published port). */
  holder: Holder | null;
  /** The firewall already lets everybody in. */
  openToAll: boolean;
}

function loopback(address: string | null): boolean {
  return address === "127.0.0.1" || address === "::1" || address === "localhost";
}

/** What listens on a port, for the protocol asked ("any" is whichever of the two does). */
export function factsOf(rows: readonly PortRow[], port: number, proto: OpenProto): PortFact {
  const wanted = proto === "any" ? ["tcp", "udp"] : [proto];
  const row = rows.find((entry) => entry.port === port && wanted.includes(entry.protocol)) ?? null;
  if (row === null) return { reach: "free", process: null, holder: null, openToAll: false };
  const firewall = row.firewall;
  const inside = firewall !== null ? firewall.verdict === "local" : row.holders.length > 0 && row.holders.every((holder) => loopback(holder.address));
  const process = firewall?.process ?? row.holders.find((holder) => holder.kind === "process")?.owner ?? null;
  return { reach: inside ? "inside" : "outside", process, holder: row.holders[0] ?? null, openToAll: firewall?.verdict === "open" };
}

export interface FirewallState {
  active: boolean;
  installed: boolean;
}

/** What is wrong with a field, as a code the dialog says in words; the firewall is about the machine and not a field. */
export interface OpenProblems {
  port?: "invalid";
  proto?: "one";
  source?: "needed" | "invalid" | "one" | "many";
  outside?: "invalid" | "same";
  firewall?: "off" | "missing";
}

export type OpenPlan =
  | { mode: "rule"; port: number; proto: OpenProto; source: string; fact: PortFact }
  | { mode: "relay"; port: number; listen: number; proto: "tcp" | "udp"; sources: string[]; target: { host: string; port: number }; fact: PortFact };

/**
 * The plan for a form, or why there is none. A field is judged as the server will judge it again; the firewall's own
 * state is judged last, because a rule is only worth offering where a firewall filters.
 */
export function planOf(form: OpenForm, rows: readonly PortRow[], firewall: FirewallState): { plan: OpenPlan | null; problems: OpenProblems } {
  const problems: OpenProblems = {};
  const port = parsePort(form.port);
  if (port === null) return { plan: null, problems: { port: "invalid" } };
  const fact = factsOf(rows, port, form.proto);
  const relay = fact.reach === "inside";

  const sources: string[] = [];
  if (form.who === "some") {
    const parsed = parseAddresses(form.source);
    if (parsed.entries.length === 0 && parsed.invalid.length === 0) problems.source = "needed";
    else if (parsed.invalid.length > 0) problems.source = "invalid";
    else if (!relay && parsed.entries.length > 1) problems.source = "one";
    else if (relay && parsed.entries.length > MAX_RELAY_SOURCES) problems.source = "many";
    sources.push(...parsed.entries);
  }

  let listen = port;
  if (relay) {
    const outside = parsePort(form.outside);
    if (outside === null) problems.outside = "invalid";
    else if (outside === port) problems.outside = "same";
    else listen = outside;
    // nginx proxies TCP or UDP, one protocol for each published port.
    if (form.proto === "any") problems.proto = "one";
  }
  if (Object.keys(problems).length > 0) return { plan: null, problems };

  if (relay) {
    return { plan: { mode: "relay", port, listen, proto: form.proto === "udp" ? "udp" : "tcp", sources, target: { host: "127.0.0.1", port }, fact }, problems };
  }
  if (!firewall.installed) return { plan: null, problems: { firewall: "missing" } };
  if (!firewall.active) return { plan: null, problems: { firewall: "off" } };
  return { plan: { mode: "rule", port, proto: form.proto, source: form.who === "some" ? (sources[0] ?? "any") : "any", fact }, problems };
}

/** The firewall rule a form asks for: what `POST /api/server/security/firewall/rules` takes. */
export function ruleBody(form: OpenForm): { action: "allow"; port: number; proto: OpenProto; source: string; comment: string } {
  const sources = form.who === "some" ? parseAddresses(form.source).entries : [];
  return { action: "allow", port: parsePort(form.port) ?? 0, proto: form.proto, source: sources[0] ?? "any", comment: "" };
}

/** A file name for the relay: what it carries, then the port it answers on (`redis-server-16379`). */
function relayName(fact: PortFact, listen: number): string {
  const word = (fact.process ?? fact.holder?.owner ?? "port")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 30)
    .replace(/-+$/g, "");
  return `${word === "" ? "port" : word}-${String(listen)}`;
}

/** The relay a form asks for: what `POST /api/streams` takes. */
export function streamBody(form: OpenForm, fact: PortFact = { reach: "inside", process: null, holder: null, openToAll: false }): StreamBody {
  const port = parsePort(form.port) ?? 0;
  const listen = parsePort(form.outside) ?? 0;
  return {
    name: relayName(fact, listen),
    protocol: form.proto === "udp" ? "udp" : "tcp",
    listen_port: listen,
    target_port: port,
    target_host: "127.0.0.1",
    sources: form.who === "some" ? parseAddresses(form.source).entries : [],
    timeout_seconds: RELAY_TIMEOUT_SECONDS,
  };
}
