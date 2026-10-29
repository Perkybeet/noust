/** What the Noust DNS tests share: a server in each of its states, a zone, and a console on a fake backend. */

import { vi } from "vitest";

import { renderConsole } from "../../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";

export const WARNINGS = ["authdns.warning.availability", "authdns.warning.amplification"];

export const NOT_INSTALLED = {
  engine: "knot",
  installed: false,
  version: null,
  running: false,
  configured: false,
  role: null,
  hostname: null,
  listen: [],
  primary_address: null,
  zones: 0,
  secondaries: [],
  candidates: [],
  firewall: {},
  foreign_zones: [],
  warnings: WARNINGS,
};

export const NOT_SET_UP = { ...NOT_INSTALLED, installed: true, version: "3.4.6", candidates: ["203.0.113.10"] };

export const PRIMARY = {
  ...NOT_SET_UP,
  running: true,
  configured: true,
  role: "primary",
  hostname: "ns1.example.net",
  listen: ["203.0.113.10"],
  zones: 1,
  secondaries: [{ address: "198.51.100.7", hostname: "ns2.example.net", added_at: "2026-10-20T10:00:00+00:00", added_by: "ana" }],
};

export const SECONDARY = { ...PRIMARY, role: "secondary", primary_address: "203.0.113.10", secondaries: [] };

export const ZONE = {
  name: "example.com",
  nameservers: ["ns1.example.net", "ns2.example.net"],
  contact: "hostmaster@example.com",
  default_ttl: 3600,
  dnssec: false,
  serial: 2026102901,
  records: [
    { id: 1, zone: "example.com", name: "@", type: "A", content: "203.0.113.7", ttl: null, created_at: "", updated_at: "" },
    { id: 2, zone: "example.com", name: "www", type: "CNAME", content: "example.com.", ttl: 300, created_at: "", updated_at: "" },
  ],
  created_at: "2026-10-20T10:00:00+00:00",
  updated_at: "2026-10-20T10:00:00+00:00",
};

export const ZONES = { zones: [{ name: "example.com", records: 2, serial: 2026102901, dnssec: false, role: "master" }] };

export const DS = { key_tag: 12345, algorithm: 13, digest_type: 2, digest: "ABCDEF0123456789", text: "example.com. IN DS 12345 13 2 ABCDEF0123456789" };

/** A console at `path` on a fake backend that answers the pages around it, and `routes` on top. */
export function noustConsole(path: string, routes: Record<string, RouteHandler>) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: true,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
    "GET /api/sites": () => json(200, { sites: [], total: 0, webserver: "nginx" }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    "GET /api/dns/providers": () => json(200, { accounts: [], kinds: [] }),
    "GET /api/tunnels": () => json(200, { tunnels: [], support: { available: true, cloudflared: "/usr/bin/cloudflared", sealed: false, accounts: ["cf"], problem: null, hint: null } }),
    "GET /api/streams": () => json(200, { exposures: [], support: { available: true, module: "dynamic", loader: "installable", problem: null, hint: null } }),
    ...routes,
  });
  return { ...renderConsole(path), backend };
}
