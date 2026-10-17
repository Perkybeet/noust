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
export type PolicyBody = Omit<BodyOf<"/api/apps/{domain}/policy", "put">, "accept_lockout" | "accept_unreachable_auth">;
/** The policy as the server answered it, read as complete (see {@link PolicyFields}). */
export const policyOf = (answer: PolicyResponse): AppPolicy => answer as AppPolicy;
export type CloudflareRanges = ResponseOf<"/api/policy/cloudflare-ranges", "get">;
/** What the common authentication services answer with, to fill a form from. */
export type ForwardAuthPresets = ResponseOf<"/api/policy/forward-auth-presets", "get">;

export type Http3Support = ResponseOf<"/api/apps/{domain}/http3", "get">;
/** Whether this server can run the web application firewall, and what its rules saw lately. */
export type WafSupport = ResponseOf<"/api/apps/{domain}/waf", "get">;
export type WafEvents = ResponseOf<"/api/apps/{domain}/waf/events", "get">;
export type WafEvent = WafEvents["events"][number];
/** The private networks this server is on, and whether an application answers one only. */
export type PrivateNetworkList = ResponseOf<"/api/private-networks", "get">;
export type PrivateNetwork = PrivateNetworkList["networks"][number];
export type AppPrivate = ResponseOf<"/api/apps/{domain}/private", "get">;
/** The certificates made for the clients of an application's site, revoked ones included. */
export type ClientCertificateList = ResponseOf<"/api/apps/{domain}/client-certs", "get">;
export type ClientCertificate = ClientCertificateList["certificates"][number];
/** What making one hands over, once: the key's bundle, its password and the PEM pair. */
export type IssuedClientCertificate = ResponseOf<"/api/apps/{domain}/client-certs", "post">;
export type UnknownHosts = ResponseOf<"/api/unknown-hosts", "get">;
export type UnknownHostsBody = BodyOf<"/api/unknown-hosts", "put">;

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
  forwardAuthPresets: ["network", "forward-auth-presets"] as const,
  dnsAccounts: ["network", "dns", "accounts"] as const,
  dnsRecords: ["network", "dns", "records"] as const,
  appDns: (app: string) => ["app", app, "dns"] as const,
  acme: ["network", "acme"] as const,
  uploaded: ["network", "uploaded"] as const,
  appCertificate: (app: string) => ["app", app, "certificate"] as const,
  appHttp3: (app: string) => ["app", app, "http3"] as const,
  appPrivate: (app: string) => ["app", app, "private"] as const,
  appClientCerts: (app: string) => ["app", app, "client-certs"] as const,
  appWaf: (app: string) => ["app", app, "waf"] as const,
  appWafEvents: (app: string) => ["app", app, "waf", "events"] as const,
  streams: ["network", "streams"] as const,
  unknownHosts: ["network", "unknown-hosts"] as const,
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

/** What answers a request for a name no site serves, and what else claims to be the default. */
export const unknownHostsQuery = () =>
  queryOptions({
    queryKey: networkKeys.unknownHosts,
    queryFn: ({ signal }) => request("get", "/api/unknown-hosts", { signal }),
  });

/** Whether this application's server can serve HTTP/3, and what stands in the way. */
export const appHttp3Query = (app: string) =>
  queryOptions({
    queryKey: networkKeys.appHttp3(app),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/http3", { params: { domain: app }, signal }),
  });

/** Whether this server can run the web application firewall, and what stands in the way. */
export const appWafQuery = (app: string) =>
  queryOptions({
    queryKey: networkKeys.appWaf(app),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/waf", { params: { domain: app }, signal }),
  });

/** What the firewall's rules judged lately for an application, and the rules that matched most. */
export const appWafEventsQuery = (app: string) =>
  queryOptions({
    queryKey: networkKeys.appWafEvents(app),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/waf/events", { params: { domain: app }, signal }),
  });

/** Whether an application answers a private network only, and the networks it could answer. */
export const appPrivateQuery = (app: string) =>
  queryOptions({
    queryKey: networkKeys.appPrivate(app),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/private", { params: { domain: app }, signal }),
  });

/** The client certificates made for an application, and whether its site asks for one. */
export const appClientCertsQuery = (app: string) =>
  queryOptions({
    queryKey: networkKeys.appClientCerts(app),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/client-certs", { params: { domain: app }, signal }),
  });

/** The presets of the common authentication services. */
export const forwardAuthPresetsQuery = () =>
  queryOptions({
    queryKey: networkKeys.forwardAuthPresets,
    queryFn: ({ signal }) => request("get", "/api/policy/forward-auth-presets", { signal }),
    staleTime: Infinity,
  });
