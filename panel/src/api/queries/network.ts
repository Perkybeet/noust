/**
 * What the network of an application and of the server is made of: the policy of a site
 * (password, addresses, limits, headers), the DNS accounts and the records Noust manages in
 * them, where an application's certificate comes from, the TCP and UDP ports published through
 * nginx, and the Cloudflare Tunnels. Reads are queries here; the mutations sit beside the page
 * that makes them, because each asks for sudo mode in its own way.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

type PolicyResponse = ResponseOf<"/api/apps/{domain}/policy", "get">;
/**
 * The generated types mark every field the server defaults as optional, but the server always
 * sends them (a response is not trimmed to what was set), so the policy is read as complete.
 */
export type PolicyFields = Omit<Required<PolicyResponse["policy"]>, "routes"> & {
  routes: Required<NonNullable<PolicyResponse["policy"]["routes"]>[number]>[];
};
export type AppPolicy = Required<Omit<PolicyResponse, "policy">> & { policy: PolicyFields };
/** What the form sends: the fields it changed. The lockout confirmation is added when asked. */
export type PolicyBody = Omit<BodyOf<"/api/apps/{domain}/policy", "put">, "accept_lockout">;
/** The policy as the server answered it, read as complete (see {@link PolicyFields}). */
export const policyOf = (answer: PolicyResponse): AppPolicy => answer as AppPolicy;
export type CloudflareRanges = ResponseOf<"/api/policy/cloudflare-ranges", "get">;

export type DnsAccountList = ResponseOf<"/api/dns/providers", "get">;
export type DnsAccount = DnsAccountList["accounts"][number];
export type DnsKind = DnsAccountList["kinds"][number];
export type ManagedRecordList = ResponseOf<"/api/dns/records", "get">;
export type ManagedRecord = ManagedRecordList["records"][number];
export type AppDns = ResponseOf<"/api/apps/{domain}/dns", "get">;
export type DnsPlan = AppDns["plans"][number];
export type EnsureRecordBody = BodyOf<"/api/apps/{domain}/dns", "post">;

export type AcmeSettings = ResponseOf<"/api/certs/acme", "get">;
export type UploadedList = ResponseOf<"/api/certs/uploaded", "get">;
export type UploadedCertificate = UploadedList["certificates"][number];
export type AppCertificate = ResponseOf<"/api/apps/{domain}/certificate", "get">;

export type StreamList = ResponseOf<"/api/streams", "get">;
export type StreamExposure = StreamList["exposures"][number];
export type StreamPlan = ResponseOf<"/api/streams/plan", "post">;
export type StreamBody = BodyOf<"/api/streams", "post">;

export type TunnelList = ResponseOf<"/api/tunnels", "get">;
export type Tunnel = TunnelList["tunnels"][number];
export type TunnelRoute = NonNullable<Tunnel["routes"]>[number];
export type RouteBody = BodyOf<"/api/tunnels/{name}/routes", "post">;
export type RouteResult = ResponseOf<"/api/tunnels/{name}/routes", "post">;

export const networkKeys = {
  /** An application's policy sits under the application's own prefix, so its jobs refresh it. */
  policy: (app: string) => ["app", app, "policy"] as const,
  ranges: ["network", "cloudflare-ranges"] as const,
  dnsAccounts: ["network", "dns", "accounts"] as const,
  dnsRecords: ["network", "dns", "records"] as const,
  appDns: (app: string) => ["app", app, "dns"] as const,
  acme: ["network", "acme"] as const,
  uploaded: ["network", "uploaded"] as const,
  appCertificate: (app: string) => ["app", app, "certificate"] as const,
  streams: ["network", "streams"] as const,
  tunnels: ["network", "tunnels"] as const,
};

/** What an application's site asks of a visitor, and what it sends back. */
export const appPolicyQuery = (app: string) =>
  queryOptions({
    queryKey: networkKeys.policy(app),
    queryFn: async ({ signal }) => policyOf(await request("get", "/api/apps/{domain}/policy", { params: { domain: app }, signal })),
  });

/** The networks a site trusts to name the visitor behind Cloudflare. */
export const cloudflareRangesQuery = () =>
  queryOptions({
    queryKey: networkKeys.ranges,
    queryFn: ({ signal }) => request("get", "/api/policy/cloudflare-ranges", { signal }),
  });

/** The DNS accounts, and the providers one can be made for. */
export const dnsAccountsQuery = () =>
  queryOptions({
    queryKey: networkKeys.dnsAccounts,
    queryFn: ({ signal }) => request("get", "/api/dns/providers", { signal }),
  });

/** The DNS records Noust manages, who made them and who uses them. */
export const managedRecordsQuery = () =>
  queryOptions({
    queryKey: networkKeys.dnsRecords,
    queryFn: ({ signal }) => request("get", "/api/dns/records", { signal }),
  });

/**
 * What each of an application's names would point at, and the records it uses. Read from the
 * providers each time it is asked for: the answer is what the operator is waiting on.
 */
export const appDnsQuery = (app: string) =>
  queryOptions({
    queryKey: networkKeys.appDns(app),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/dns", { params: { domain: app }, signal }),
    staleTime: 0,
  });

/** Which certificate authority orders, and as whom. */
export const acmeQuery = () =>
  queryOptions({
    queryKey: networkKeys.acme,
    queryFn: ({ signal }) => request("get", "/api/certs/acme", { signal }),
  });

/** The certificates that were uploaded. Never a key. */
export const uploadedQuery = () =>
  queryOptions({
    queryKey: networkKeys.uploaded,
    queryFn: ({ signal }) => request("get", "/api/certs/uploaded", { signal }),
  });

/** How an application's certificate comes to be, and what it is. */
export const appCertificateQuery = (app: string) =>
  queryOptions({
    queryKey: networkKeys.appCertificate(app),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/certificate", { params: { domain: app }, signal }),
  });

/** The TCP and UDP ports published through nginx, and whether another can be. */
export const streamsQuery = () =>
  queryOptions({
    queryKey: networkKeys.streams,
    queryFn: ({ signal }) => request("get", "/api/streams", { signal }),
  });

/** The Cloudflare Tunnels, what each carries and whether its connector runs. */
export const tunnelsQuery = () =>
  queryOptions({
    queryKey: networkKeys.tunnels,
    queryFn: ({ signal }) => request("get", "/api/tunnels", { signal }),
  });
