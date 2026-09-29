/**
 * A central with two servers, for tests: web-2 answers through its tunnel (one failed app,
 * one certificate about to expire), db-1 does not (ssh timed out), and runs an older Noust.
 */

import type { SessionInfo } from "../../api/queries/auth";
import { MACHINE, SESSION, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import type { CentralInfo } from "../central/central";

export const SSH_TIMEOUT = "ssh: connect to host db1.example.com port 22: Connection timed out";

export const WEB2 = {
  name: "web-2",
  ssh_host: "web2.example.com",
  ssh_port: 22,
  ssh_user: "root",
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

/** A session of a central in the given state. */
export function centralSession(central: Partial<CentralInfo> = {}, session: Partial<SessionInfo> = {}): SessionInfo {
  return { ...SESSION, ...session, central: { role: "server", sealed: false, locked: false, ...central } };
}

/** Everything the Fleet page and Settings > Servers read, on a central with web-2 and db-1. */
export function fleetRoutes(session: SessionInfo = centralSession(), nodes: unknown[] = [WEB2, DB1]): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(session),
    "GET /api/certs": () => json(200, { total: 0, certificates: [] }),
    "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
    "GET /api/nodes": () => json(200, { items: nodes }),
    "GET /api/nodes/web-2/api/system/machine": () =>
      json(200, { ...MACHINE, hostname: "web-2", cpu_percent: 7.5, apps: { running: 3, failed: 1, stopped: 0, static: 0 }, units: { running: 5, failed: 1, stopped: 0 } }),
    "GET /api/nodes/web-2/api/apps": () =>
      json(200, {
        total: 2,
        apps: [
          { domain: "api.example.com", name: "api", app_type: "python", status: "failed", active: false, enabled: true, port: 8000, layout: "releases" },
          { domain: "www.example.com", name: "www", app_type: "static", status: "running", active: true, enabled: true, port: null, layout: "inplace" },
        ],
      }),
    "GET /api/nodes/web-2/api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
    "GET /api/nodes/web-2/api/certs": () =>
      json(200, {
        total: 1,
        certificates: [{ domain: "www.example.com", domains: ["www.example.com"], days_remaining: 5, expires_on: "2026-10-04", auto_renew: true }],
      }),
    "GET /api/nodes/web-2/api/services": () => json(200, { services: [], total: 0 }),
    "GET /api/nodes/db-1/api/system/machine": () =>
      problem(502, "node_unreachable", "The tunnel to db-1 did not open", {
        hint: "Check that the node is up and that its SSH address and host key are the ones the central recorded; the output below is ssh's own.",
        output: SSH_TIMEOUT,
      }),
  };
}
