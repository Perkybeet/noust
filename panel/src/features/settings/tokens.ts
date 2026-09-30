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
