/**
 * Accounts, invitations and separation-of-duties exceptions (`/api/auth/accounts`,
 * `/api/auth/invitations`, `/api/auth/exceptions`), the central's own. Every rule - the
 * password policy, which roles one person may not hold together, single-use invitations - is
 * the server's; the console asks and shows the answer.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../../../api/client";
import type { BodyOf, ResponseOf } from "../../../api/client";

export type AccountsResponse = ResponseOf<"/api/auth/accounts", "get">;
export type Account = AccountsResponse["accounts"][number];
export type SeparationConflict = AccountsResponse["conflicts"][number];
export type AccountCreate = BodyOf<"/api/auth/accounts", "post">;
export type InvitationCreate = BodyOf<"/api/auth/invitations", "post">;
export type InvitationIssued = ResponseOf<"/api/auth/invitations", "post">;
export type ExceptionsResponse = ResponseOf<"/api/auth/exceptions", "get">;
export type SodException = ExceptionsResponse["exceptions"][number];
export type ExceptionCreate = BodyOf<"/api/auth/exceptions", "post">;
export type RolesResponse = ResponseOf<"/api/auth/roles", "get">;

export const accountKeys = {
  list: ["auth", "accounts"] as const,
  exceptions: ["auth", "exceptions"] as const,
  roles: ["auth", "roles"] as const,
};

export const accountsQuery = () =>
  queryOptions({
    queryKey: accountKeys.list,
    queryFn: ({ signal }) => request("get", "/api/auth/accounts", { signal }),
  });

export const exceptionsQuery = () =>
  queryOptions({
    queryKey: accountKeys.exceptions,
    queryFn: ({ signal }) => request("get", "/api/auth/exceptions", { signal }),
  });

/** Every role and the permissions it holds, with each permission described. */
export const rolesQuery = () =>
  queryOptions({
    queryKey: accountKeys.roles,
    queryFn: ({ signal }) => request("get", "/api/auth/roles", { signal }),
    staleTime: Infinity,
  });

export function createAccount(body: AccountCreate) {
  return request("post", "/api/auth/accounts", { body });
}

export function invite(body: InvitationCreate) {
  return request("post", "/api/auth/invitations", { body });
}

export function setRole(username: string, role: string) {
  return request("patch", "/api/auth/accounts/{username}", { params: { username }, body: { role } });
}

export function disableAccount(username: string, reason: string | null) {
  return request("post", "/api/auth/accounts/{username}/disable", { params: { username }, body: { reason } });
}

export function enableAccount(username: string) {
  return request("post", "/api/auth/accounts/{username}/enable", { params: { username } });
}

export function unlockAccount(username: string) {
  return request("post", "/api/auth/accounts/{username}/unlock", { params: { username } });
}

export function resetMfa(username: string) {
  return request("post", "/api/auth/accounts/{username}/reset-mfa", { params: { username } });
}

export function removeAccount(username: string) {
  return request("delete", "/api/auth/accounts/{username}", { params: { username } });
}

export function createException(body: ExceptionCreate) {
  return request("post", "/api/auth/exceptions", { body });
}

export function revokeException(id: number) {
  return request("delete", "/api/auth/exceptions/{exception_id}", { params: { exception_id: id } });
}
