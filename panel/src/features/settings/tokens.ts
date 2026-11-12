/**
 * API tokens as the console presents them: what each scope allows, the expiry choices, and a
 * token's state from its record.
 */

import type { ApiToken } from "../../api/queries/auth";
import type { T } from "../../i18n";
import { formatDate } from "../../lib/format";

export type TokenScope = "read" | "deploy" | "admin";

export interface ScopeOption {
  value: TokenScope;
  label: string;
  /** What a token of this scope can do, in the operator's words. */
  description: string;
}

/**
 * The scopes, weakest first. Each includes everything the one before it can do; the policy
 * itself is the backend's (noust.web.auth.required_scope), this only says it in words.
 */
export function scopes(t: T): readonly ScopeOption[] {
  return [
    { value: "read", label: t("settings.tokens.scopes.read.label"), description: t("settings.tokens.scopes.read.description") },
    { value: "deploy", label: t("settings.tokens.scopes.deploy.label"), description: t("accounts.tokens.scopeDeploy") },
    { value: "admin", label: t("settings.tokens.scopes.admin.label"), description: t("accounts.tokens.scopeAdmin") },
  ];
}

/** What a token will hold, and what was left out of what it was asked to hold, and by whom. */
export interface HeldPermissions {
  kept: string[];
  /** What its owner may not do, or the issuer when the token has no owner: never more than they hold. */
  droppedByOwner: string[];
  /** What a token tied to a team, a project or an environment never reaches: the server's own. */
  droppedByReach: string[];
}

/**
 * What a token will hold: what it is asked to (a scope's, a preset's), cut to what its owner may do today, and, when it
 * is tied to a team, a project or an environment, to what a team can own (`tenant`: the server's list, `null` when it is
 * not tied or the server does not say). `self`, which every credential keeps, is not listed.
 */
export function heldPermissions(wanted: readonly string[], own: readonly string[], tenant: readonly string[] | null): HeldPermissions {
  const asked = wanted.filter((permission) => permission !== "self");
  const byOwner = asked.filter((permission) => own.includes(permission));
  const inReach = tenant === null ? byOwner : byOwner.filter((permission) => tenant.includes(permission));
  return {
    kept: inReach,
    droppedByOwner: asked.filter((permission) => !own.includes(permission)),
    droppedByReach: tenant === null ? [] : byOwner.filter((permission) => !tenant.includes(permission)),
  };
}

export interface ExpiryOption {
  value: string;
  label: string;
  /** Lifetime in hours; null never expires. */
  hours: number | null;
}

export function expiryOptions(t: T): readonly ExpiryOption[] {
  return [
    { value: "7", label: t("settings.tokens.expiry.7"), hours: 7 * 24 },
    { value: "30", label: t("settings.tokens.expiry.30"), hours: 30 * 24 },
    { value: "90", label: t("settings.tokens.expiry.90"), hours: 90 * 24 },
    { value: "365", label: t("settings.tokens.expiry.365"), hours: 365 * 24 },
    { value: "never", label: t("settings.tokens.expiry.never"), hours: null },
  ];
}

export const DEFAULT_EXPIRY = "90";

export type TokenState = "active" | "expired" | "revoked";

/** Whether a token still authenticates, from its record. Revocation outranks expiry. */
export function tokenState(token: Pick<ApiToken, "expires_at" | "revoked_at">, now: number = Date.now()): TokenState {
  if (token.revoked_at !== null && token.revoked_at !== undefined) return "revoked";
  if (token.expires_at !== null && token.expires_at !== undefined && token.expires_at * 1000 <= now) return "expired";
  return "active";
}

const STATE_ORDER: Record<TokenState, number> = { active: 0, expired: 1, revoked: 2 };

/** Live tokens first, newest first within each state. */
export function sortTokens(tokens: readonly ApiToken[], now: number = Date.now()): ApiToken[] {
  return [...tokens].sort(
    (a, b) => STATE_ORDER[tokenState(a, now)] - STATE_ORDER[tokenState(b, now)] || b.created_at - a.created_at,
  );
}

/** "expires Dec 24, 2026", or "never expires", for a Unix timestamp or null. */
export function expiryPhrase(t: T, expiresAt: number | null): string {
  return expiresAt === null
    ? t("settings.tokens.expiry.neverExpires")
    : t("settings.tokens.expiry.expires", { date: formatDate(new Date(expiresAt * 1000), {}, t.locale) });
}
