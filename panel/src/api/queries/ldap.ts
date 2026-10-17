/**
 * The directories people sign in against (`/api/auth/ldap/directories`): where they are, who
 * searches them, how a person is found, and what a first sign-in does. The service account's
 * password is only ever sent; whether one is kept is all that comes back. Every rule is the
 * server's, and a refusal is shown as it worded it.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type Directories = ResponseOf<"/api/auth/ldap/directories", "get">;
export type Directory = Directories["directories"][number];
export type DirectorySettings = BodyOf<"/api/auth/ldap/directories/{name}", "put">;
export type DirectoryTest = ResponseOf<"/api/auth/ldap/directories/{name}/test", "post">;

export const ldapKeys = {
  all: ["auth", "ldap"] as const,
  directories: ["auth", "ldap", "directories"] as const,
};

/** The directories, with their settings and whether a service account's password is kept. */
export const directoriesQuery = () =>
  queryOptions({
    queryKey: ldapKeys.directories,
    queryFn: ({ signal }) => request("get", "/api/auth/ldap/directories", { signal }),
  });

/** Creates a directory or changes the settings named; what is left out stays. Needs "Confirm it's you". */
export function saveDirectory(name: string, body: DirectorySettings) {
  return request("put", "/api/auth/ldap/directories/{name}", { params: { name }, body });
}

/** Removes a directory and its stored password. The server refuses while that would leave nobody able to sign in. */
export function removeDirectory(name: string) {
  return request("delete", "/api/auth/ldap/directories/{name}", { params: { name } });
}

/** Connects, binds and searches the way a sign-in would; signs nobody in. */
export function testDirectory(name: string) {
  return request("post", "/api/auth/ldap/directories/{name}/test", { params: { name } });
}
