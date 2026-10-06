/**
 * What the signed-in principal may do in a team, so the console draws a button only for what
 * would be allowed. The server decides for real (every route checks the permission in the
 * team of its resource); this is only the same arithmetic done ahead of time, so a viewer is
 * never shown a "New project" it would be refused.
 *
 * A session's `permissions` are the ceiling: what the instance role holds plus everything a
 * team role adds in any team. Inside one team the principal holds its instance role's
 * permissions plus what its role in that team adds, never more than the ceiling (an API token
 * narrowed below its owner stays narrow), exactly as `permissions_in_team` computes it.
 */

import type { SessionInfo } from "../../api/queries/auth";

export interface AccessInput {
  session: Pick<SessionInfo, "permissions" | "role" | "account"> | undefined;
  /** Role name to what it holds, from `GET /api/auth/roles`; undefined until it is read. */
  rolePermissions: Readonly<Record<string, readonly string[]>> | undefined;
  /** Team slug to the principal's role in it. */
  teamRoles: Readonly<Record<string, string | null | undefined>>;
  /** Team role to what it holds, from `GET /api/teams/roles`; undefined until it is read. */
  teamRolePermissions: Readonly<Record<string, readonly string[]>> | undefined;
}

/**
 * Whether the principal holds a permission: anywhere (no team named) or in one team.
 *
 * With no account behind the credential (the access token, a fleet token) the session's
 * permissions are all there is. Until the two role tables are read the answer is the ceiling's,
 * which can only show a button a moment too long, never hide one that is allowed.
 */
export function holds({ session, rolePermissions, teamRoles, teamRolePermissions }: AccessInput, permission: string, team: string | null = null): boolean {
  const ceiling = session?.permissions ?? [];
  if (!ceiling.includes(permission)) return false;
  if (team === null || session?.account == null) return true;
  if (rolePermissions === undefined || teamRolePermissions === undefined) return true;
  const instance = rolePermissions[session.role ?? ""] ?? [];
  if (instance.includes(permission)) return true;
  const role = teamRoles[team];
  return role != null && (teamRolePermissions[role] ?? []).includes(permission);
}
