/**
 * An application placed on two servers of the test fleet, for tests: web-2 and web-3 both run
 * shop.example.com, web-3 on another commit. The central's answers are what the real routes give.
 */

import { json } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { fleetRoutes } from "../testFixtures";

export const SHOP = "shop.example.com";

export const PLACEMENT = {
  domain: SHOP,
  nodes: ["web-2", "web-3"],
  labels: {},
  members: ["web-2", "web-3"],
  serial: null,
  max_failures: null,
  canary: null,
  rollback_on_failure: true,
  created_at: "2026-10-10T10:00:00+00:00",
  created_by: "cli:yago@nas",
  updated_at: "2026-10-10T10:00:00+00:00",
};

function server(node: string, sync: string, commit: string | null, extra: Record<string, unknown> = {}) {
  return {
    node,
    reachable: sync !== "unreachable",
    present: sync !== "missing" && sync !== "unreachable",
    status: sync === "missing" || sync === "unreachable" ? null : "running",
    commit,
    deployed_at: "2026-10-10T10:00:00+00:00",
    branch: "main",
    layout: "releases",
    sync,
    stale: false,
    message: sync === "missing" ? `${SHOP} is not installed on ${node}: noust fleet placement copy ${SHOP} --from <server> --to ${node}` : null,
    href: `/n/${node}/apps/${SHOP}`,
    ...extra,
  };
}

export const STATUS = {
  domain: SHOP,
  members: ["web-2", "web-3"],
  reference_commit: "abc1234",
  in_sync: false,
  servers: [server("web-2", "current", "abc1234"), server("web-3", "differs", "def5678")],
};

export const STATUS_MISSING = {
  ...STATUS,
  servers: [server("web-2", "current", "abc1234"), server("web-3", "missing", null)],
};

export const IN_SYNC = {
  ...STATUS,
  in_sync: true,
  servers: [server("web-2", "current", "abc1234"), server("web-3", "current", "abc1234")],
};

export const ROLLOUT_PLAN = {
  action: "placement_rollout",
  title: "Roll an application out",
  strategy: { serial: 1, max_failures: 0, canary: "web-2" },
  options: { domain: SHOP },
  nodes: [
    { node: "web-2", position: 0, batch: 0, state: "queued", reason: null, step: "abc1234 -> the head of the branch", node_jobs: [], items: [], error: null, output: null, started_at: null, ended_at: null, requires_elevation: false },
    { node: "web-3", position: 1, batch: 1, state: "queued", reason: null, step: "def5678 -> the head of the branch", node_jobs: [], items: [], error: null, output: null, started_at: null, ended_at: null, requires_elevation: false },
  ],
  batches: [["web-2"], ["web-3"]],
  summary: { run: 2, skipped: 0 },
  requires_elevation: false,
  notes: ["web-2 goes first and alone: it builds the head of the branch, and the commit it ends on is the one every other server is asked for, so they all run the same code."],
};

export const SHIP_PLAN = {
  ...ROLLOUT_PLAN,
  action: "placement_ship",
  title: "Build on one server, run on others",
  options: { domain: SHOP, builder: "db-1" },
  notes: [
    "db-1 builds shop.example.com once, in its build sandbox, without activating it; the release travels to each server through its tunnel and is activated there behind the health check, after the application's own hooks.",
    "web-2 goes first and alone: the build is made for it, and only then does it go to the rest.",
  ],
};

export const BUILDER = {
  domain: SHOP,
  node: "db-1",
  created_at: "2026-10-10T10:00:00+00:00",
  created_by: "cli:yago@nas",
  updated_at: "2026-10-10T10:00:00+00:00",
};

export const UPSTREAMS = {
  domain: SHOP,
  format: "nginx",
  port: 443,
  text: "upstream shop_example_com {\n    server web2.example.com:443;  # web-2\n    server web3.example.com:443;  # web-3: on def5678, not abc1234\n}\n",
  servers: [
    { node: "web-2", address: "web2.example.com:443", serving: true, sync: "current", commit: "abc1234", note: null },
    { node: "web-3", address: "web3.example.com:443", serving: true, sync: "differs", commit: "def5678", note: "on def5678, not abc1234" },
  ],
};

/** The fleet of the test fixtures, with the application placed on two of its servers. */
export function placementRoutes(status: unknown = STATUS, builders: readonly unknown[] = []): Record<string, RouteHandler> {
  return {
    ...fleetRoutes(),
    "GET /api/fleet/builders": () => json(200, { builders }),
    [`PUT /api/fleet/builders/${SHOP}`]: (call) => json(200, { ...BUILDER, node: (call.body as { node: string }).node }),
    [`DELETE /api/fleet/builders/${SHOP}`]: () => json(200, BUILDER),
    [`POST /api/fleet/placements/${SHOP}/ship`]: (call) =>
      (call.body as { plan?: boolean }).plan === true
        ? json(200, { plan: SHIP_PLAN, job: null })
        : json(200, { plan: SHIP_PLAN, job: { job_id: "fj-11", status: "pending", message: "queued", job: {} } }),
    "GET /api/fleet/placements": () => json(200, { placements: [PLACEMENT] }),
    [`GET /api/fleet/placements/${SHOP}`]: () => json(200, { placement: PLACEMENT, status }),
    [`GET /api/fleet/placements/${SHOP}/upstreams`]: () => json(200, UPSTREAMS),
    [`PUT /api/fleet/placements/${SHOP}`]: () => json(200, PLACEMENT),
    [`DELETE /api/fleet/placements/${SHOP}`]: () => json(200, PLACEMENT),
    [`POST /api/fleet/placements/${SHOP}/rollout`]: (call) =>
      (call.body as { plan?: boolean }).plan === true
        ? json(200, { plan: ROLLOUT_PLAN, job: null })
        : json(200, { plan: ROLLOUT_PLAN, job: { job_id: "fj-9", status: "pending", message: "queued", job: {} } }),
    [`POST /api/fleet/placements/${SHOP}/copy`]: (call) =>
      (call.body as { plan?: boolean }).plan === true
        ? json(200, { plan: { ...ROLLOUT_PLAN, action: "placement_copy", batches: [["web-3"]], nodes: [ROLLOUT_PLAN.nodes[1]], summary: { run: 1, skipped: 0 }, notes: [], strategy: { serial: 2, max_failures: null, canary: null } }, job: null })
        : json(200, { plan: ROLLOUT_PLAN, job: { job_id: "fj-10", status: "pending", message: "queued", job: {} } }),
  };
}
