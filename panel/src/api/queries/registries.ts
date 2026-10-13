import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

/** An account on a container registry: never its password, only the facts and the last test. */
export type RegistryLogins = ResponseOf<"/api/registries", "get">;
export type RegistryLogin = RegistryLogins["logins"][number];
export type RegistryLoginBody = BodyOf<"/api/registries", "post">;
export type RegistryLoginChangeBody = BodyOf<"/api/registries/{name}", "patch">;

/** The registry accounts one application pulls and builds with. */
export type AppRegistryLogins = ResponseOf<"/api/apps/{domain}/registry-logins", "get">;

export const registryKeys = {
  all: ["registries"] as const,
  list: ["registries", "list"] as const,
  // Under the app's own prefix, so a job on the app finishing refreshes it with the rest.
  forApp: (domain: string) => ["app", domain, "registry-logins"] as const,
};

/** Every registry account this server holds, and the kinds of registry there are. */
export const registryLoginsQuery = () =>
  queryOptions({
    queryKey: registryKeys.list,
    queryFn: ({ signal }) => request("get", "/api/registries", { signal }),
  });

/** The accounts an application uses. */
export const appRegistryLoginsQuery = (domain: string) =>
  queryOptions({
    queryKey: registryKeys.forApp(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/registry-logins", { params: { domain }, signal }),
  });

/** Adds an account. Needs "Confirm it's you". */
export function createRegistryLogin(body: RegistryLoginBody) {
  return request("post", "/api/registries", { body });
}

/** Changes an account; a field left out keeps its value. Needs "Confirm it's you". */
export function changeRegistryLogin(name: string, body: RegistryLoginChangeBody) {
  return request("patch", "/api/registries/{name}", { params: { name }, body });
}

/** Removes an account; the applications that used it pull without one. Needs "Confirm it's you". */
export function removeRegistryLogin(name: string) {
  return request("delete", "/api/registries/{name}", { params: { name } });
}

/** Logs in to the registry with the stored account and says how that went. Needs "Confirm it's you". */
export function testRegistryLogin(name: string) {
  return request("post", "/api/registries/{name}/test", { params: { name } });
}

/** Sets the accounts an application uses. Needs "Confirm it's you". */
export function setAppRegistryLogins(domain: string, logins: string[]) {
  return request("put", "/api/apps/{domain}/registry-logins", { params: { domain }, body: { logins } });
}
