import { screen } from "@testing-library/react";

import { json } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../testRoutes";
import { DOMAIN, ELEVATED, renderSettings } from "./testKit";

export const FIELDS = {
  basic_auth: false,
  realm: "Restricted",
  access: "all",
  allow_ips: [],
  deny_ips: [],
  real_ip: "off",
  real_ip_header: "",
  trusted_proxies: [],
  rate_limit: "",
  rate_burst: 20,
  connection_limit: 0,
  headers: {},
  hidden_headers: [],
  hsts: "default",
  noindex: false,
  canonical: "",
  http3: false,
  client_certs: false,
  force_https: true,
  waf: "",
  waf_exclusions: [] as number[],
  forward_auth: null,
  routes: [],
};

export const PRESETS = {
  presets: [
    {
      name: "oauth2-proxy",
      url: "http://127.0.0.1:4180/oauth2/auth",
      sign_in: "/oauth2/start?rd=",
      headers: ["X-Auth-Request-User", "X-Auth-Request-Email"],
      note: "Run oauth2-proxy with --set-xauthrequest.",
    },
  ],
};

export const TAILNET = {
  name: "tailscale",
  kind: "tailscale",
  interface: "tailscale0",
  cidrs: ["100.64.0.0/10", "fd7a:115c:a1e0::/48"],
  addresses: ["100.101.102.103"],
  up: true,
  dns_name: "web.tail1234.ts.net",
  tailnet: "example.org",
  cert_domains: ["web.tail1234.ts.net"],
  problem: "",
};

export function privateStatus(changes: Record<string, unknown> = {}) {
  return {
    app: DOMAIN,
    private: false,
    networks: [],
    allow_ips: [],
    public_ranges: [],
    applicable: true,
    reason: "",
    available: [TAILNET],
    tailnet_name: "web.tail1234.ts.net",
    tailnet_certificate: "",
    ...changes,
  };
}

export const WAF = {
  available: true,
  webserver: "nginx",
  module_loaded: true,
  module_installed: true,
  rules: true,
  problem: "",
  hint: "",
  installable: true,
  packages: ["libnginx-mod-http-modsecurity", "modsecurity-crs"],
  anchor: false,
};

export const NO_EVENTS = { events: [], noisy: [] };

export const SERIAL = "1A000000000000000000000000000001";
export const OLD_SERIAL = "2B000000000000000000000000000002";

export function certificate(name: string, changes: Record<string, unknown> = {}) {
  return {
    name,
    serial: SERIAL,
    fingerprint: "AA:BB",
    not_before: "2026-10-01T00:00:00+00:00",
    not_after: "2999-10-01T00:00:00+00:00",
    issued_by: "ana",
    issued_at: "2026-10-01T00:00:00+00:00",
    revoked: false,
    revoked_at: null,
    revoked_by: null,
    ...changes,
  };
}

export function clientCerts(certificates: object[] = [], enabled = false) {
  return { enabled, authority: certificates.length > 0, certificates };
}

export const HTTP3 = { available: true, webserver: "nginx", version: "1.26.2", problem: "", hint: "", anchor: false, udp_allowed: true, firewall_hint: "" };

export function policy(changes: Record<string, unknown> = {}, extra: Record<string, unknown> = {}) {
  return {
    app: DOMAIN,
    policy: { ...FIELDS, ...changes },
    users: ["ana"],
    webserver: "nginx",
    applicable: true,
    reason: "",
    headers: [{ name: "X-Frame-Options", value: "SAMEORIGIN" }],
    real_ip_networks: ["173.245.48.0/20", "103.21.244.0/22"],
    real_ip_source: "built-in",
    ...extra,
  };
}


/** The site's policy page, Access or Protection: both read the same policy and its neighbours. */
async function openPolicy(path: "/access" | "/protection", heading: string, ready: RegExp, routes: Record<string, RouteHandler>, body: object) {
  screenWidth(1440);
  const harness = await renderSettings(path, heading, undefined, {
    "GET /api/auth/session": () => json(200, ELEVATED),
    [`GET /api/apps/${DOMAIN}/policy`]: () => json(200, body),
    [`GET /api/apps/${DOMAIN}/http3`]: () => json(200, HTTP3),
    [`GET /api/apps/${DOMAIN}/private`]: () => json(200, privateStatus()),
    [`GET /api/apps/${DOMAIN}/waf`]: () => json(200, WAF),
    [`GET /api/apps/${DOMAIN}/waf/events`]: () => json(200, NO_EVENTS),
    [`GET /api/apps/${DOMAIN}/client-certs`]: () => json(200, clientCerts()),
    "GET /api/policy/forward-auth-presets": () => json(200, PRESETS),
    ...routes,
  });
  // The policy is read after the page has its heading.
  await screen.findByRole("checkbox", { name: ready });
  return harness;
}

/** Access: who gets in. */
export function openAccess(routes: Record<string, RouteHandler> = {}, body: object = policy()) {
  return openPolicy("/access", "Access", /Ask for a password/, routes, body);
}

/** Protection: what the site limits and sends back. */
export function openProtection(routes: Record<string, RouteHandler> = {}, body: object = policy()) {
  return openPolicy("/protection", "Protection", /Ask search engines not to index/, routes, body);
}
