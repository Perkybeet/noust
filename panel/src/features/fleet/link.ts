/**
 * Servers that dial in to this central: what the console reads of `/api/fleet/link` and the
 * calls that invite one and accept its code, and what an operator's paste is read as before
 * anything is sent.
 *
 * A server behind NAT (or a carrier's CGNAT) has no SSH port this central can reach. It runs a
 * command that keeps a connection to this central open and prints a link code, `noust-link:v1:`
 * and base64url JSON, mirroring `noust.fleet.link.code` (LINK_PREFIX). The code carries a token:
 * nothing here repeats it, and the summary read from it leaves it out.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../../api/client";
import type { BodyOf, ResponseOf } from "../../api/client";
import type { NodeRecord } from "./nodes";

export type LinkStatus = ResponseOf<"/api/fleet/link", "get">;
export type LinkEntry = LinkStatus["links"][number];
export type Invite = ResponseOf<"/api/fleet/link/invites", "post">;
export type InviteBody = BodyOf<"/api/fleet/link/invites", "post">;
export type AcceptBody = BodyOf<"/api/fleet/link/accept", "post">;

export const linkKeys = {
  all: ["fleet", "link"] as const,
  status: ["fleet", "link", "status"] as const,
};

/** Whether this central takes servers that dial in, and which do. */
export const linkStatusQuery = () =>
  queryOptions({
    queryKey: linkKeys.status,
    queryFn: ({ signal }) => request("get", "/api/fleet/link", { signal }),
  });

/** Gives a server a port of this central and the command to run on it. Sudo mode. */
export function inviteServer(body: InviteBody): Promise<Invite> {
  return request("post", "/api/fleet/link/invites", { body });
}

/** Forgets an invitation nobody answered. Sudo mode. */
export function cancelInvite(name: string): Promise<void> {
  return request("delete", "/api/fleet/link/invites/{name}", { params: { name } });
}

/** Registers a server from its link code, once it connects and answers with its token. Sudo mode. */
export function acceptServer(body: AcceptBody): Promise<NodeRecord> {
  return request("post", "/api/fleet/link/accept", { body });
}

// -- What was pasted ----------------------------------------------------------------------------

export const LINK_PREFIX = "noust-link:v1:";

/** The code on its own line, wherever it is in what was pasted. */
const CODE = /noust-link:v1:[A-Za-z0-9_-]+/;
const OTHER_VERSION = /noust-link:(?!v1:)[A-Za-z0-9]+:/;

export type LinkCodeProblem = "empty" | "joinCode" | "apiToken" | "consoleToken" | "newer" | "wrongPrefix" | "unreadable";

export type LinkCodeReading = { ok: true; code: string; summary: LinkCodeSummary } | { ok: false; problem: LinkCodeProblem };

/** What the code says, without its token: shown so the operator can check it is the right one. */
export interface LinkCodeSummary {
  /** The name the server suggests for itself. */
  node: string;
  /** The central it linked to, by the name its invitation gave. */
  central: string;
  consolePort: number;
  version: string;
}

function decodeBase64Url(payload: string): string | null {
  try {
    const base64 = payload.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (payload.length % 4)) % 4);
    const binary = atob(base64);
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    // Not base64url, or not UTF-8: not a link code, whatever its prefix says.
    return null;
  }
}

function text(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

/** Reads the summary out of a code; null when it is not what a server prints. */
export function linkSummaryOf(code: string): LinkCodeSummary | null {
  const json = decodeBase64Url(code.slice(LINK_PREFIX.length));
  if (json === null) return null;
  let document: unknown;
  try {
    document = JSON.parse(json);
  } catch {
    // Decoded, but not JSON.
    return null;
  }
  if (typeof document !== "object" || document === null || Array.isArray(document)) return null;
  const fields = document as Record<string, unknown>;
  const node = text(fields["node_name"]);
  const central = text(fields["central"]);
  const version = text(fields["noust_version"]);
  const consolePort = fields["console_port"];
  if (node === null || central === null || version === null || text(fields["token"]) === null || text(fields["link_key"]) === null) return null;
  if (typeof consolePort !== "number" || !Number.isInteger(consolePort) || consolePort < 1 || consolePort > 65535) return null;
  return { node, central, consolePort, version };
}

/**
 * Tells what was pasted: a link code (the line itself, found even inside the whole block the
 * terminal printed), or which mistake it is.
 */
export function classifyLinkCode(pasted: string): LinkCodeReading {
  const value = pasted.trim();
  if (value === "") return { ok: false, problem: "empty" };
  const match = CODE.exec(value);
  if (match !== null) {
    const summary = linkSummaryOf(match[0]);
    return summary === null ? { ok: false, problem: "unreadable" } : { ok: true, code: match[0], summary };
  }
  if (OTHER_VERSION.test(value)) return { ok: false, problem: "newer" };
  if (/noust-join:/.test(value)) return { ok: false, problem: "joinCode" };
  if (/(?:^|\s)(?:noust|wasm)_tok_/.test(value)) return { ok: false, problem: "apiToken" };
  if (/(?:^|\s)(?:noust|wasm)_[A-Za-z0-9_-]{8,}/.test(value)) return { ok: false, problem: "consoleToken" };
  return { ok: false, problem: "wrongPrefix" };
}
