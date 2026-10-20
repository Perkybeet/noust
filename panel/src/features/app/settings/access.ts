/**
 * An application's access policy as a form: what the server stores (a password asked or not,
 * address lists, the header that names the visitor, limits, response headers) turned into
 * what a person edits (text, one entry per line), and back into only the fields that changed.
 * The rules are the server's (`noust.managers.site_policy`); these checks only give a quick
 * answer for the mistakes that cost a round trip, and a refusal from the server lands on the
 * field it names all the same.
 */

import type { PlainKey } from "../../../i18n";
import type { PolicyBody, PolicyFields } from "../../../api/queries/network";

/** What the form holds: text fields hold what was typed, lists hold one entry per line. */
export interface AccessDraft {
  basic_auth: boolean;
  realm: string;
  access: "all" | "any";
  allow: string;
  deny: string;
  real_ip: "off" | "cloudflare" | "custom";
  real_ip_header: string;
  trusted_proxies: string;
  rate_amount: string;
  rate_unit: "s" | "m";
  rate_burst: string;
  connection_limit: string;
  hsts: "default" | "on" | "off";
  noindex: boolean;
  /** Send port 80 to HTTPS once the site has a certificate (nginx); off leaves it answering on both. */
  force_https: boolean;
  headers: string;
  hidden_headers: string;
  /** What happens at prefixes of the site; edited in their own dialog, saved with the rest. */
  routes: PolicyFields["routes"];
  /** Serve the site over HTTP/3 (QUIC) as well. */
  http3: boolean;
  /** Answer only a visitor with a certificate this site's own authority signed. */
  client_certs: boolean;
  /** The web application firewall: off, writing attacks down, or refusing them. */
  waf: "off" | "detect" | "block";
  /** Numbers of the rules left out for this site, separated by spaces, commas or lines. */
  waf_exclude: string;
  /** The authentication service every request is asked about: its address, empty for none. */
  forward_url: string;
  forward_sign_in: string;
  /** Response headers of the service handed to the application, one per line. */
  forward_headers: string;
}

export type AccessField = keyof AccessDraft;

export type AccessErrors = Partial<Record<AccessField, PlainKey>>;

/** The numbers an empty field falls back to: the server's own defaults. */
export const DEFAULT_BURST = 20;

/** Split a list a person typed: one per line, but commas and spaces separate too. Duplicates go. */
export function listOf(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of text.split(/[\s,]+/)) {
    const entry = part.trim();
    if (entry !== "" && !seen.has(entry)) {
      seen.add(entry);
      out.push(entry);
    }
  }
  return out;
}

/** The rule numbers a person typed, once each and in order; what is not a number is left for the check. */
export function ruleNumbers(text: string): number[] {
  return [...new Set(listOf(text).filter((entry) => /^\d+$/.test(entry)).map(Number))].sort((a, b) => a - b);
}

/** Header lines, `Name: value`, as the form shows them. */
export function formatHeaders(headers: Readonly<Record<string, string>>): string {
  return Object.entries(headers)
    .map(([name, value]) => `${name}: ${value}`)
    .join("\n");
}

/** What a header text holds, and the first line that is not `Name: value` (1-based), or null. */
export function parseHeaders(text: string): { headers: Record<string, string>; badLine: number | null } {
  const headers: Record<string, string> = {};
  let badLine: number | null = null;
  text.split("\n").forEach((raw, index) => {
    const line = raw.trim();
    if (line === "") return;
    const colon = line.indexOf(":");
    const name = colon > 0 ? line.slice(0, colon).trim() : "";
    const value = colon > 0 ? line.slice(colon + 1).trim() : "";
    if (name === "" || value === "") {
      badLine ??= index + 1;
      return;
    }
    headers[name] = value;
  });
  return { headers, badLine };
}

/** `10r/s` and `300r/m` as an amount and a unit; anything else is no rate. */
function splitRate(rate: string): { amount: string; unit: "s" | "m" } {
  const match = /^(\d+)r\/([sm])$/.exec(rate.trim());
  return match ? { amount: match[1] ?? "", unit: match[2] === "m" ? "m" : "s" } : { amount: "", unit: "s" };
}

/** The form's fields for a stored policy. */
export function draftOf(policy: PolicyFields): AccessDraft {
  const rate = splitRate(policy.rate_limit);
  return {
    basic_auth: policy.basic_auth,
    realm: policy.realm,
    access: policy.access === "any" ? "any" : "all",
    allow: policy.allow_ips.join("\n"),
    deny: policy.deny_ips.join("\n"),
    real_ip: policy.real_ip === "cloudflare" || policy.real_ip === "custom" ? policy.real_ip : "off",
    real_ip_header: policy.real_ip_header,
    trusted_proxies: policy.trusted_proxies.join("\n"),
    rate_amount: rate.amount,
    rate_unit: rate.unit,
    rate_burst: String(policy.rate_burst),
    connection_limit: policy.connection_limit === 0 ? "" : String(policy.connection_limit),
    hsts: policy.hsts === "on" || policy.hsts === "off" ? policy.hsts : "default",
    noindex: policy.noindex,
    force_https: policy.force_https,
    headers: formatHeaders(policy.headers),
    hidden_headers: policy.hidden_headers.join("\n"),
    routes: policy.routes,
    http3: policy.http3,
    client_certs: policy.client_certs,
    waf: policy.waf === "detect" || policy.waf === "block" ? policy.waf : "off",
    waf_exclude: policy.waf_exclusions.join("\n"),
    forward_url: policy.forward_auth?.url ?? "",
    forward_sign_in: policy.forward_auth?.sign_in ?? "",
    forward_headers: (policy.forward_auth?.headers ?? []).join("\n"),
  };
}

const WHOLE = /^\d+$/;

/** The mistakes that can be told without asking the server. */
export function errorsOf(draft: AccessDraft): AccessErrors {
  const errors: AccessErrors = {};
  if (draft.rate_amount.trim() !== "" && (!WHOLE.test(draft.rate_amount.trim()) || Number(draft.rate_amount) < 1)) {
    errors.rate_amount = "network.access.errors.rate";
  }
  if (draft.rate_burst.trim() !== "" && !WHOLE.test(draft.rate_burst.trim())) errors.rate_burst = "network.access.errors.whole";
  if (draft.connection_limit.trim() !== "" && !WHOLE.test(draft.connection_limit.trim())) errors.connection_limit = "network.access.errors.whole";
  if (parseHeaders(draft.headers).badLine !== null) errors.headers = "network.access.errors.header";
  if (draft.real_ip === "custom" && draft.real_ip_header.trim() === "") errors.real_ip_header = "network.access.errors.realIpHeader";
  if (draft.forward_url.trim() !== "" && !/^https?:\/\/\S+$/.test(draft.forward_url.trim())) errors.forward_url = "network.access.errors.forwardUrl";
  if (listOf(draft.waf_exclude).some((entry) => !/^\d{1,8}$/.test(entry) || Number(entry) < 1)) errors.waf_exclude = "network.access.errors.rule";
  return errors;
}

/** How many fields of the form differ from the stored policy, and what to send for them. */
export function changesOf(draft: AccessDraft, policy: PolicyFields): { count: number; body: PolicyBody } {
  const body: PolicyBody = {};
  let count = 0;
  const differs = <T>(now: T, stored: T): boolean => JSON.stringify(now) !== JSON.stringify(stored);

  if (draft.basic_auth !== policy.basic_auth) {
    body.basic_auth = draft.basic_auth;
    count += 1;
  }
  if (draft.realm.trim() !== policy.realm) {
    body.realm = draft.realm.trim();
    count += 1;
  }
  if (draft.access !== policy.access) {
    body.access = draft.access;
    count += 1;
  }
  const allow = listOf(draft.allow);
  if (differs(allow, policy.allow_ips)) {
    body.allow_ips = allow;
    count += 1;
  }
  const deny = listOf(draft.deny);
  if (differs(deny, policy.deny_ips)) {
    body.deny_ips = deny;
    count += 1;
  }
  if (draft.real_ip !== policy.real_ip) {
    body.real_ip = draft.real_ip;
    count += 1;
  }
  if (draft.real_ip === "custom" && draft.real_ip_header.trim() !== policy.real_ip_header) {
    body.real_ip_header = draft.real_ip_header.trim();
    count += 1;
  }
  const trusted = listOf(draft.trusted_proxies);
  if (draft.real_ip === "custom" && differs(trusted, policy.trusted_proxies)) {
    body.trusted_proxies = trusted;
    count += 1;
  }
  const amount = draft.rate_amount.trim();
  const rate = amount === "" ? "" : `${String(Number(amount))}r/${draft.rate_unit}`;
  if (rate !== policy.rate_limit) {
    body.rate_limit = rate;
    count += 1;
  }
  const burst = draft.rate_burst.trim() === "" ? DEFAULT_BURST : Number(draft.rate_burst);
  if (burst !== policy.rate_burst) {
    body.rate_burst = burst;
    count += 1;
  }
  const connections = draft.connection_limit.trim() === "" ? 0 : Number(draft.connection_limit);
  if (connections !== policy.connection_limit) {
    body.connection_limit = connections;
    count += 1;
  }
  if (draft.hsts !== policy.hsts) {
    body.hsts = draft.hsts;
    count += 1;
  }
  if (draft.noindex !== policy.noindex) {
    body.noindex = draft.noindex;
    count += 1;
  }
  if (draft.force_https !== policy.force_https) {
    body.force_https = draft.force_https;
    count += 1;
  }
  const { headers } = parseHeaders(draft.headers);
  if (differs(Object.entries(headers).sort(), Object.entries(policy.headers).sort())) {
    body.headers = headers;
    count += 1;
  }
  const hidden = listOf(draft.hidden_headers);
  if (differs(hidden, policy.hidden_headers)) {
    body.hidden_headers = hidden;
    count += 1;
  }
  const forward = {
    url: draft.forward_url.trim(),
    sign_in: draft.forward_sign_in.trim(),
    headers: listOf(draft.forward_headers),
  };
  const storedForward = { url: policy.forward_auth?.url ?? "", sign_in: policy.forward_auth?.sign_in ?? "", headers: policy.forward_auth?.headers ?? [] };
  if (differs(forward, storedForward)) {
    body.forward_auth = forward;
    count += 1;
  }
  if (draft.http3 !== policy.http3) {
    body.http3 = draft.http3;
    count += 1;
  }
  if (draft.client_certs !== policy.client_certs) {
    body.client_certs = draft.client_certs;
    count += 1;
  }
  if (draft.waf !== (policy.waf === "" ? "off" : policy.waf)) {
    body.waf = draft.waf;
    count += 1;
  }
  const left = ruleNumbers(draft.waf_exclude);
  if (differs(left, [...policy.waf_exclusions].sort((a, b) => a - b))) {
    body.waf_exclusions = left;
    count += 1;
  }
  if (differs(draft.routes, policy.routes)) {
    body.routes = [...draft.routes];
    count += 1;
  }
  return { count, body };
}

/**
 * Whether a refusal is the server saying the lists would refuse the operator's own address, which
 * is asked about again rather than shown as an error: the one refusal a person can accept.
 */
export function unreachableAuth(message: string | undefined): boolean {
  return (message ?? "").includes("does not answer");
}

/**
 * Whether a refusal is the server saying the authentication service does not answer, which is
 * asked about again rather than shown as an error: with it down every visitor would be refused.
 */
export function lockoutAddress(message: string | undefined): string | null {
  const match = /would refuse your own address, (\S+)/.exec(message ?? "");
  return match?.[1] ?? null;
}

/**
 * Whether a refusal is the server saying no client certificate is valid, so turning the
 * requirement on would shut everybody out: asked about again, because making the first one
 * can wait until after.
 */
export function noValidCertificate(message: string | undefined): boolean {
  return (message ?? "").includes("so no one could open it");
}
