/**
 * Reading a DNS check: the verdict, which address is which, and what to do about it. Pure.
 */

import type { DnsCheck } from "../../api/queries/domains";

export type DnsVerdictKind = "here" | "cdn" | "unknown" | "elsewhere" | "missing";

/**
 * What a check says, in the server's own terms: `cdn` when every address is a CDN's proxy (which
 * reaches this server by itself) and `unknown` when this server only sees private addresses and
 * cannot say what its public one is. An older server sends neither, and the check is read from
 * what it resolves to.
 */
export function dnsVerdict(check: Pick<DnsCheck, "points_here" | "resolved_addresses"> & { verdict?: string | undefined }): DnsVerdictKind {
  if (check.verdict === "cdn") return "cdn";
  if (check.verdict === "unknown") return "unknown";
  if (check.points_here) return "here";
  return check.resolved_addresses.length === 0 ? "missing" : "elsewhere";
}

/** The CDNs the server recognises, by the id it sends, as their operators write them. */
const CDN_NAMES: Readonly<Record<string, string>> = {
  cloudflare: "Cloudflare",
  fastly: "Fastly",
  bunnycdn: "Bunny CDN",
  arvancloud: "ArvanCloud",
};

/** A CDN's name as a person reads it; one this console does not know keeps the server's id. */
export function cdnName(id: string | null | undefined): string {
  return id === null || id === undefined ? "" : (CDN_NAMES[id] ?? id);
}

/** Whether a verdict means visitors and the certificate authority can reach the server. */
export function reachesServer(verdict: DnsVerdictKind): boolean {
  return verdict === "here" || verdict === "cdn";
}

/** IPv4 private ranges (RFC 1918) and carrier-grade NAT: addresses no public record points at. */
export function isPrivateAddress(address: string): boolean {
  const parts = address.split(".").map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part))) return address.toLowerCase().startsWith("fd");
  const [a = 0, b = 0] = parts;
  return a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 100 && b >= 64 && b <= 127);
}

export interface DnsRecordAdvice {
  type: "A" | "AAAA";
  value: string;
}

/** The records to create so a name points here: one A per IPv4 address, one AAAA per IPv6. */
export function recordsToCreate(expected: readonly string[]): DnsRecordAdvice[] {
  return expected
    .filter((address) => !isPrivateAddress(address))
    .map((address) => ({ type: address.includes(":") ? "AAAA" : "A", value: address }));
}
