/**
 * Roles and where they are held (`/api/access/roles`, `/api/access/bindings`): the built-in team
 * roles, the roles an operator defines, and a role given to an account on the whole server, a
 * team, a project or an environment. Every rule - which permissions a role may mix, what a
 * project can carry, the one role of an account under the ENS profile - is the server's; the
 * console asks and shows its answer, and a refusal is shown as the server worded it.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type AccessRoles = ResponseOf<"/api/access/roles", "get">;
export type AccessRole = AccessRoles["roles"][number];
export type AccessBindings = ResponseOf<"/api/access/bindings", "get">;
export type AccessBinding = AccessBindings["bindings"][number];
export type CreateRoleBody = BodyOf<"/api/access/roles", "post">;
export type UpdateRoleBody = BodyOf<"/api/access/roles/{role}", "patch">;
export type BindBody = BodyOf<"/api/access/bindings", "post">;

/** Where a role can be held, widest first. */
export const SCOPE_KINDS = ["instance", "team", "project", "environment"] as const;
export type ScopeKind = (typeof SCOPE_KINDS)[number];

export function isScopeKind(value: string): value is ScopeKind {
  return SCOPE_KINDS.some((kind) => kind === value);
}

export const accessKeys = {
  all: ["access"] as const,
  roles: ["access", "roles"] as const,
  bindings: ["access", "bindings"] as const,
};

/** The roles, the permissions with their meaning, and what a team and a project can carry. */
export const accessRolesQuery = () =>
  queryOptions({
    queryKey: accessKeys.roles,
    queryFn: ({ signal }) => request("get", "/api/access/roles", { signal }),
  });

/** Every role given to an account at a scope, the memberships of teams included. */
export const accessBindingsQuery = () =>
  queryOptions({
    queryKey: accessKeys.bindings,
    queryFn: ({ signal }) => request("get", "/api/access/bindings", { signal }),
  });

export function createRole(body: CreateRoleBody) {
  return request("post", "/api/access/roles", { body });
}

export function updateRole(role: string, body: UpdateRoleBody) {
  return request("patch", "/api/access/roles/{role}", { params: { role }, body });
}

/** Deletes a role nobody holds; the server refuses while an account does. */
export function deleteRole(role: string) {
  return request("delete", "/api/access/roles/{role}", { params: { role } });
}

/** Gives an account a role at a scope. Needs "Confirm it's you", and a second person where approvals apply. */
export function bindRole(body: BindBody) {
  return request("post", "/api/access/bindings", { body });
}

/** Takes a role from an account at a scope. */
export function unbindRole(id: number) {
  return request("delete", "/api/access/bindings/{binding_id}", { params: { binding_id: id } });
}
