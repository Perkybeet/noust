/**
 * How long every settings subsection is (docs/DESIGN.md FM-8 and NV-9): one subsection is one
 * subject and fits in one and a half screens at 1440px. A page that is longer is split, not
 * scrolled, and nothing in the console measured it until this spec did: the longest subsection of
 * 3.3.1 was 6102px, the length of seven screens.
 *
 * The height is that of the subsection's content column, at 1440 by 900, once the page has stopped
 * loading. Each route has its height of today in settings-length.baseline.json, and the baseline
 * only goes down:
 *
 * - a route longer than its baseline fails (it grew: move what was added to a subsection of its
 *   own, or to the subject it belongs to);
 * - a route shorter than its baseline by more than the slack fails too, asking for the baseline to
 *   be lowered, so the progress is kept;
 * - a route over the limit of 1400px needs a `reason` in the baseline, which says what splits it
 *   and when. A route that had none and is over the limit is a regression whatever its height.
 *
 * A lane that splits a subsection lowers the entries of its routes and adds the entries of the
 * routes it creates (measure with `NOUST_E2E_MEASURE=1`, write with `NOUST_E2E_SETTINGS_BASELINE=write`,
 * which sets a new route to what it measures and never raises an old one).
 *
 * Both lists below are the routes the console has: a settings route file nobody listed fails the
 * first test, so a subsection cannot land without a length.
 */

import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import path from "node:path";

import type { Page } from "@playwright/test";

import { expect, settle, signIn, startConsoleServer, test } from "./fixtures";

interface Entry {
  path: string;
  height: number;
  /** Why a route over the limit is as long as it is, and what shortens it. */
  reason?: string;
}

interface Baseline {
  $comment?: string;
  viewport: { width: number; height: number };
  /** The height from which a route needs a reason: a screen and a half at 1440 by 900. */
  limit: number;
  /** How much a route may differ from its baseline before the test says so (fonts and rounding). */
  tolerance: number;
  /** How much shorter than its baseline a route may be before the baseline has to come down. */
  slack: number;
  routes: Record<string, Entry>;
}

const BASELINE_FILE = path.join(import.meta.dirname, "settings-length.baseline.json");

function readBaseline(): Baseline {
  return JSON.parse(readFileSync(BASELINE_FILE, "utf8")) as Baseline;
}

/** Only measures: reports every height and asserts none. */
const MEASURE_ONLY = process.env.NOUST_E2E_MEASURE === "1";
/** Writes the heights it measures into the baseline: new routes as they are, old ones never higher. */
const WRITE = process.env.NOUST_E2E_SETTINGS_BASELINE === "write";

interface Route {
  id: string;
  path: string;
}

/** The server's settings, one URL each (the list `app/nav.ts` calls `settings.*`). Controls is not one yet: it opens Security. */
const SERVER_SETTINGS: readonly Route[] = [
  { id: "settings-general", path: "/settings" },
  { id: "settings-variables", path: "/settings/variables" },
  { id: "settings-secret-managers", path: "/settings/secret-managers" },
  { id: "settings-console", path: "/settings/console" },
  { id: "settings-integrations", path: "/settings/integrations" },
  { id: "settings-about", path: "/settings/about" },
  { id: "settings-notifications", path: "/settings/notifications" },
  { id: "settings-alerts", path: "/settings/alerts" },
  { id: "settings-probes", path: "/settings/probes" },
  { id: "settings-log-shipping", path: "/settings/log-shipping" },
  { id: "settings-tokens", path: "/settings/tokens" },
  { id: "settings-api", path: "/settings/api" },
  { id: "settings-manifest", path: "/settings/manifest" },
  { id: "settings-mcp", path: "/settings/mcp" },
  { id: "settings-accounts", path: "/settings/accounts" },
  { id: "settings-access", path: "/settings/access" },
  { id: "settings-sso", path: "/settings/sso" },
  { id: "settings-approvals", path: "/settings/approvals" },
  { id: "settings-audit", path: "/settings/audit" },
  { id: "settings-compliance", path: "/settings/compliance" },
  { id: "settings-cloud", path: "/settings/cloud" },
  { id: "settings-central", path: "/settings/central" },
  { id: "settings-security", path: "/settings/security" },
];

/**
 * An application's settings: the subsection and an application of the seeded machine that has it
 * (the same ones `e2e/routes.ts` opens). Container is not here: the seed has no container application.
 */
const APPLICATION_SETTINGS: readonly Route[] = [
  { id: "app-settings-general", path: "/apps/shop.example.net/settings" },
  { id: "app-settings-source", path: "/apps/taller.example.com/settings/source" },
  { id: "app-settings-deploys", path: "/apps/tienda.example.org/settings/deploys" },
  { id: "app-settings-deploy-on-push", path: "/apps/tienda.example.org/settings/deploy-on-push" },
  { id: "app-settings-hooks", path: "/apps/tienda.example.org/settings/hooks" },
  { id: "app-settings-automation", path: "/apps/tienda.example.org/settings/automation" },
  { id: "app-settings-access", path: "/apps/tienda.example.org/settings/access" },
  { id: "app-settings-protection", path: "/apps/tienda.example.org/settings/protection" },
  { id: "app-settings-build", path: "/apps/shop.example.net/settings/build" },
  { id: "app-settings-resources", path: "/apps/shop.example.net/settings/resources" },
  { id: "app-settings-disk", path: "/apps/shop.example.net/settings/disk" },
  { id: "app-settings-processes", path: "/apps/tienda.example.org/settings/processes" },
  { id: "app-settings-security", path: "/apps/tienda.example.org/settings/security" },
  { id: "app-settings-mounts", path: "/apps/shop.example.net/settings/mounts" },
  { id: "app-settings-previews", path: "/apps/portal.example.org/settings/previews" },
  { id: "app-settings-export", path: "/apps/shop.example.net/settings/export" },
  { id: "app-settings-delete", path: "/apps/shop.example.net/settings/delete" },
];

test.use({ viewport: { width: 1440, height: 900 } });

/** Settings route files that are not a subsection of their own, with the reason. */
const NOT_A_SUBSECTION: Readonly<Record<string, string>> = {
  "settings/controls.tsx": "it opens Security until its page lands; the lane that writes it adds the route here and takes this line away",
  "apps/$domain/settings/container.tsx": "only a container application has it, and the seeded machine has none",
  "apps/$domain/settings/compose.tsx": "only a Compose stack has it, and the seeded machine has none",
  "apps/$domain/settings/builds.tsx": "it redirects to Build: the sandbox is a card of that page since 4.0",
  "settings/servers.tsx": "it redirects to Fleet > Servers: the fleet's list is the one list of servers since 4.0",
};

/** The route files under a settings directory of src/routes, as `settings/x.tsx` relative to _console. */
function routeFiles(directory: string): string[] {
  const base = path.join(import.meta.dirname, "..", "src", "routes", "_console");
  return readdirSync(path.join(base, directory))
    .filter((name) => name.endsWith(".tsx"))
    .map((name) => `${directory}/${name}`);
}

test("every settings route of the console has a length in the baseline", () => {
  const listed = new Set([...SERVER_SETTINGS, ...APPLICATION_SETTINGS].map((route) => route.id));
  const files = [...routeFiles("settings"), ...routeFiles("apps/$domain/settings")].filter((file) => NOT_A_SUBSECTION[file] === undefined);
  // settings/index.tsx and apps/$domain/settings/index.tsx are General.
  const ids = files.map((file) => {
    const name = path.basename(file, ".tsx");
    const prefix = file.startsWith("apps/") ? "app-settings" : "settings";
    return `${prefix}-${name === "index" ? "general" : name}`;
  });
  expect(
    ids.filter((id) => !listed.has(id)),
    "A settings route has no entry here: add it to the list of this spec and measure it (NOUST_E2E_SETTINGS_BASELINE=write)",
  ).toEqual([]);
  const baseline = readBaseline();
  expect(
    [...listed].filter((id) => baseline.routes[id] === undefined),
    "A listed route has no height in settings-length.baseline.json",
  ).toEqual([]);
});

/**
 * The length of the subsection: what its content column holds (the settings layout's
 * `data-slot="content"`), the part that is the subsection's own and not the page frame around it
 * (the header, the tabs, the room below), which is the same on every route. A page not drawn by the
 * settings layout is measured whole. Waits for the page to stop moving: nothing busy, fonts in, and
 * the height the same three times running.
 */
async function heightOf(page: Page): Promise<number> {
  await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
  await expect(page.locator("main [aria-busy=true]")).toHaveCount(0, { timeout: 15_000 });
  await settle(page);
  let last = -1;
  let steady = 0;
  for (let attempt = 0; attempt < 40 && steady < 3; attempt += 1) {
    const now = await page.evaluate(() => {
      // The layout's column is the sibling of its side list; the page template has a content slot of its own around both.
      const content = document.querySelector('[data-slot="nav"]')?.parentElement?.querySelector(':scope > [data-slot="content"]') ?? null;
      if (content === null) return Math.ceil(document.documentElement.scrollHeight);
      // The column stretches to the height of the side list beside it, so its own box is a floor and
      // says nothing about a short page: what is measured is what it holds, one child under the next.
      const kids = [...content.children].filter((child) => getComputedStyle(child).display !== "none");
      const gap = parseFloat(getComputedStyle(content).rowGap) || 0;
      const stacked = kids.reduce((sum, child) => sum + child.getBoundingClientRect().height, 0);
      return Math.ceil(stacked + gap * Math.max(0, kids.length - 1));
    });
    steady = now === last ? steady + 1 : 0;
    last = now;
    await page.waitForTimeout(250);
  }
  return last;
}

test("every settings subsection fits in a screen and a half, or says why not, and none grows", async ({ browser }, testInfo) => {
  // Heights do not depend on the theme: the dark project would measure the same.
  test.skip(testInfo.project.name !== "light", "the length of a page does not depend on the theme");
  test.setTimeout(420_000);
  const baseline = readBaseline();
  const routes = [...SERVER_SETTINGS, ...APPLICATION_SETTINGS];
  const measured: Record<string, { path: string; height: number; landed: string }> = {};

  // A server of its own, as the seed leaves it and set up as the suite's (two-factor sign-in): the audit trail and the
  // list of sessions grow with whatever the worker's shared server was asked before (a save from another spec,
  // another sign-in), and so would their pages.
  const server = await startConsoleServer(["--totp", "--backup-codes", "20"]);
  const context = await browser.newContext({ baseURL: server.url, viewport: { width: 1440, height: 900 } });
  try {
    const page = await context.newPage();
    await signIn(page, server, routes[0]?.path);
    for (const route of routes) {
      await page.goto(route.path);
      const height = await heightOf(page);
      const landed = new URL(page.url()).pathname;
      measured[route.id] = { path: route.path, height, landed };
      console.log(`SETTINGS_LENGTH ${route.id} ${String(height)}${landed === route.path ? "" : ` (landed on ${landed})`}`);
    }
  } finally {
    await context.close();
    await server.stop();
  }

  if (WRITE) {
    const next = readBaseline();
    for (const [id, found] of Object.entries(measured)) {
      const old = next.routes[id];
      next.routes[id] = { ...(old ?? {}), path: found.path, height: old === undefined ? found.height : Math.min(old.height, found.height) };
    }
    writeFileSync(BASELINE_FILE, `${JSON.stringify(next, null, 2)}\n`);
  }
  if (MEASURE_ONLY || WRITE) return;

  for (const route of routes) {
    const found = measured[route.id];
    const entry = baseline.routes[route.id];
    if (found === undefined) continue;
    expect.soft(entry, `${route.id} has no entry in settings-length.baseline.json: measure it with NOUST_E2E_SETTINGS_BASELINE=write`).toBeDefined();
    if (entry === undefined) continue;
    expect
      .soft(found.height, `${route.id} (${route.path}) is ${String(found.height)}px tall; its baseline is ${String(entry.height)}px. Split what was added into a subsection of its own (NV-9, FM-8)`)
      .toBeLessThanOrEqual(entry.height + baseline.tolerance);
    expect
      .soft(
        found.height,
        `${route.id} is ${String(found.height)}px tall, ${String(entry.height - found.height)}px shorter than its baseline: lower it in e2e/settings-length.baseline.json so the progress is kept`,
      )
      .toBeGreaterThanOrEqual(entry.height - baseline.slack);
  }

  // A route over the limit says why; a baseline that no route owns is stale.
  for (const [id, entry] of Object.entries(baseline.routes)) {
    if (entry.height > baseline.limit) {
      expect.soft(entry.reason?.trim() ?? "", `${id} is ${String(entry.height)}px, over ${String(baseline.limit)}px: its baseline needs a reason (what makes it long, and what splits it)`).not.toBe("");
    }
    expect.soft(measured[id], `${id} is in the baseline but is not a route this spec visits: remove it, or add the route`).toBeDefined();
  }
});
