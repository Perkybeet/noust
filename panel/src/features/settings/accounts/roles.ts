/**
 * The five roles as the console says them (ENS research §4.2.2). What each role may do is the
 * server's (`GET /api/auth/roles`); this only names them and says, in the operator's words,
 * what each is for and which may not be held by one person together.
 */

import type { T } from "../../../i18n";

export const ROLES = ["viewer", "operator", "admin", "security", "auditor"] as const;
export type Role = (typeof ROLES)[number];

export function isRole(value: string): value is Role {
  return (ROLES as readonly string[]).includes(value);
}

/** "Administrator", or the name as the server gave it for a role this console does not know. */
export function roleLabel(t: T, role: string): string {
  if (!isRole(role)) return role;
  switch (role) {
    case "viewer":
      return t("accounts.roles.viewer.label");
    case "operator":
      return t("accounts.roles.operator.label");
    case "admin":
      return t("accounts.roles.admin.label");
    case "security":
      return t("accounts.roles.security.label");
    case "auditor":
      return t("accounts.roles.auditor.label");
  }
}

/** One sentence on what the role is for. */
export function roleDescription(t: T, role: Role): string {
  switch (role) {
    case "viewer":
      return t("accounts.roles.viewer.description");
    case "operator":
      return t("accounts.roles.operator.description");
    case "admin":
      return t("accounts.roles.admin.description");
    case "security":
      return t("accounts.roles.security.description");
    case "auditor":
      return t("accounts.roles.auditor.description");
  }
}

/**
 * Roles one person may not hold in two accounts (spec §2.1): administrator or operator with
 * security, and auditor with any other. The server enforces it; the console says it before
 * the operator asks, and says the exception that exists for small installations.
 */
export function incompatible(first: string, second: string): boolean {
  if (first === second) return false;
  if (first === "auditor" || second === "auditor") return true;
  const pair = new Set([first, second]);
  return pair.has("security") && (pair.has("admin") || pair.has("operator"));
}
