/**
 * The fake API an application's page needs in a test: the signed-in shell, the app itself and
 * the machine-wide lists its header reads. Tests add the routes of the tab they exercise.
 */

import { vi } from "vitest";

import { json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";

export const TAB_DOMAIN = "shop.example.com";

export const TAB_APP = {
  name: TAB_DOMAIN,
  domain: TAB_DOMAIN,
  status: "running",
  active: true,
  enabled: true,
  pid: 41234,
  uptime: "Fri 2026-09-25 13:06:35 UTC",
  port: 3000,
  app_type: "nextjs",
  path: `/var/www/apps/shop-example-com`,
  layout: "inplace",
  memory_max_mb: null,
  cpu_quota_percent: null,
  tasks_max: null,
};

export function appRoutes(app: Partial<typeof TAB_APP> | Record<string, unknown> = {}, extra: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  const merged = { ...TAB_APP, ...app };
  return {
    ...signedInRoutes(),
    [`GET /api/apps/${TAB_DOMAIN}`]: () => json(200, merged),
    "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
    "GET /api/sites": () => json(200, { sites: [], total: 0, webserver: "nginx" }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    ...extra,
  };
}

/**
 * Makes the window `width` pixels wide for the media queries the console reads (`min-width`
 * in rem, 16px each): the default jsdom window matches none, which is a phone. Anything that is
 * not a width query (reduced motion, a coarse pointer) does not match. Undone after each test.
 */
export function screenWidth(width: number): void {
  vi.stubGlobal("matchMedia", (query: string) => {
    const min = /min-width:\s*([\d.]+)(rem|px)/.exec(query);
    const matches = min !== null && width >= Number(min[1]) * (min[2] === "rem" ? 16 : 1);
    return {
      matches,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    };
  });
}
