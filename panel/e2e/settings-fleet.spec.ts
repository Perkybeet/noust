/**
 * Settings around the central (item 166), against a real central and a real node joined without ssh:
 *
 * - Settings is the central's: the selector is a label with its reason, never a control, and a server chosen elsewhere does
 *   not count there, nor in the two pages of other areas that keep what the central carries (the rules of Alerts, the
 *   backup destinations), whose other tabs lead back to the server that was chosen;
 * - what the central carries is written once in its own section and says how many servers follow it, with a panel that is a
 *   table of servers;
 * - whether a server follows the fleet is decided on its own page, and says what will change before it does;
 * - what is each server's own is a table of servers, and a server's page of it opens from its row;
 * - the old addresses (Fleet settings, the manifest) open the places the things are in now;
 * - a lone server sees the same sections with nothing of a fleet in them.
 *
 * Every page is held to axe and the CSP gate in both themes (the two projects), and to no sideways scroll at 390.
 */

import { csrf } from "./apps-cleanup";
import { expect, expectNoA11yViolations, settle, signIn, startConsoleServer, test } from "./fixtures";
import type { ConsoleServer, RunningServer } from "./fixtures";

const NODE_NAME = "fleet-node";

interface FleetFixtures {
  node: RunningServer;
  central: ConsoleServer;
}

const withFleet = test.extend<object, FleetFixtures>({
  node: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const node = await startConsoleServer([]);
      try {
        await use(node);
      } finally {
        await node.stop();
      }
    },
    { scope: "worker", timeout: 75_000 },
  ],
  central: [
    async ({ node }, use) => {
      const central = await startConsoleServer(["--totp", "--backup-codes", "300", "--fleet-node", `${NODE_NAME}=127.0.0.1:${new URL(node.url).port}`]);
      try {
        await use(central);
      } finally {
        await central.stop();
      }
    },
    { scope: "worker", timeout: 90_000 },
  ],
  baseURL: async ({ central }, use) => {
    await use(central.url);
  },
});

/** Sudo mode for what changes the fleet's settings: the central asks it once. */
async function elevate(page: import("@playwright/test").Page, server: ConsoleServer): Promise<void> {
  const answer = await page.request.post("/api/auth/elevate", { data: { code: server.secondFactor() }, headers: await csrf(page) });
  expect(answer.ok()).toBe(true);
}

withFleet("Settings is the central's: the selector is a label with its reason, and a server chosen elsewhere does not count", async ({ page, central }) => {
  await signIn(page, central, `/n/${NODE_NAME}/apps`);
  await expect(page.getByRole("button", { name: `Server: ${NODE_NAME}` })).toBeVisible();
  await page.goto(`/n/${NODE_NAME}/settings/notifications`);
  // The node is dropped from the address: the channels are the central's.
  await expect(page).toHaveURL(/\/settings\/notifications$/);
  await expect(page.getByRole("heading", { level: 1, name: "Notification channels" })).toBeVisible();
  // The selector is a label that says whose the page is, and no control.
  await expect(page.getByRole("group", { name: /^Server: .*, the central\. Not chosen here$/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Server:/ })).toHaveCount(0);
  await page.getByRole("button", { name: "Why it is not chosen here" }).click();
  await expect(page.getByText("What is here belongs to the central")).toBeVisible();
  await page.keyboard.press("Escape");
  // Nothing in the column says "All the fleet" any more.
  await expect(page.getByText("All the fleet")).toHaveCount(0);
  await settle(page);
  await expectNoA11yViolations(page, "Settings, notification channels, on a fleet");
});

withFleet("the rules of Alerts and the backup destinations are the central's too, and the area's other tabs lead back to the server chosen", async ({ page, central }) => {
  await signIn(page, central, `/n/${NODE_NAME}/backups`);
  await page.goto(`/n/${NODE_NAME}/backups/destinations`);
  await expect(page).toHaveURL(/\/backups\/destinations$/);
  await expect(page.getByRole("group", { name: /the central\. Not chosen here$/ })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Backups, destinations, on a fleet");
  // The tab beside it is a server's: it opens on the server the person had chosen.
  await page.getByRole("navigation", { name: /sections/i }).getByRole("link", { name: "Schedules" }).first().click();
  await expect(page).toHaveURL(new RegExp(`/n/${NODE_NAME}/backups/schedules$`));
  await expect(page.getByRole("button", { name: `Server: ${NODE_NAME}` })).toBeVisible();
});

withFleet("a variable shared with the fleet says how many servers follow it, and its panel is a table of servers", async ({ page, central }) => {
  await signIn(page, central, "/settings/variables");
  await elevate(page, central);
  await expect(page.getByRole("heading", { level: 2, name: "Variables for the whole fleet" })).toBeVisible();
  await page.getByRole("button", { name: "Share a variable" }).click();
  const dialog = page.getByRole("dialog", { name: "Share with the fleet" });
  await dialog.getByLabel(/^Name/).fill("SUPPORT_EMAIL");
  await dialog.getByLabel(/^Value/).fill("help@example.com");
  await dialog.getByRole("button", { name: "Save and apply" }).click();
  const table = page.getByRole("region", { name: "Variables for the whole fleet" });
  await expect(table.getByText("SUPPORT_EMAIL")).toBeVisible();
  // The count is the server's: the central is the source and is never one of the servers that follow.
  await expect(table.getByRole("button", { name: /follow(s)? it$/ })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Settings, variables, one shared");
  await table.getByRole("button", { name: /follow(s)? it$/ }).click();
  const panel = page.getByRole("dialog", { name: "SUPPORT_EMAIL" });
  await expect(panel.getByRole("table", { name: "Servers of SUPPORT_EMAIL" })).toBeVisible();
  await expect(panel.getByRole("row", { name: new RegExp(NODE_NAME) })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a setting's panel: the table of servers");
});

withFleet("what only a server has is said under what the fleet carries, with the way into that server's page", async ({ page, central }) => {
  // The sandbox seeds each server with two backup destinations of its own, which the fleet does not know.
  await signIn(page, central, "/backups/destinations");
  const block = page.getByRole("region", { name: "Destinations for the whole fleet" });
  const only = block.getByRole("list", { name: "Only on one server" });
  await expect(only.getByRole("listitem").filter({ hasText: "offsite-sftp" })).toContainText(`Only ${NODE_NAME} has it. The fleet does not know it.`);
  await settle(page);
  await expectNoA11yViolations(page, "Backups, destinations, with what only a server has");
  // The way in is the server's page, where what it holds is told with the kind of each.
  await only.getByRole("link", { name: `Open the page of ${NODE_NAME}` }).first().click();
  await expect(page).toHaveURL(new RegExp(`/n/${NODE_NAME}/server/central$`));
  const holds = page.getByRole("region", { name: "What it holds" });
  await expect(holds.getByText("Only it has")).toBeVisible();
  await expect(holds.getByText("offsite-sftp")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a server's page with the central, with what only it has");
});

withFleet("a server decides on its own page whether it follows the fleet, and is told what will change", async ({ page, central }) => {
  await signIn(page, central, `/n/${NODE_NAME}/server/central`);
  await elevate(page, central);
  await expect(page.getByRole("heading", { level: 2, name: "The fleet's settings" })).toBeVisible();
  await page.getByRole("radio", { name: /^Independent/ }).click();
  await expect(page.getByText(`${NODE_NAME} will stop getting settings`)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a server's page, about to become independent");
  await page.getByRole("button", { name: "Set it independent" }).click();
  await expect(page.getByRole("heading", { level: 2, name: "The fleet's settings" }).locator("..").getByText("Independent")).toBeVisible();
  // Independent is a decision and is said as one, on the page of every section and in the list of servers.
  await page.goto("/settings/variables");
  await expect(page.getByRole("link", { name: `Open the page of ${NODE_NAME}` })).toBeVisible();
  await page.goto("/fleet/servers");
  await expect(page.getByRole("region", { name: "Servers of this fleet" }).getByText("Independent")).toBeVisible();
  // And back: the page says what it will be given before it is.
  await page.goto(`/n/${NODE_NAME}/server/central`);
  await page.getByRole("radio", { name: /^Follows the fleet's settings/ }).click();
  await expect(page.getByText(`${NODE_NAME} will follow the fleet again`)).toBeVisible();
  await page.getByRole("button", { name: "Follow the fleet again" }).click();
  await expect(page.getByRole("heading", { level: 2, name: "What it holds" })).toBeVisible();
});

withFleet("what is each server's own is a table of servers, and a server's page of it opens from its row", async ({ page, central }) => {
  await signIn(page, central, "/settings/log-shipping");
  await expect(page.getByRole("heading", { level: 2, name: "The other servers" })).toBeVisible();
  const table = page.getByRole("table", { name: /of each server$/ });
  await expect(table.getByRole("row", { name: new RegExp(NODE_NAME) })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Settings, log shipping, the other servers");
  await table.getByRole("row", { name: new RegExp(NODE_NAME) }).getByRole("button", { name: /^Actions for/ }).click();
  await page.getByRole("menuitem", { name: `Manage on ${NODE_NAME}` }).click();
  await expect(page).toHaveURL(new RegExp(`/n/${NODE_NAME}/settings/log-shipping$`));
  await expect(page.getByRole("button", { name: `Server: ${NODE_NAME}` })).toBeVisible();
  await page.getByRole("link", { name: "Back to all servers" }).click();
  await expect(page).toHaveURL(/\/settings\/log-shipping$/);
});

withFleet("the accounts and the tokens of each server are said in one sentence with the command that manages them there", async ({ page, central }) => {
  await signIn(page, central, "/settings/tokens");
  await expect(page.getByText("Each server's tokens are managed on that server")).toBeVisible();
  await expect(page.getByText("noust token list")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Settings, API tokens, on a fleet");
});

withFleet("the old addresses open the places the things are in now", async ({ page, central }) => {
  await signIn(page, central, "/settings/fleet?kind=variable");
  await expect(page).toHaveURL(/\/settings\/variables$/);
  await page.goto("/settings/fleet?kind=channel");
  await expect(page).toHaveURL(/\/settings\/notifications$/);
  await page.goto("/settings/fleet?kind=alert_rule");
  await expect(page).toHaveURL(/\/alerts$/);
  await page.goto("/settings/fleet?kind=backup_destination");
  await expect(page).toHaveURL(/\/backups\/destinations$/);
  await page.goto("/settings/fleet");
  await expect(page).toHaveURL(/\/settings$/);
  await page.goto("/settings/manifest");
  await expect(page).toHaveURL(/\/server\/manifest$/);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
});

withFleet("what was General about the machine is Server's, and the policy stays in Settings", async ({ page, central }) => {
  await signIn(page, central, "/server/folders");
  await expect(page.getByLabel("Applications folder")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Server, folders and builds");
  await page.goto("/settings/general");
  await expect(page.getByRole("heading", { level: 2, name: "Policy" })).toBeVisible();
  await expect(page.getByLabel("Applications folder")).toHaveCount(0);
  await settle(page);
  await expectNoA11yViolations(page, "Settings, general, the policy");
  await page.goto("/server/updates");
  await expect(page.getByRole("heading", { level: 2, name: /Automatic updates/i }).first()).toBeVisible();
});

withFleet("the sections fit a phone", async ({ page, central }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, central, "/settings");
  for (const path of ["/settings/notifications", "/settings/variables", "/settings/console", "/settings/log-shipping", "/settings/general", "/settings/tokens", `/n/${NODE_NAME}/server/central`]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await settle(page);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `${path} scrolls sideways by ${String(overflow)}px at 390`).toBeLessThanOrEqual(0);
  }
});

test("a lone server sees the same sections with nothing of a fleet in them", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/settings/variables");
  await expect(page.getByRole("heading", { level: 1, name: "Variables" })).toBeVisible();
  await expect(page.getByRole("group", { name: /the central\. Not chosen here$/ })).toHaveCount(0);
  await expect(page.getByText("Variables for the whole fleet")).toHaveCount(0);
  await expect(page.getByText("Worth knowing")).toHaveCount(0);
  await page.goto("/settings/log-shipping");
  await expect(page.getByText("The other servers")).toHaveCount(0);
  await page.goto("/settings/tokens");
  await expect(page.getByText("Each server's tokens are managed on that server")).toHaveCount(0);
  await page.goto("/server");
  await expect(page.getByRole("link", { name: "With the central" })).toHaveCount(0);
  await settle(page);
  await expectNoA11yViolations(page, "a lone server's Settings");
});
