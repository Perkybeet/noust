/**
 * What a role is, said from what the server sent: which kind it is (the owner, one that follows Noust,
 * one that was edited, one of your own), the words the console says for the roles Noust ships, and what an
 * account can do from all its roles together. The server decides every rule; this only reads the answer.
 */

import type { Role } from "../../../api/queries/roles";
import type { T } from "../../../i18n";

export type RoleKind = "owner" | "follows" | "edited" | "custom";

/** The roles Noust ships, whose names the console says in its own language. */
export const SHIPPED = ["owner", "admin", "operator", "viewer", "security", "auditor"] as const;
export type ShippedRole = (typeof SHIPPED)[number];

/** The English the server gives a shipped role, so a role the operator renamed keeps the name they gave it. */
const SHIPPED_ENGLISH: Readonly<Record<ShippedRole, string>> = {
  owner: "Owner",
  admin: "Administrator",
  operator: "Operator",
  viewer: "Viewer",
  security: "Security officer",
  auditor: "Auditor",
};

function shipped(name: string): name is ShippedRole {
  return (SHIPPED as readonly string[]).includes(name);
}

/** The kind of a role: the protected owner, one nobody touched, one that was edited, or one of your own. */
export function kindOf(role: Pick<Role, "protected" | "kind" | "edited">): RoleKind {
  if (role.protected) return "owner";
  if (role.kind === "custom") return "custom";
  return role.edited ? "edited" : "follows";
}

/** The role's name for a person: the console's own words for one Noust ships and nobody renamed, the server's otherwise. */
export function roleLabel(t: T, role: Pick<Role, "name" | "label">): string {
  return shipped(role.name) && role.label === SHIPPED_ENGLISH[role.name] ? t(`roles.names.${role.name}.label`) : role.label;
}

/** What the role is for: the console's own sentence for one Noust ships and nobody edited, the server's otherwise. */
export function roleDescription(t: T, role: Pick<Role, "name" | "description" | "edited">): string {
  return shipped(role.name) && !role.edited ? t(`roles.names.${role.name}.description`) : role.description;
}

export interface PermissionGroup {
  /** The part before the dot: `apps`, `server`, `secrets`. */
  area: string;
  permissions: string[];
}

/** The permissions grouped by area and sorted; `self` is everyone's and is left out. */
export function groupPermissions(names: readonly string[]): PermissionGroup[] {
  const groups = new Map<string, string[]>();
  for (const name of [...names].sort()) {
    if (name === "self") continue;
    const area = name.includes(".") ? name.slice(0, name.indexOf(".")) : name;
    groups.set(area, [...(groups.get(area) ?? []), name]);
  }
  return [...groups.entries()].map(([area, permissions]) => ({ area, permissions }));
}

export interface EffectivePermission {
  permission: string;
  /** The roles of the account that give it, in the order the account holds them. */
  from: string[];
}

/**
 * What an account can do from all its roles together, and which role gives each permission: the answer to
 * "why can she do this?" that a list of roles alone does not give. An owner holds everything, so each
 * permission is from the owner role.
 */
export function effectivePermissions(held: readonly string[], roles: readonly Role[]): EffectivePermission[] {
  const byName = new Map(roles.map((role) => [role.name, role]));
  const given = new Map<string, string[]>();
  for (const name of held) {
    for (const permission of byName.get(name)?.permissions ?? []) {
      if (permission === "self") continue;
      given.set(permission, [...(given.get(permission) ?? []), name]);
    }
  }
  return [...given.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([permission, from]) => ({ permission, from }));
}
