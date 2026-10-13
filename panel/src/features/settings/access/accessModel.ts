/**
 * What a role is, said from the permissions it holds: where it can be held, and how the
 * permissions are grouped for choosing. The server decides every rule; this reproduces only
 * the ceilings it publishes (`team_permissions`, `narrow_permissions`) so a form can say where a
 * role will be usable before it is saved.
 */

import type { ScopeKind } from "../../../api/queries/access";

/**
 * Where a role with these permissions can be held. A role of permissions a project can carry can
 * be held on an environment, a project, a team or the whole server; one that also governs a team
 * (its members) on a team or the server; anything else only on the server.
 */
export function roleScopes(permissions: readonly string[], narrow: readonly string[], team: readonly string[]): ScopeKind[] {
  const held = permissions.filter((name) => name !== "self");
  const within = (ceiling: readonly string[]): boolean => held.every((name) => ceiling.includes(name));
  if (held.length === 0) return [];
  if (within(narrow)) return ["environment", "project", "team", "instance"];
  if (within(team)) return ["team", "instance"];
  return ["instance"];
}

export interface PermissionGroup {
  /** The part before the dot: `apps`, `server`, `secrets`. */
  area: string;
  permissions: string[];
}

/** The permissions a role can choose, grouped by area and sorted; `self` is everyone's and is left out. */
export function groupPermissions(names: readonly string[]): PermissionGroup[] {
  const groups = new Map<string, string[]>();
  for (const name of [...names].sort()) {
    if (name === "self") continue;
    const area = name.includes(".") ? name.slice(0, name.indexOf(".")) : name;
    groups.set(area, [...(groups.get(area) ?? []), name]);
  }
  return [...groups.entries()].map(([area, permissions]) => ({ area, permissions }));
}
