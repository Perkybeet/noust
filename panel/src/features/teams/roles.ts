/**
 * The four roles of a team as the console says them. What each one may do is the server's
 * (`GET /api/teams/roles`); this only names them and says what each is for, in the operator's
 * words, and says "not a member" for a team an administrator sees without belonging to it.
 */

import { isTeamRole } from "../../api/queries/teams";
import type { T } from "../../i18n";

/** "Owner", or "Not a member" for a team seen without belonging to it, or the server's word for one this console does not know. */
export function teamRoleLabel(t: T, role: string | null | undefined): string {
  if (role === null || role === undefined) return t("teams.roles.none");
  if (!isTeamRole(role)) return role;
  switch (role) {
    case "viewer":
      return t("teams.roles.viewer.label");
    case "operator":
      return t("teams.roles.operator.label");
    case "admin":
      return t("teams.roles.admin.label");
    case "owner":
      return t("teams.roles.owner.label");
  }
}

/** One sentence on what the role holds in its team. */
export function teamRoleDescription(t: T, role: "viewer" | "operator" | "admin" | "owner"): string {
  switch (role) {
    case "viewer":
      return t("teams.roles.viewer.description");
    case "operator":
      return t("teams.roles.operator.description");
    case "admin":
      return t("teams.roles.admin.description");
    case "owner":
      return t("teams.roles.owner.description");
  }
}
