/**
 * Screenshots for docs/assets/console/**, from an invented small agency's server instead of
 * the E2E suite's own fixture domains (example.com and friends): these pictures are published
 * in docs/console.md, docs/CENTRAL.md and README.md (which also renders on PyPI), and the
 * owner does not want a real project, company, domain, GitHub account or person recognisable
 * in them, nor example.com itself, which reads as a fixture rather than a real server. The
 * dataset is tests.showcase, seeded by `scripts/console_server.py --showcase` (see its module
 * docstring for how every invented name was checked against the real thing before use).
 *
 * Tagged @docs and left out of the default run, the same way @screens is in
 * screenshots.spec.ts: `npm run docs:screens` sets NOUST_DOCS_SCREENS=1, which this project's
 * playwright.config.ts turns into `--grep @docs`, and runs the light project only - the
 * console has no reason to publish a dark-theme picture of itself next to a light one, and the
 * pictures already in the tree are all light.
 *
 * Every capture is 1440px wide, full page, after `settle()` (fonts in, network idle, no
 * running animation) and with focus removed from whatever the last action landed on, so
 * nothing here carries a focus ring, a toast or a hover state left over from getting there.
 */

import type { Page } from "@playwright/test";
import path from "node:path";

import { expect, settle, signIn, startConsoleServer, test } from "./fixtures";
import type { ConsoleServer, RunningServer } from "./fixtures";

/** Where the pictures the documentation links land; committed, unlike e2e/__screens__. */
const OUT = path.resolve(import.meta.dirname, "..", "..", "docs", "assets", "console");

const DESKTOP = { width: 1440, height: 900 };

/** The showcase's own hero application: released four times, a certificate, an alias. */
const HERO_APP = "wrenfield.io";

/** Down since this morning, with a realistic journal: the Diagnose and failed-deploy shots. */
const FAILED_APP = "shop.brumaria.es";

/** The fleet's own node names (see tests.showcase's module docstring: infrastructure labels,
 * not domains, so they need no NXDOMAIN check), picked the way a real fleet's would be. */
const NODE_LON = "lon-2";
const NODE_AMS = "ams-3";

/** The node whose own Applications page is captured (item 3 of the brief: "one more worth
 * showing for 3.0"): any one of the fleet's members works, since --showcase seeds each node
 * the same believable machine. */
const FEATURED_NODE = NODE_LON;

/** The node's own local port, as `--fleet-node` wants it. */
function portOf(server: ConsoleServer): string {
  return new URL(server.url).port;
}

/** The newest deployment of a showcase domain, as the API lists it: its id is not fixed. */
function newestDeployment(domain: string) {
  return async (page: Page): Promise<string> => {
    const response = await page.request.get(`/api/deployments?domain=${domain}&limit=1`);
    const newest = ((await response.json()) as { items: { id: number }[] }).items[0];
    if (newest === undefined) throw new Error(`the showcase has no deployment of ${domain}`);
    return `/apps/${domain}/deployments/${String(newest.id)}`;
  };
}

/** Blurs whatever has focus: the one thing `settle()` does not do, and a screenshot must not
 * carry a focus ring left by the click or fill that got the page here. */
async function defocus(page: Page): Promise<void> {
  // A click on the page itself (not any control) is what a real operator's next move
  // blurs a field with; document.activeElement.blur() alone raced an input's own
  // autofocus effect on the login page (coordinator review) and lost often enough to
  // matter, landing the screenshot mid-focus-ring.
  await page.locator("body").click({ position: { x: 2, y: 2 }, force: true });
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
}

async function shot(page: Page, name: string): Promise<void> {
  await settle(page);
  await defocus(page);
  await settle(page);
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true });
}

interface DocsRoute {
  name: string;
  path: string | ((page: Page) => Promise<string>);
}

/** Every page but the fleet ones, which need the two-node fixture below. */
const ROUTES: readonly DocsRoute[] = [
  { name: "overview", path: "/" },
  { name: "apps", path: "/apps" },
  { name: "apps-new", path: "/apps/new" },
  { name: "app-overview", path: `/apps/${HERO_APP}` },
  { name: "app-deployments", path: `/apps/${FAILED_APP}/deployments` },
  { name: "app-deployment", path: newestDeployment(FAILED_APP) },
  { name: "app-diagnose", path: `/apps/${FAILED_APP}/diagnose` },
  { name: "domains", path: "/domains" },
];

/**
 * A single showcase server, no two-factor: the plain, one-step sign-in the login shot wants,
 * and every page that is not about the fleet reads a plain server just as well as a central.
 */
const withShowcase = test.extend<object, { consoleServer: ConsoleServer }>({
  consoleServer: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const server = await startConsoleServer(["--showcase", "--hostname", "fra-1"]);
      try {
        await use(server);
      } finally {
        await server.stop();
      }
    },
    { scope: "worker", timeout: 75_000 },
  ],
});

interface FleetWorkerFixtures {
  lon: RunningServer;
  ams: RunningServer;
  consoleServer: ConsoleServer;
}

/**
 * The showcase's fleet: a central ("fra-1") and two nodes, joined without ssh exactly as
 * fleet.spec.ts's own fixture does. A central refuses to register a node without two-factor
 * sign-in on (noust.fleet.nodes.NodeManager.add), which the plain server above deliberately
 * has off for its own simpler login shot - this is a second, separate central for exactly the
 * three fleet-shaped pages, `signIn()` spending a backup code for each.
 */
const withFleet = test.extend<object, FleetWorkerFixtures>({
  lon: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const node = await startConsoleServer(["--showcase", "--hostname", NODE_LON]);
      try {
        await use(node);
      } finally {
        await node.stop();
      }
    },
    { scope: "worker", timeout: 75_000 },
  ],
  ams: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const node = await startConsoleServer(["--showcase", "--hostname", NODE_AMS]);
      try {
        await use(node);
      } finally {
        await node.stop();
      }
    },
    { scope: "worker", timeout: 75_000 },
  ],
  consoleServer: [
    async ({ lon, ams }, use) => {
      const central = await startConsoleServer([
        "--showcase",
        "--hostname",
        "fra-1",
        "--totp",
        "--backup-codes",
        "50",
        "--fleet-node",
        `${NODE_LON}=127.0.0.1:${portOf(lon)}`,
        "--fleet-node",
        `${NODE_AMS}=127.0.0.1:${portOf(ams)}`,
      ]);
      try {
        await use(central);
      } finally {
        await central.stop();
      }
    },
    { scope: "worker", timeout: 90_000 },
  ],
});

test.describe("docs @docs", () => {
  withShowcase("login", async ({ page }) => {
    // baseURL (fixtures.ts) already depends on the consoleServer fixture, so requesting
    // page alone is enough to have the showcase server up before this navigates.
    await page.setViewportSize(DESKTOP);
    await page.goto("/login");
    await shot(page, "login");
  });

  for (const route of ROUTES) {
    withShowcase(route.name, async ({ page, consoleServer }) => {
      await page.setViewportSize(DESKTOP);
      if (typeof route.path === "string") {
        await signIn(page, consoleServer, route.path);
      } else {
        await signIn(page, consoleServer);
        await page.goto(await route.path(page));
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      }
      await shot(page, route.name);
    });
  }

  withFleet("fleet", async ({ page, consoleServer }) => {
    await page.setViewportSize(DESKTOP);
    await signIn(page, consoleServer, "/fleet");
    await shot(page, "fleet");
  });

  withFleet("settings-servers", async ({ page, consoleServer }) => {
    await page.setViewportSize(DESKTOP);
    await signIn(page, consoleServer, "/settings/servers");
    await shot(page, "settings-servers");
  });

  withFleet("node-apps", async ({ page, consoleServer }) => {
    await page.setViewportSize(DESKTOP);
    await signIn(page, consoleServer, `/n/${FEATURED_NODE}/apps`);
    await shot(page, "node-apps");
  });
});
