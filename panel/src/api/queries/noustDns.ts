/**
 * Noust DNS: the authoritative name server Noust runs on a server of the fleet, the zones it serves
 * and what it takes to delegate a domain to it. Reads are queries here; the mutations sit beside the
 * page that makes them, because each asks for sudo mode in its own way. Everything is the server's
 * own (through the central: `/api/nodes/<server>/api/dns/noust/...`), so the cache is partitioned
 * by server like the rest.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

export type NoustServer = ResponseOf<"/api/dns/noust/server", "get">;
export type NoustZoneList = ResponseOf<"/api/dns/noust/zones", "get">;
export type NoustZoneSummary = NoustZoneList["zones"][number];
export type NoustZone = ResponseOf<"/api/dns/noust/zones/{zone}", "get">;
export type NoustRecord = NoustZone["records"][number];
export type NoustDnssec = ResponseOf<"/api/dns/noust/zones/{zone}/dnssec", "get">;
export type NoustDs = NoustDnssec["ds"][number];
export type NoustDelegation = ResponseOf<"/api/dns/noust/zones/{zone}/delegation", "get">;
export type NoustDelegationCheck = ResponseOf<"/api/dns/noust/zones/{zone}/delegation/check", "get">;
/** A sentence the server says as a code and its values, with its English beside it (`ServerText` draws it). */
export type NoustSentence = NoustDelegation["steps"][number];
/** Noust DNS on every server of the fleet, from the central (`GET /api/fleet/dns`). */
export type FleetDns = ResponseOf<"/api/fleet/dns", "get">;

/** The record types a zone takes, in the order a person looks for them. */
export const RECORD_TYPES = ["A", "AAAA", "CNAME", "MX", "TXT", "NS", "SRV", "CAA", "PTR", "DS"] as const;
export type RecordType = (typeof RECORD_TYPES)[number];

export const noustDnsKeys = {
  server: ["noust-dns", "server"] as const,
  zones: ["noust-dns", "zones"] as const,
  zone: (zone: string) => ["noust-dns", "zone", zone] as const,
  dnssec: (zone: string) => ["noust-dns", "zone", zone, "dnssec"] as const,
  delegation: (zone: string) => ["noust-dns", "zone", zone, "delegation"] as const,
  check: (zone: string) => ["noust-dns", "zone", zone, "check"] as const,
  /** The central's own answer about every server: its key is the fleet's, never partitioned by server. */
  fleet: ["fleet", "dns"] as const,
};

export const noustServerQuery = () =>
  queryOptions({
    queryKey: noustDnsKeys.server,
    queryFn: ({ signal }) => request("get", "/api/dns/noust/server", { signal }),
  });

export const noustZonesQuery = () =>
  queryOptions({
    queryKey: noustDnsKeys.zones,
    queryFn: ({ signal }) => request("get", "/api/dns/noust/zones", { signal }),
  });

export const noustZoneQuery = (zone: string) =>
  queryOptions({
    queryKey: noustDnsKeys.zone(zone),
    queryFn: ({ signal }) => request("get", "/api/dns/noust/zones/{zone}", { params: { zone }, signal }),
  });

export const noustDnssecQuery = (zone: string) =>
  queryOptions({
    queryKey: noustDnsKeys.dnssec(zone),
    queryFn: ({ signal }) => request("get", "/api/dns/noust/zones/{zone}/dnssec", { params: { zone }, signal }),
  });

export const noustDelegationQuery = (zone: string) =>
  queryOptions({
    queryKey: noustDnsKeys.delegation(zone),
    queryFn: ({ signal }) => request("get", "/api/dns/noust/zones/{zone}/delegation", { params: { zone }, signal }),
  });

/** What the world sees of the delegation. It asks the network, so it is read on request and never kept as fresh. */
export const noustCheckQuery = (zone: string) =>
  queryOptions({
    queryKey: noustDnsKeys.check(zone),
    queryFn: ({ signal }) => request("get", "/api/dns/noust/zones/{zone}/delegation/check", { params: { zone }, signal }),
    staleTime: 0,
    gcTime: 0,
  });

export const fleetDnsQuery = () =>
  queryOptions({
    queryKey: noustDnsKeys.fleet,
    queryFn: ({ signal }) => request("get", "/api/fleet/dns", { signal }),
  });
