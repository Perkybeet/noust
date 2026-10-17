/**
 * The fake API an application's settings need in a test: the signed-in shell, the app and the
 * machine-wide lists its header reads, and a quiet answer for each subsection's own reads.
 * Tests override the routes of the subsection they exercise.
 */

import { screen } from "@testing-library/react";

import { renderConsole } from "../../../test/console";
import { APPS, SESSION, fakeBackend, json, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";

export const DOMAIN = "shop.example.com";

/** A session in sudo mode, so actions behind it are not asked about first. */
export const ELEVATED = { ...SESSION, elevated_until: "2999-01-01T00:00:00+00:00" };

export const RELEASE_APP = {
  ...APPS[0],
  path: "/var/www/apps/shop-example-com",
  layout: "releases",
  keep_releases: 5,
  source: "https://github.com/shop/storefront.git",
  branch: "main",
  build_command: ["npm", "run", "build"],
  start_command: "npm run start",
  unit: "shop-example-com.service",
  run_as: "www-data",
  memory_max_mb: 512,
  cpu_quota_percent: 150,
  tasks_max: 256,
  webhook_enabled: false,
};

export const IN_PLACE_APP = { ...RELEASE_APP, layout: "inplace", source: null, branch: null, build_command: [], start_command: null };

export const STATIC_APP = {
  ...RELEASE_APP,
  app_type: "static",
  status: "static",
  port: null,
  layout: "inplace",
  path: "/var/www/apps/landing",
  source: "https://github.com/you/landing",
  branch: null,
  build_command: [],
  start_command: null,
};

/** A job as a queuing endpoint answers it, and as it ends. */
export function jobOf(id: string, type: string, status = "pending", extra: Record<string, unknown> = {}) {
  return {
    id,
    type,
    name: `${type} ${DOMAIN}`,
    description: `${type} of ${DOMAIN}`,
    status,
    progress: status === "pending" ? 0 : 100,
    total_steps: 100,
    current_step: "",
    created_at: "2026-09-29T10:00:00",
    logs: [],
    metadata: { domain: DOMAIN },
    ...extra,
  };
}

export function accepted(job: ReturnType<typeof jobOf>) {
  return { job_id: job.id, status: job.status, message: `${job.name} queued`, job };
}

export const ZERO_DOWNTIME_OFF = {
  domain: DOMAIN,
  enabled: false,
  active_color: null,
  drain_seconds: 10,
  instances: [],
  upstream_port: null,
  eligible: true,
  reason: null,
  hint: null,
};

/** What the startup check has beside the path, the statuses and the time: nothing, as a new app. */
export const NO_HEALTH_EXTRAS = { kind: "http", method: null, scheme: null, host: null, port: null, command: null, service: null, interval: null, retries: null, start_period: null };

/** The whole startup check as the API reads it, for an application whose check is its three fields. */
export function healthOf(app: Record<string, unknown>, extras: Record<string, unknown> = {}) {
  return {
    domain: DOMAIN,
    path: app["health_path"] ?? null,
    expect: app["health_expect"] ?? null,
    timeout: app["health_timeout"] ?? null,
    ...NO_HEALTH_EXTRAS,
    ...extras,
    effective_path: "/",
    effective_expect: "any status below 500",
    effective_timeout: 30,
    effective_summary: "",
  };
}

/**
 * Renders one subsection of the app's settings over the fake API and waits for its heading.
 *
 * @param path The subsection's path after `/settings`: "" for General, "/deploys"...
 * @param heading The subsection's h2, which says it rendered.
 */
export async function renderSettings(path: string, heading: string, app: object = RELEASE_APP, routes: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    [`GET /api/apps/${DOMAIN}`]: () => json(200, app),
    [`GET /api/apps/${DOMAIN}/pending`]: () => json(200, { domain: DOMAIN, pending: false, items: [], counts: {}, needs: null, since: null, deployment_id: null, baseline_created: false }),
    "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
    "GET /api/sites": () => json(200, { sites: [], total: 0, webserver: "nginx" }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    "GET /api/system": () => json(200, { cpu: { cores: 4, percent: 3, load_1min: 0.1, load_5min: 0.1, load_15min: 0.1 } }),
    [`GET /api/apps/${DOMAIN}/releases`]: () => json(200, { domain: DOMAIN, items: [], total: 0 }),
    [`GET /api/apps/${DOMAIN}/zero-downtime`]: () => json(200, ZERO_DOWNTIME_OFF),
    [`GET /api/apps/${DOMAIN}/health`]: () => json(200, healthOf(app as Record<string, unknown>)),
    [`GET /api/apps/${DOMAIN}/previews`]: () => json(200, { domain: DOMAIN, enabled: false, settings: null, previews: [], total: 0 }),
    [`GET /api/apps/${DOMAIN}/networks`]: () => json(200, { app: DOMAIN, applicable: true, reason: "", containers: [], isolated: true, networks: [] }),
    ...routes,
  });
  const harness = renderConsole(`/apps/${DOMAIN}/settings${path}`);
  await screen.findByRole("heading", { level: 2, name: heading });
  return { ...harness, backend };
}

/** The first of what a query matched, which a test that needs it has already shown is there. */
export function first<T>(items: readonly T[]): T {
  const [item] = items;
  if (item === undefined) throw new Error("nothing matched");
  return item;
}

/** The last of what a query matched. */
export function last<T>(items: readonly T[]): T {
  const item = items.at(-1);
  if (item === undefined) throw new Error("nothing matched");
  return item;
}

/** A card of the subsection, by its heading: the element the heading titles. */
export function part(name: string): HTMLElement {
  const heading = screen.getByRole("heading", { name });
  const card = heading.closest("section, [role='group']");
  if (!(card instanceof HTMLElement)) throw new Error(`No card titled ${name}`);
  return card;
}

/** The save bar of the subsection on screen. */
export function saveBar(): HTMLElement {
  return screen.getByRole("region", { name: "Unsaved changes" });
}
