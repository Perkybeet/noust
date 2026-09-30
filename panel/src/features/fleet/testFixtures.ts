/**
 * A central with three servers, for tests. web-2 answers (one failed app, one certificate about
 * to expire); db-1 stopped answering (ssh timed out) and is shown from its last good answer,
 * three minutes old; old-1 answers but runs a Noust too old to offer the overview. The
 * central's own views (`/api/fleet/*`) say so, the way the aggregator does.
 */

import type { SessionInfo } from "../../api/queries/auth";
import { MACHINE, SESSION, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import type { CentralInfo } from "../central/central";

export const SSH_TIMEOUT = "ssh: connect to host db1.example.com port 22: Connection timed out";

export const WEB2 = {
  name: "web-2",
  ssh_host: "web2.example.com",
  ssh_port: 22,
  ssh_user: "noust-tunnel",
  host_key: "web2.example.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB",
  console_port: 8080,
  version: "2.0.0",
  status: "reachable",
  last_seen: "2026-09-29T08:00:00Z",
  allow_shell: false,
  created_at: "2026-09-20T10:00:00Z",
  tunnel: { open: true, local_port: 40123, since: "2026-09-29T07:00:00Z", last_error: null, failures: 0 },
};

export const DB1 = {
  ...WEB2,
  name: "db-1",
  ssh_host: "db1.example.com",
  ssh_port: 2222,
  version: "1.9.0",
  status: "unreachable",
  last_seen: "2026-09-28T08:00:00Z",
  tunnel: { open: false, local_port: null, since: null, last_error: SSH_TIMEOUT, failures: 3 },
};

export const OLD1 = { ...WEB2, name: "old-1", ssh_host: "old1.example.com", version: "1.8.0", status: "reachable" };

/** A session of a central in the given state. */
export function centralSession(central: Partial<CentralInfo> = {}, session: Partial<SessionInfo> = {}): SessionInfo {
  return { ...SESSION, ...session, central: { role: "server", sealed: false, locked: false, ...central } };
}

const NOW = new Date().toISOString();
const THREE_MINUTES_AGO = new Date(Date.now() - 180_000).toISOString();

function outcome(name: string, status: string, extra: Record<string, unknown> = {}) {
  return {
    name,
    local: name === "web-01",
    status,
    code: null,
    message: null,
    hint: null,
    error_verbatim: null,
    age_seconds: status === "ok" ? 0 : null,
    fetched_at: status === "ok" ? NOW : null,
    elapsed_ms: 40,
    version: null,
    missing: [],
    warnings: [],
    ...extra,
  };
}

/** Every server's outcome, as each view gives it: db-1 stale, old-1 too old for some of it. */
export const OUTCOMES = [
  outcome("web-01", "ok", { version: "2.0.0" }),
  outcome("web-2", "ok", { version: "2.0.0" }),
  outcome("db-1", "stale", {
    code: "node_unreachable",
    message: "The tunnel to db-1 did not open",
    hint: "Check it with 'noust node test db-1'.",
    error_verbatim: SSH_TIMEOUT,
    age_seconds: 180,
    fetched_at: THREE_MINUTES_AGO,
    version: "1.9.0",
  }),
  outcome("old-1", "unsupported", {
    code: "not_offered",
    message: "old-1 does not offer GET /api/overview",
    hint: "It runs an older Noust. Update it to see this here.",
    fetched_at: NOW,
    age_seconds: 0,
    version: "1.8.0",
    missing: ["GET /api/overview"],
  }),
];

function row(node: string, page: string, fields: Record<string, unknown>) {
  const local = node === "web-01";
  return { ...fields, node, local, page, href: local ? page : `/n/${node}${page === "/" ? "" : page}` };
}

function server(node: string, extra: Record<string, unknown> = {}) {
  const version = node === "db-1" ? "1.9.0" : node === "old-1" ? "1.8.0" : "2.0.0";
  return row(node, "/", {
    name: node,
    reachability: node === "db-1" ? "unreachable" : "reachable",
    version,
    latency_ms: 40,
    last_seen: node === "db-1" ? "2026-09-28T08:00:00Z" : "2026-09-29T08:00:00Z",
    ssh: node === "web-01" ? null : `noust-tunnel@${node.replace("-", "")}.example.com:22`,
    access: node === "web-01" ? null : node === "old-1" ? null : { level: node === "db-1" ? "read" : "admin", host_access: false },
    labels: node === "web-2" ? { env: "prod" } : node === "db-1" ? { env: "prod", role: "db" } : {},
    noust: { current_version: version, latest_version: "2.1.0", update_state: version === "2.0.0" ? "up_to_date" : "update_available", update_command: "pip install --upgrade noust" },
    ...extra,
  });
}

const ATTENTION_WEB2 = [
  {
    id: "app:api.example.com",
    title: "api.example.com",
    severity: "fail",
    subject: { kind: "app", domain: "api.example.com" },
    reasons: [{ code: "service_failed", kind: "service", severity: "fail", actions: ["diagnose"], params: {} }],
  },
];

export const SUMMARY_VIEW = {
  resource: "summary",
  generated_at: NOW,
  partial: true,
  nodes: OUTCOMES,
  items: [
    server("web-01", {
      apps: { running: 4, failed: 1, stopped: 1, static: 1 },
      units: { running: 9, failed: 1, stopped: 2 },
      certificates_expiring: 0,
      machine: MACHINE,
      overview: { attention: [], attention_total: 0 },
      server: { reboot: { required: false } },
    }),
    server("web-2", {
      apps: { running: 3, failed: 1, stopped: 0, static: 0 },
      units: { running: 5, failed: 1, stopped: 0 },
      certificates_expiring: 1,
      machine: { ...MACHINE, hostname: "web-2", cpu_percent: 7.5 },
      overview: { attention: ATTENTION_WEB2, attention_total: 1 },
      server: { reboot: { required: true } },
    }),
    server("db-1", {
      apps: { running: 1, failed: 0, stopped: 0, static: 0 },
      units: { running: 2, failed: 0, stopped: 0 },
      certificates_expiring: 0,
      machine: { ...MACHINE, hostname: "db-1" },
      overview: { attention: [], attention_total: 0 },
      server: null,
    }),
    server("old-1", {
      apps: { running: 2, failed: 0, stopped: 0, static: 0 },
      units: { running: 3, failed: 0, stopped: 0 },
      certificates_expiring: null,
      machine: { ...MACHINE, hostname: "old-1" },
      overview: null,
      server: null,
    }),
  ],
};

function view(resource: string, items: unknown[]) {
  return { resource, generated_at: NOW, partial: true, nodes: OUTCOMES, items };
}

export const SERVERS_VIEW = view("servers", ["web-01", "web-2", "db-1", "old-1"].map((node) => server(node)));

export const APPS_VIEW = view("apps", [
  row("web-01", "/apps/shop.example.com", { domain: "shop.example.com", name: "shop", app_type: "nextjs", status: "running", port: 3000, layout: "releases" }),
  row("web-2", "/apps/api.example.com", {
    domain: "api.example.com",
    name: "api",
    app_type: "python",
    status: "failed",
    port: 8000,
    layout: "releases",
    last_deployment: { id: 7, status: "failed", finished_at: NOW, git_commit: "abc1234" },
  }),
  row("db-1", "/apps/reports.example.com", { domain: "reports.example.com", name: "reports", app_type: "static", status: "running", port: null, layout: "inplace" }),
]);

export const CERTIFICATES_VIEW = view("certificates", [
  row("web-2", "/domains", { domain: "www.example.com", domains: ["www.example.com", "example.com"], days_remaining: 5, expires_on: "2026-10-04", auto_renew: true, issuer: "C=US, O=Let's Encrypt, CN=R11" }),
  row("web-01", "/domains", { domain: "shop.example.com", domains: ["shop.example.com"], days_remaining: 80, expires_on: "2026-12-18", auto_renew: true, issuer: null }),
]);

export const BACKUPS_VIEW = view("backups", [
  row("web-2", "/backups?domain=api.example.com", { domain: "api.example.com", backups: 0, size: 0, last_backup: null, verified: "never", scheduled: false }),
  row("web-01", "/backups?domain=shop.example.com", {
    domain: "shop.example.com",
    backups: 3,
    size: 52_428_800,
    last_backup: { timestamp: NOW, verified_ok: true },
    verified: "ok",
    scheduled: true,
  }),
]);

export const UPDATES_VIEW = view(
  "updates",
  ["web-01", "web-2", "db-1", "old-1"].map((node) =>
    server(
      node,
      node === "web-2"
        ? { os: { updates: { pending: 12, security: 3 }, reboot: { required: true } } }
        : node === "old-1"
          ? // Its package index has not seen the release yet: the fleet's update refreshes it first.
            { os: null, noust: { current_version: "1.8.0", latest_version: "2.1.0", update_state: "index_behind", update_command: null } }
          : { os: null },
    ),
  ),
);

export const ACTIVITY_VIEW = view("activity", [
  row("web-2", "/activity", { timestamp: NOW, action: "deploy.update", actor: "token:ci", result: "success", resource: "app:api.example.com" }),
  row("web-01", "/activity", { timestamp: THREE_MINUTES_AGO, action: "auth.login", actor: "master", result: "denied", resource: null }),
]);

export const ACTIONS = {
  actions: [
    { name: "certs_renew", title: "Renew certificates", operations: ["POST /api/certs/renew-all"], serial: 4, max_failures: null },
    { name: "apps_update", title: "Update applications", operations: ["GET /api/apps", "POST /api/jobs/update"], serial: 2, max_failures: 0 },
    { name: "noust_update", title: "Update Noust", operations: ["GET /api/system/update", "POST /api/system/update"], serial: 1, max_failures: 0 },
  ],
};

function nodeState(node: string, state: string, extra: Record<string, unknown> = {}) {
  return {
    node,
    position: 0,
    batch: 1,
    state,
    reason: null,
    step: null,
    node_jobs: [],
    items: [],
    error: null,
    output: null,
    started_at: null,
    ended_at: null,
    requires_elevation: true,
    href: `/n/${node}/activity`,
    ...extra,
  };
}

export const PLAN = {
  action: "certs_renew",
  title: "Renew certificates",
  strategy: { serial: 4, max_failures: null, canary: null },
  options: { force: false },
  nodes: [nodeState("web-2", "queued"), nodeState("db-1", "skipped", { reason: "policy", batch: 1 })],
  batches: [["web-2"]],
  summary: { run: 1, skipped: 1 },
  requires_elevation: true,
  notes: [],
};

export const JOB = {
  job_id: "fj-1",
  action: "certs_renew",
  title: "Renew certificates",
  request: { action: "certs_renew", nodes: ["web-2", "db-1"], strategy: { serial: 4 } },
  status: "failed",
  created_at: NOW,
  created_by: "admin",
  finished_at: NOW,
  retry_of: null,
  summary: { queued: 0, running: 0, succeeded: 0, failed: 1, skipped: 1, unreachable: 0, refused: 0, interrupted: 0 },
  nodes: [
    nodeState("web-2", "failed", {
      step: "certbot renew",
      started_at: THREE_MINUTES_AGO,
      ended_at: NOW,
      error: { code: "node_error", message: "certbot failed on web-2", hint: "Check the DNS of www.example.com." },
      output: "Certbot failed to authenticate some domains (authenticator: nginx).",
    }),
    nodeState("db-1", "skipped", { reason: "policy" }),
  ],
};

/** Everything the fleet's pages and Settings > Servers read, on a central with web-2, db-1 and old-1. */
export function fleetRoutes(session: SessionInfo = centralSession(), nodes: unknown[] = [WEB2, DB1, OLD1]): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(session),
    "GET /api/certs": () => json(200, { total: 0, certificates: [] }),
    "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
    "GET /api/nodes": () => json(200, { items: nodes }),
    "GET /api/fleet/summary": () => json(200, SUMMARY_VIEW),
    "GET /api/fleet/servers": () => json(200, SERVERS_VIEW),
    "GET /api/fleet/apps": () => json(200, APPS_VIEW),
    "GET /api/fleet/certificates": () => json(200, CERTIFICATES_VIEW),
    "GET /api/fleet/backups": () => json(200, BACKUPS_VIEW),
    "GET /api/fleet/updates": () => json(200, UPDATES_VIEW),
    "GET /api/fleet/activity": () => json(200, ACTIVITY_VIEW),
    "GET /api/fleet/actions": () => json(200, ACTIONS),
    "GET /api/fleet/jobs": () => json(200, { jobs: [{ ...JOB, nodes: null }] }),
    "GET /api/fleet/jobs/fj-1": () => json(200, JOB),
  };
}
