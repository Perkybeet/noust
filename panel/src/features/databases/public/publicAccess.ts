/**
 * What the form that makes a database public needs to know beyond what the server decides:
 * how the list of addresses is read, which accounts it can make, and which engines can refuse a
 * connection without TLS. The server checks everything again; this keeps a typo from costing a
 * round trip.
 */

import type { PublicBody } from "../../../api/queries/publicAccess";
import { engineFamilyOf } from "../engineKey";

/** What the account made for the database may do, as the API names it. */
export const ACCESS_PROFILES = ["owner", "read_write", "read_only"] as const;
export type AccessProfile = (typeof ACCESS_PROFILES)[number];

export type TlsMode = "required" | "optional";

/** Engines that can refuse an account that does not use TLS; the others can only say so. */
const TLS_ENFORCING: ReadonlySet<string> = new Set(["postgresql", "mysql"]);

/** Whether the engine can require TLS of the account Noust makes. */
export function canRequireTls(engine: string): boolean {
  return TLS_ENFORCING.has(engineFamilyOf(engine));
}

const IPV4 = /^(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\/(?:3[0-2]|[12]?\d))?$/;
const IPV6 = /^(?=.*:)[0-9a-fA-F:.]+(?:\/(?:12[0-8]|1[01]\d|\d?\d))?$/;

/** An address or a network, as the form accepts it (the server decides what it really is). */
export function isAddressOrNetwork(text: string): boolean {
  return IPV4.test(text) || IPV6.test(text);
}

export interface ParsedSources {
  sources: string[];
  /** The entries that are neither an address nor a network, in the order typed. */
  invalid: string[];
}

/** Reads "203.0.113.7, 10.0.0.0/24" (commas, spaces or lines), without repeats. */
export function parseSources(text: string): ParsedSources {
  const entries = [...new Set(text.split(/[\s,;]+/).filter((entry) => entry !== ""))];
  return { sources: entries.filter(isAddressOrNetwork), invalid: entries.filter((entry) => !isAddressOrNetwork(entry)) };
}

/** The most addresses one rule may name (`PublicRequest.sources`). */
export const MAX_SOURCES = 20;

export interface PublicForm {
  port: string;
  sources: string;
  profile: AccessProfile;
  tls: TlsMode;
  bind: string;
}

/** The form's starting values: an engine that cannot refuse plaintext starts on optional, said plainly. */
export function initialForm(engine: string): PublicForm {
  return { port: "", sources: "", profile: "owner", tls: canRequireTls(engine) ? "required" : "optional", bind: "" };
}

/** A port as typed: whole, 1024 to 65535, or empty for "a free one next to the engine's". */
export function validPort(text: string): boolean {
  const trimmed = text.trim();
  if (trimmed === "") return true;
  const port = Number(trimmed);
  return /^\d+$/.test(trimmed) && port >= 1024 && port <= 65535;
}

/** The request the form asks the plan and the opening with. Throws nothing: invalid parts are left out. */
export function requestOf(engine: string, database: string, form: PublicForm): Omit<PublicBody, "confirm"> {
  const port = form.port.trim() === "" || !validPort(form.port) ? null : Number(form.port);
  const bind = form.bind.trim();
  return {
    engine,
    database,
    port,
    bind_address: bind === "" ? null : bind,
    sources: parseSources(form.sources).sources,
    tls: form.tls,
    profile: form.profile,
  };
}
