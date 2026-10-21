import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

export type AppDomainList = ResponseOf<"/api/apps/{domain}/domains", "get">;
export type AppDomain = AppDomainList["domains"][number];
export type AppDomainChange = ResponseOf<"/api/apps/{domain}/domains", "post">;
export type DnsCheck = ResponseOf<"/api/apps/{domain}/domains/{name}/dns", "get">;

/** What a domain is to its application: the application itself, served the same, or sent to it. */
export type DomainKind = "primary" | "alias" | "redirect";

/**
 * Keys of an application's domains. They live under the application's own prefix
 * (`["app", domain]`), so a job on the app ending, which invalidates that prefix, refreshes
 * them with everything else read about it.
 */
export const domainKeys = {
  list: (app: string) => ["app", app, "domains"] as const,
  dns: (app: string, name: string) => ["app", app, "domains", "dns", name] as const,
};

/** A name made of this server's public address, for an application with no domain of its own yet. */
export type TemporaryName = ResponseOf<"/api/domains/temporary", "get">;

/** What an application's temporary name is made from: any one of them, a prefix before the others. */
export interface TemporaryNameOf {
  prefix?: string | undefined;
  source?: string | undefined;
  image?: string | undefined;
}

/**
 * The temporary address an application would get (`shop.203-0-113-10.sslip.io`): read out of the
 * repository, the image or the recipe it is deployed from, numbered when another application has
 * it. Fails with the way to declare the address on a server behind NAT, which the wizard takes as
 * "not available" and not as an error to show. Short-lived: another application may take it.
 */
export const temporaryNameQuery = (of: TemporaryNameOf) => {
  const query = {
    ...(of.prefix !== undefined && of.prefix !== "" ? { prefix: of.prefix } : {}),
    ...(of.source !== undefined && of.source !== "" ? { source: of.source } : {}),
    ...(of.image !== undefined && of.image !== "" ? { image: of.image } : {}),
  };
  return queryOptions({
    queryKey: ["domains", "temporary", query] as const,
    queryFn: ({ signal }) => request("get", "/api/domains/temporary", { query, signal }),
    staleTime: 10_000,
    gcTime: 60_000,
    retry: false,
  });
};

export const appDomainsQuery = (app: string) =>
  queryOptions({
    queryKey: domainKeys.list(app),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/domains", { params: { domain: app }, signal }),
  });

/**
 * Whether a name resolves to this server. Asked again every time it is read: DNS is exactly
 * the thing an operator is waiting on to change.
 */
export const dnsCheckQuery = (app: string, name: string) =>
  queryOptions({
    queryKey: domainKeys.dns(app, name),
    queryFn: ({ signal }) =>
      request("get", "/api/apps/{domain}/domains/{name}/dns", { params: { domain: app, name }, signal }),
    staleTime: 0,
    gcTime: 60_000,
  });

/**
 * Whether a name resolves to this server, before it is any application's - the same check
 * `dnsCheckQuery` runs for a domain already added to one, through the same backend function,
 * for the new-app wizard, which has no application yet to hang the path off.
 */
export const bareDnsCheckQuery = (name: string) =>
  queryOptions({
    queryKey: ["domains", "dns", "bare", name] as const,
    queryFn: ({ signal }) => request("get", "/api/domains/dns", { query: { name }, signal }),
    staleTime: 0,
    gcTime: 60_000,
  });
