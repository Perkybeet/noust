/**
 * The one list of roles (`/api/roles`, item 96): the five Noust ships and the owner, which are edited,
 * duplicated and put back like any other, and the operator's own. An account holds several of them and
 * what it can do is what they hold together. Every rule (the protected owner, the last owner, a role
 * in use, which roles may not be held together) is the server's; the console asks and shows the answer.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type RolesList = ResponseOf<"/api/roles", "get">;
export type Role = RolesList["roles"][number];
export type RoleCreate = BodyOf<"/api/roles", "post">;
export type RoleUpdate = BodyOf<"/api/roles/{name}", "patch">;
export type RoleDuplicate = BodyOf<"/api/roles/{name}/duplicate", "post">;
export type RoleOffers = BodyOf<"/api/roles/{name}/offers", "post">;
export type RolesReview = ResponseOf<"/api/roles/review", "get">;

export const roleKeys = {
  list: ["roles"] as const,
  review: ["roles", "review"] as const,
};

/** Every role and every permission there is, with what each allows. */
export const rolesQuery = () =>
  queryOptions({
    queryKey: roleKeys.list,
    queryFn: ({ signal }) => request("get", "/api/roles", { signal }),
  });

/** What the frameworks in force make of the roles: which join duties they keep apart, and the owners left. */
export const rolesReviewQuery = () =>
  queryOptions({
    queryKey: roleKeys.review,
    queryFn: ({ signal }) => request("get", "/api/roles/review", { signal }),
  });

export function createRole(body: RoleCreate) {
  return request("post", "/api/roles", { body });
}

export function updateRole(name: string, body: RoleUpdate) {
  return request("patch", "/api/roles/{name}", { params: { name }, body });
}

export function deleteRole(name: string) {
  return request("delete", "/api/roles/{name}", { params: { name } });
}

export function duplicateRole(name: string, body: RoleDuplicate) {
  return request("post", "/api/roles/{name}/duplicate", { params: { name }, body });
}

/** Puts a role Noust ships back as it comes: its permissions, and the ones a newer release adds. */
export function resetRole(name: string) {
  return request("post", "/api/roles/{name}/reset", { params: { name } });
}

/** Takes or leaves, for good, the permissions a newer release offers an edited role. */
export function answerOffers(name: string, body: RoleOffers) {
  return request("post", "/api/roles/{name}/offers", { params: { name }, body });
}

/** Sets every role an account holds; at least one, the one it is known by first. */
export function setAccountRoles(username: string, roles: string[]) {
  return request("put", "/api/auth/accounts/{username}/roles", { params: { username }, body: { roles } });
}
