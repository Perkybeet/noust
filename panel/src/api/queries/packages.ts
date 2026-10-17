import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

/** The system packages an application declares, and what the host has of them. */
export type AppPackages = ResponseOf<"/api/apps/{domain}/packages", "get">;
export type AppPackagesBody = BodyOf<"/api/apps/{domain}/packages", "put">;

export const packagesKeys = {
  // Under the app's own prefix, so a job on the app finishing refreshes it with the rest.
  forApp: (domain: string) => ["app", domain, "packages"] as const,
};

/** What the application declares, what the host has of it, and the server's policy. Changes nothing. */
export const appPackagesQuery = (domain: string) =>
  queryOptions({
    queryKey: packagesKeys.forApp(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/packages", { params: { domain }, signal }),
  });

/** Declares the packages, replacing the list. Records it and installs nothing. Needs "Confirm it's you". */
export function putAppPackages(domain: string, packages: string[]) {
  return request("put", "/api/apps/{domain}/packages", { params: { domain }, body: { packages } });
}

/** Queues the installation of what is declared and missing. Needs "Confirm it's you". */
export function installAppPackages(domain: string) {
  return request("post", "/api/apps/{domain}/packages/install", { params: { domain } });
}
