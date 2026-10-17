/**
 * The routes of an application's site as a form: what the server stores (a prefix, what it
 * does, who may reach it, what it sends) turned into what a person edits, and the quick checks
 * that save a round trip. The rules are the server's (`noust.managers.site_routes`); a refusal
 * from it is shown all the same.
 */

import type { PlainKey } from "../../../i18n";
import type { PolicyFields } from "../../../api/queries/network";
import { formatHeaders, parseHeaders } from "./access";

/** One route as the server holds and sends it. */
export type PolicyRoute = PolicyFields["routes"][number];

export type RouteKind = "site" | "proxy" | "redirect";
export type RouteAuth = "inherit" | "password" | "open";
export const REDIRECT_CODES = [301, 302, 307, 308] as const;

/** What the dialog holds: text as typed. */
export interface RouteDraft {
  path: string;
  kind: RouteKind;
  auth: RouteAuth;
  port: string;
  strip: boolean;
  to: string;
  code: string;
  keepPath: boolean;
  headers: string;
}

export type RouteField = keyof RouteDraft;
export type RouteErrors = Partial<Record<RouteField, PlainKey>>;

/** The dialog's fields for a route, or for a new one. */
export function draftOfRoute(route?: PolicyRoute): RouteDraft {
  return {
    path: route?.path ?? "",
    kind: route?.kind === "proxy" || route?.kind === "redirect" ? route.kind : "site",
    auth: route?.auth === "password" || route?.auth === "open" ? route.auth : "inherit",
    port: route !== undefined && route.port > 0 ? String(route.port) : "",
    strip: route?.strip ?? false,
    to: route?.to ?? "",
    code: String(route?.code ?? 301),
    keepPath: route?.keep_path ?? true,
    headers: formatHeaders(route?.headers ?? {}),
  };
}

/** A prefix as the server spells it: one leading slash, none at the end, spaces trimmed. */
export function normalPath(text: string): string {
  const trimmed = text.trim();
  return trimmed.length > 1 ? trimmed.replace(/\/$/, "") : trimmed;
}

const PATH = /^\/[A-Za-z0-9._~-]+(?:\/[A-Za-z0-9._~-]+)*$/;
const PORT = /^\d{1,5}$/;

/** The mistakes that can be told without asking the server. */
export function errorsOfRoute(draft: RouteDraft, others: readonly string[]): RouteErrors {
  const errors: RouteErrors = {};
  const path = normalPath(draft.path);
  if (!PATH.test(path)) errors.path = "network.access.routes.errors.path";
  else if (others.includes(path)) errors.path = "network.access.routes.errors.same";
  if (draft.kind === "proxy" && (!PORT.test(draft.port.trim()) || Number(draft.port) < 1 || Number(draft.port) > 65535)) {
    errors.port = "network.access.routes.errors.port";
  }
  if (draft.kind === "redirect" && draft.to.trim() === "") errors.to = "network.access.routes.errors.target";
  if (draft.kind !== "redirect" && parseHeaders(draft.headers).badLine !== null) errors.headers = "network.access.routes.errors.header";
  if (draft.kind === "site" && draft.auth === "inherit" && Object.keys(parseHeaders(draft.headers).headers).length === 0) {
    errors.auth = "network.access.routes.errors.nothing";
  }
  return errors;
}

/** The route a draft describes, in the shape the server takes. */
export function routeOf(draft: RouteDraft): PolicyRoute {
  const path = normalPath(draft.path);
  if (draft.kind === "redirect") {
    return { path, kind: "redirect", auth: "inherit", port: 0, strip: false, to: draft.to.trim(), code: Number(draft.code), keep_path: draft.keepPath, headers: {} };
  }
  return {
    path,
    kind: draft.kind,
    auth: draft.auth,
    port: draft.kind === "proxy" ? Number(draft.port) : 0,
    strip: draft.kind === "proxy" && draft.strip,
    to: "",
    code: 301,
    keep_path: true,
    headers: parseHeaders(draft.headers).headers,
  };
}

/** The list with a route added, or put in place of the one at its path; longest path first, as the server keeps it. */
export function withRoute(routes: readonly PolicyRoute[], route: PolicyRoute): PolicyRoute[] {
  return sorted([...routes.filter((existing) => existing.path !== route.path), route]);
}

/** The list without the route at a path. */
export function withoutRoute(routes: readonly PolicyRoute[], path: string): PolicyRoute[] {
  return routes.filter((existing) => existing.path !== path);
}

/** Longest path first, then alphabetical: the order the server returns them in. */
export function sorted(routes: readonly PolicyRoute[]): PolicyRoute[] {
  return [...routes].sort((a, b) => b.path.length - a.path.length || (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
}
