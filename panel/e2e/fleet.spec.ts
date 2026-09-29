/**
 * The fleet against two real backends: a central and a node, connected without ssh (see
 * scripts/console_server.py's `--fleet-node`, `join_fleet_node` and
 * noust.fleet.tunnels.set_loopback_for_testing). Every request the browser causes still goes
 * through the real proxy, the real fleet token and the node's own real API - only the ssh
 * dial itself is replaced by a loopback port, so what these tests exercise is the same code a
 * production central runs against a real server.
 *
 * - the selector switches to the node, and the node's own, differently-seeded application
 *   shows where the central's never do;
 * - a deep link to a node's page survives a reload;
 * - the Fleet page lists this server and the node together;
 * - a destructive, elevated action made while on the node lands on the node, not the central;
 * - a node that stops answering says so on its own pages and in the Fleet's attention;
 * - Settings > Servers lists the node and opens the add flow's first step;
 * - a central with sealed secrets shows the lock screen and opens with the right passphrase;
 * - a hub sends its own pages to the Fleet instead.
 */

import type { Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, signIn, startConsoleServer, test, toasts } from "./fixtures";
import type { ConsoleServer, RunningServer } from "./fixtures";
import { confirmItsYou } from "./settings.helpers";

/**
 * Signs in to a server this file started itself, never the worker's default one `baseURL`
 * points at: the address bar is given the full origin up front (`signIn`'s own navigation is
 * relative, resolved against `baseURL`, which is the wrong server for these), with `next`
 * already in the query string so the post-login landing page - and any redirect it triggers,
 * such as a hub's - is exactly what a plain `signIn(page, server, next)` would have reached.
 */
async function signInAt(page: Page, server: ConsoleServer, next?: string): Promise<void> {
  const query = next === undefined ? "" : `?next=${encodeURIComponent(next)}`;
  await page.goto(`${server.url}/login${query}`);
  await signIn(page, server);
}

/** The node's name on the central: 1-32 lower-case letters, digits and dashes, like any other. */
const NODE_NAME = "fleet-node";

/** Seeded only on the node (see NOUST_E2E_FLEET_APP), so seeing it proves the page read the node. */
const NODE_APP = "node-only.example.net";

/** `NODE_APP`, escaped for use inside a `RegExp`. */
const NODE_APP_RE = NODE_APP.replace(/\./g, "\\.");

/** The node's own local port, as `--fleet-node` wants it. */
function portOf(server: ConsoleServer): string {
  return new URL(server.url).port;
}

interface FleetWorkerFixtures {
  node: RunningServer;
  consoleServer: ConsoleServer;
}

/**
 * A central and a node sharing one worker: every test below reads them, and the one
 * destructive test only ever creates and removes its own, disposable service on the node, so
 * the pair stays good for every test scheduled after it on the same worker. The node going
 * down is its own, separate pair (below), never this shared one.
 */
const withFleet = test.extend<object, FleetWorkerFixtures>({
  node: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const node = await startConsoleServer([], { env: { NOUST_E2E_FLEET_APP: NODE_APP } });
      try {
        await use(node);
      } finally {
        await node.stop();
      }
    },
    { scope: "worker", timeout: 75_000 },
  ],
  consoleServer: [
    async ({ node }, use) => {
      const central = await startConsoleServer([
        "--totp",
        "--backup-codes",
        "500",
        "--fleet-node",
        `${NODE_NAME}=127.0.0.1:${portOf(node)}`,
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

withFleet("the selector switches to the node, and the apps list shows its own application", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/apps");
  await expect(page.getByRole("link", { name: NODE_APP, exact: true })).toHaveCount(0);

  await page.getByRole("button", { name: /^Server:/ }).click();
  await page.getByRole("menuitemradio", { name: new RegExp(`^${NODE_NAME}`) }).click();
  await expect(page).toHaveURL(new RegExp(`/n/${NODE_NAME}/apps$`));
  await expect(page.getByRole("link", { name: NODE_APP, exact: true })).toBeVisible();

  await settle(page);
  await expectNoA11yViolations(page, "the apps list, switched to the node");
});

withFleet("a deep link to a node's application works on reload", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/n/${NODE_NAME}/apps/${NODE_APP}`);
  await expect(page).toHaveURL(new RegExp(`/n/${NODE_NAME}/apps/${NODE_APP_RE}$`));
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(NODE_APP);

  await page.reload();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(NODE_APP);
  await settle(page);
  await expectNoA11yViolations(page, "a node's application, reloaded from a deep link");
});

withFleet("the Fleet page shows this server and the node", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/fleet");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Fleet");

  const table = page.getByRole("region", { name: "Servers of this fleet" });
  await expect(table.getByText("This server")).toBeVisible();
  await expect(table.getByRole("link", { name: `Open ${NODE_NAME}` })).toBeVisible();

  await settle(page);
  await expectNoA11yViolations(page, "the Fleet page");
});

withFleet(
  "a destructive action on the node asks to confirm it's you, then succeeds there and only there",
  async ({ page, consoleServer, problems }) => {
    const name = `e2e-fleet-${String(Date.now())}`;
    problems.expect(new RegExp(`status of 403 .*\\/api\\/nodes\\/${NODE_NAME}\\/api\\/services$`));

    await signIn(page, consoleServer, `/n/${NODE_NAME}/services`);
    await page.getByRole("button", { name: "New service" }).click();
    const create = page.getByRole("dialog", { name: "New service" });
    await create.getByLabel("Name", { exact: true }).fill(name);
    await create.getByLabel("Command", { exact: true }).fill("/usr/bin/node worker.js");
    await create.getByRole("button", { name: "Create service" }).click();
    await confirmItsYou(page, consoleServer);
    await expect(toasts(page).getByText(`Created ${name}`)).toBeVisible();
    await expect(page.getByRole("link", { name })).toBeVisible();

    // It landed on the node's own model, not the central's: the central's own services never
    // saw it, which only the proxy actually reaching the node's process could produce.
    await page.goto("/services");
    await expect(page.getByRole("link", { name })).toHaveCount(0);

    await page.goto(`/n/${NODE_NAME}/services/${name}`);
    await page.getByRole("button", { name: "Delete service" }).click();
    const remove = page.getByRole("alertdialog", { name: `Delete ${name}` });
    await remove.locator("input").fill(name);
    await remove.getByRole("button", { name: "Delete service" }).click();
    await expect(page).toHaveURL(new RegExp(`/n/${NODE_NAME}/services$`));
    await expect(toasts(page).getByText(`Deleted ${name}`)).toBeVisible();
    await expect(page.getByRole("link", { name })).toHaveCount(0);
  },
);

withFleet("Settings > Servers shows the node and the add flow's first step", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/settings/servers");
  // Settings shares one heading across every section (SettingsLayout's own PageHeader); the
  // "Servers" table below is what actually says which tab this is.
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Settings");

  const table = page.getByRole("region", { name: "Servers this central manages" });
  const row = table.getByRole("row").filter({ hasText: NODE_NAME });
  await expect(row).toBeVisible();
  await expect(row.getByText("Reachable")).toBeVisible();

  await page.getByRole("button", { name: "Add a server" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a server" });
  await dialog.getByLabel("Name", { exact: true }).fill("temp-add-check");
  await dialog.getByRole("button", { name: "Show the command" }).click();
  // The command names this central (`--name <central>`), not the server being added: it is
  // what the new server will call this central back, not the node's own name.
  const command = dialog.getByTestId("authorize-command");
  await expect(command).toBeVisible();
  await expect(command).toContainText("noust fleet authorize");
  await expect(command).toContainText("--central-key");

  await settle(page);
  await expectNoA11yViolations(page, "the add server dialog, step 1");
});

// ---------------------------------------------------------------------------------------
// The node going down would break every test above sharing the worker's pair, so this one
// starts and tears down its own, and is free to stop the node mid-test.

test("a node that stops answering says so, on its own pages and the Fleet page's attention", async ({ page, problems }) => {
  const node = await startConsoleServer([], { env: { NOUST_E2E_FLEET_APP: NODE_APP } });
  const central = await startConsoleServer([
    "--totp",
    "--backup-codes",
    "500",
    "--fleet-node",
    `${NODE_NAME}=127.0.0.1:${portOf(node)}`,
  ]);
  try {
    problems.expect(new RegExp(`status of 50\\d .*\\/api\\/nodes\\/${NODE_NAME}\\/`));
    await signInAt(page, central, `/n/${NODE_NAME}/apps`);
    await expect(page.getByRole("link", { name: NODE_APP, exact: true })).toBeVisible();

    await node.stop();

    await page.reload();
    await expect(page.getByText(`${NODE_NAME} is not answering`)).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "a node that is not answering");

    await page.goto(`${central.url}/fleet`);
    const attention = page.getByRole("region", { name: "Needs attention" });
    // Exact: the fix text below also names the node, in a `noust node test fleet-node` command.
    await expect(attention.getByText(NODE_NAME, { exact: true })).toBeVisible();
    await expect(attention.getByText("The central cannot reach this server")).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "the Fleet page's attention, with a server down");
  } finally {
    // Closed before either server stops: an authenticated page keeps an /events stream and
    // polls its session, and stopping the backend under it would fail those as console
    // errors the "problems" fixture is still watching for, wrongly, this test's own teardown.
    await page.close();
    await Promise.all([node.stop(), central.stop()]);
  }
});

test("a sealed central shows the lock screen, and opens with the right passphrase", async ({ page, problems }) => {
  const passphrase = "correct horse battery staple";
  const central = await startConsoleServer(["--seal"], { stdin: `${passphrase}\n` });
  try {
    problems.expect(/status of 403 .*\/api\/central\/unlock$/);
    await signInAt(page, central, "/");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("This central is locked");
    await settle(page);
    await expectNoA11yViolations(page, "the locked central's screen");

    await page.getByLabel("Passphrase").fill(passphrase);
    // Exact: "Continue without unlocking" contains "unlocking", a substring match of "Unlock".
    await page.getByRole("button", { name: "Unlock", exact: true }).click();
    await confirmItsYou(page, central);
    await expect(toasts(page).getByText("Unlocked: the servers are within reach again")).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Overview");
  } finally {
    await page.close();
    await central.stop();
  }
});

test("a hub sends its own pages to the Fleet instead", async ({ page }) => {
  const central = await startConsoleServer(["--central-role", "hub"]);
  try {
    await signInAt(page, central, "/apps");
    await expect(page).toHaveURL(/\/fleet(\?|$)/);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Fleet");
    await expect(page.getByText("This central deploys nothing itself")).toBeVisible();

    await page.goto(`${central.url}/`);
    await expect(page).toHaveURL(/\/fleet(\?|$)/);
    await settle(page);
    await expectNoA11yViolations(page, "the Fleet page, opened for a hub");
  } finally {
    await page.close();
    await central.stop();
  }
});
