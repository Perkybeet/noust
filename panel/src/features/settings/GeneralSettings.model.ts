/**
 * What the General and About subsections read out of the configuration and the update status,
 * apart from any page: pure, so every rule is tested without rendering.
 *
 * GET /api/config answers the whole file as an untyped tree (secrets redacted). The keys read
 * here are the ones no typed endpoint serves: `server.name`, `web.public_url`, `web.hooks_url`,
 * `updates.check` and the role of this Noust.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../../api/client";
import type { ResponseOf } from "../../api/client";
import type { ConsoleConfig } from "../../api/queries/config";
import type { T } from "../../i18n";

/** How this installation updates itself: its package manager, and whether the console can run it. */
export type SelfUpdateStatus = ResponseOf<"/api/system/update", "get">;

export const selfUpdateQuery = () =>
  queryOptions({
    queryKey: ["system", "self-update"] as const,
    queryFn: ({ signal }) => request("get", "/api/system/update", { signal }),
    staleTime: 10 * 60_000,
  });

type Tree = Record<string, unknown>;

function isTree(value: unknown): value is Tree {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function branch(tree: unknown, key: string): Tree {
  if (!isTree(tree)) return {};
  const value = tree[key];
  return isTree(value) ? value : {};
}

function text(tree: Tree, key: string): string {
  const value = tree[key];
  return typeof value === "string" ? value : typeof value === "number" ? String(value) : "";
}

export interface ServerIdentity {
  /** `server.name`: "" means the machine's own short host name. */
  name: string;
  /** `web.public_url`: where a browser reaches the console; "" when never set. */
  publicUrl: string;
  /** `web.hooks_url`: the public base of /hooks, written by `noust web expose-hooks`. */
  hooksUrl: string;
  /** `updates.check`: whether Noust asks for new releases. On unless turned off. */
  checkUpdates: boolean;
  /** `central.role`: "hub" only manages other servers. */
  role: "server" | "hub";
  /** `service_user`: who applications run as. */
  serviceUser: string;
}

/** The General subsection's untyped settings, out of the whole configuration. */
export function readServerIdentity(config: ConsoleConfig["config"]): ServerIdentity {
  const server = branch(config, "server");
  const web = branch(config, "web");
  const updates = branch(config, "updates");
  const central = branch(config, "central");
  return {
    name: text(server, "name"),
    publicUrl: text(web, "public_url"),
    hooksUrl: text(web, "hooks_url"),
    checkUpdates: updates["check"] !== false,
    role: text(central, "role") === "hub" ? "hub" : "server",
    serviceUser: text(isTree(config) ? config : {}, "service_user"),
  };
}

/** Where this installation's updates come from, in words: "the apt repository". */
export function installMethodWords(t: T, method: string | undefined): string {
  switch (method) {
    case "apt":
      return t("settings.about.method.apt");
    case "dnf":
    case "yum":
      return t("settings.about.method.rpm", { tool: method });
    case "zypper":
      return t("settings.about.method.zypper");
    case "pip":
    case "pipx":
      return t("settings.about.method.pip", { tool: method });
    case "source":
      return t("settings.about.method.source");
    default:
      return t("settings.about.method.unknown");
  }
}

/**
 * A whole number typed into a field, as a number; anything else is sent as typed, so the
 * server's refusal is shown beside the field. The server is the one validator.
 */
export function wholeNumber(value: string): number {
  const trimmed = value.trim();
  return /^-?\d+$/.test(trimmed) ? Number(trimmed) : (trimmed as unknown as number);
}
