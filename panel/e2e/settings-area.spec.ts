/**
 * The tasks of the Settings area of the 4.2 mockup (tanda 3, section 5), walked from Home against the real backend, in both
 * themes, with axe and the security policy on every page:
 *
 * - sign in with the company's Microsoft account: Single sign-on says what it is for under its name, the first step names
 *   what the company has, and the provider is there at the end;
 * - turn on a framework a customer asks for, and see its report instead of a warning: with none on there is nothing red;
 * - stop receiving notifications for a while: the switch asks until when, the page says until when, and Alerts offers the
 *   same question and the way back;
 * - on a fleet, "All the fleet" is said once for a group in which every section is the fleet's, and on the row where the
 *   group is mixed; what a server shows before anyone signs in is its own and is never said to be the fleet's.
 */

import type { Page } from "@playwright/test";

import { csrf } from "./apps-cleanup";
import { expect, expectNoA11yViolations, settle, signIn, startConsoleServer, test } from "./fixtures";
import type { ConsoleServer, RunningServer } from "./fixtures";
import { confirmItsYou } from "./settings.helpers";

const own = test.extend<object, { consoleServer: ConsoleServer }>({
  consoleServer: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const server = await startConsoleServer(["--totp", "--backup-codes", "200"]);
      try {
        await use(server);
      } finally {
        await server.stop();
      }
    },
    { scope: "worker", timeout: 75_000 },
  ],
});
own.describe.configure({ mode: "serial" });

/** Confirms sudo mode for this browser over the API, with a second factor nothing has spent. */
async function elevate(page: Page, server: ConsoleServer): Promise<void> {
  const answer = await page.request.post("/api/auth/elevate", { data: { code: server.secondFactor() }, headers: await csrf(page) });
  expect(answer.ok()).toBe(true);
}

/** Opens Settings from the main bar, the way a person who starts on Home does. */
async function openSettings(page: Page): Promise<void> {
  await page.getByRole("navigation", { name: "Main" }).first().getByRole("link", { name: "Settings" }).click();
  await expect(page).toHaveURL(/\/settings$/);
}

own("Home to a company sign-in: the entry says what it is for, and the provider is there at the end", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer, "/");
  await elevate(page, consoleServer);
  await openSettings(page);

  const column = page.getByRole("navigation", { name: "Settings sections" });
  // A newcomer does not know "single sign-on": the entry says it is the company's account.
  await column.getByRole("link", { name: /^Single sign-on With the company account/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Single sign-on" })).toBeVisible();
  await expect(page.getByText(/Connect the company's Microsoft or Google account/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Single sign-on, with no provider");

  // The first step is what the company has, in its own words; Microsoft is the one asked for.
  await page.getByRole("button", { name: "Add a provider" }).click();
  const choice = page.getByRole("dialog", { name: "Add a provider" });
  await expect(choice.getByRole("radio", { name: "Microsoft Entra ID" })).toBeChecked();
  await expect(choice.getByRole("radio", { name: "Google Workspace" })).toBeVisible();
  await expect(choice.getByRole("radio", { name: /A LDAP directory/ })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the first step of adding a provider");
  await choice.getByRole("button", { name: "Continue" }).click();

  // The form that follows already has the name and the label of what was chosen.
  const drawer = page.getByRole("dialog", { name: "Add an identity provider" });
  await expect(drawer.getByLabel("Name", { exact: true })).toHaveValue("entra");
  await expect(drawer.getByLabel("Button label")).toHaveValue("Microsoft Entra ID");
  await drawer.getByLabel("Issuer").fill("https://login.microsoftonline.com/0b7c3e5a/v2.0");
  await drawer.getByLabel("Client ID").fill("noust");
  await drawer.getByLabel("Client secret").fill("a-secret-that-is-never-shown-again");
  await drawer.getByRole("button", { name: "Add provider" }).click();
  const providers = page.getByRole("region", { name: "Sign-in connections" });
  await expect(providers.getByText("Microsoft Entra ID")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Single sign-on, with a provider");

  // The accounts page leads here for whoever asks the same question there.
  await column.getByRole("link", { name: "Accounts" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Accounts" })).toBeVisible();
  await page.getByRole("link", { name: "Single sign-on" }).last().click();
  await expect(page).toHaveURL(/\/settings\/sso$/);

  // What was made is taken away, so the next walk starts where this one did.
  await providers.getByRole("button", { name: "Actions for entra" }).click();
  await page.getByRole("menuitem", { name: "Remove" }).click();
  await page.getByRole("alertdialog", { name: "Remove entra" }).getByRole("button", { name: "Remove provider" }).click();
  await expect(page.getByText(/Connect the company's Microsoft or Google account/)).toBeVisible();
});

own("Home to a framework a customer asks for: nothing is red until one is on, and then its report is the page", async ({ page, consoleServer, problems }) => {
  // Turning one off asks "Confirm it's you" by answering 403 first, by design.
  problems.expect(/status of 403 .* \/api\/compliance$/);
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer, "/");
  await openSettings(page);
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: "Compliance" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Compliance" })).toBeVisible();

  // With none on, which is the usual state of a server, the page says so as a fact: no report, no red.
  await expect(page.getByRole("heading", { level: 2, name: "No framework is on" })).toBeVisible();
  await expect(page.getByText(/That is not a warning: it is normal when nothing obliges you/)).toBeVisible();
  for (const name of ["ENS MEDIA", "ISO/IEC 27001:2022", "NIS2"]) await expect(page.getByRole("heading", { level: 3, name })).toBeVisible();
  await expect(page.locator('main [data-state="failed"], main [data-state="warning"]')).toHaveCount(0);
  await settle(page);
  await expectNoA11yViolations(page, "Compliance, with no framework on");

  // Turning one on says first what changes; what stays the organisation's is one press away.
  const card = page.locator("article").filter({ has: page.getByRole("heading", { level: 3, name: "NIS2" }) });
  await card.getByRole("button", { name: "Turn on…" }).click();
  const dialog = page.getByRole("alertdialog", { name: "Turn on NIS2" });
  await expect(dialog.getByText("Encrypted backups")).toBeVisible();
  await dialog.getByRole("button", { name: "What stays yours" }).click();
  await settle(page);
  await expectNoA11yViolations(page, "the dialog that turns a framework on");
  await dialog.getByRole("button", { name: "Turn on" }).click();

  // The page is its report now: how many controls are in force, and the way to hand it over.
  const report = page.locator("section").filter({ has: page.getByRole("heading", { level: 2, name: "NIS2" }) }).last();
  await expect(report.getByText(/controls in force|The control is in force/)).toBeVisible();
  await expect(page.getByRole("main").getByRole("link", { name: "Download the report" })).toHaveAttribute("href", "/api/compliance/check?format=markdown");
  await expect(page.getByRole("heading", { level: 2, name: "No framework is on" })).toHaveCount(0);
  await settle(page);
  await expectNoA11yViolations(page, "Compliance, with a framework on");

  // Turned off again, behind "Confirm it's you": the server is as it was.
  await report.getByRole("button", { name: "Turn off…" }).click();
  const off = page.getByRole("alertdialog", { name: "Turn off NIS2" });
  await off.getByRole("button", { name: "Turn off" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(page.getByRole("heading", { level: 2, name: "No framework is on" })).toBeVisible();
});

own("Home to a quiet hour: the switch asks until when, Settings and Alerts say until when, and both can end it", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer, "/");
  await elevate(page, consoleServer);
  await openSettings(page);
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: "Notification channels" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Notification channels" })).toBeVisible();

  // The switch governs the page and is on the title's line: nowhere near the far edge.
  const master = page.getByRole("switch", { name: "Send notifications" });
  if (!(await master.isChecked())) await master.click();
  await expect(master).toBeChecked();
  // The switch shows where it is going while the write is in flight, and cannot be pressed meanwhile: wait for the write.
  await settle(page);

  // Off is a question, because off is almost always "for a while".
  await master.click();
  const dialog = page.getByRole("dialog", { name: "Silence the notifications until when?" });
  await expect(dialog.getByRole("radio", { name: /For an hour/ })).toBeChecked();
  await settle(page);
  await expectNoA11yViolations(page, "the question of until when");
  await dialog.getByRole("button", { name: "Silence" }).click();
  await expect(page.getByText(/^Silenced until /)).toBeVisible();
  await expect(master).not.toBeChecked();
  await settle(page);
  await expectNoA11yViolations(page, "Notification channels, silenced");

  // Alerts says it too, and turns them on again.
  await page.goto("/alerts");
  await expect(page.getByRole("heading", { level: 1, name: "Alerts" })).toBeVisible();
  await expect(page.getByText(/^Silenced until /)).toBeVisible();
  await page.getByRole("button", { name: "Turn on now" }).click();
  await expect(page.getByRole("button", { name: "Silence notifications…" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Alerts, with the way to silence");

  // The same dialog from here, and the answer that is the switch's own meaning: until they are turned on.
  await page.getByRole("button", { name: "Silence notifications…" }).click();
  const again = page.getByRole("dialog", { name: "Silence the notifications until when?" });
  await again.getByRole("radio", { name: /Until I turn them on/ }).check();
  await again.getByRole("button", { name: "Silence" }).click();
  await expect(again).toBeHidden();
  await page.goto("/settings/notifications");
  await expect(page.getByRole("switch", { name: "Send notifications" })).not.toBeChecked();
  await expect(page.getByText(/^Silenced until /)).toHaveCount(0);
});

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
      const central = await startConsoleServer(["--totp", "--backup-codes", "200", "--fleet-node", `fleet-node=127.0.0.1:${new URL(node.url).port}`]);
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

withFleet("on a fleet, nothing in Settings says whose a section is: the sections are the central's, the group Fleet is gone and the tables of servers carry no server", async ({ page, central }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, central, "/settings/accounts");
  const list = page.getByRole("navigation", { name: "Settings sections" }).first();
  await expect(list.getByRole("link", { name: "Accounts" })).toBeVisible();
  await expect(page.getByText("All the fleet")).toHaveCount(0);
  await expect(page.getByText(/it does not depend on the server chosen above/)).toHaveCount(0);
  // Six groups: Fleet is gone, and what it held is in the sections of the kinds, or with Noust.
  const headings = await list.locator("p").allTextContents();
  expect(headings).toEqual(["People and access", "Notices", "Connections", "Compliance and audit", "API", "Noust"]);
  await expect(list.getByRole("link", { name: /^Central and sealing/ })).toBeVisible();
  await expect(list.getByRole("link", { name: /^Fleet settings/ })).toHaveCount(0);
  // The sections that are each server's own open the table of servers: their link carries no server.
  for (const name of [/^Integrations/, /^Logs and metrics export/, /^Secret managers/, /^Deploy keys/]) await expect(list.getByRole("link", { name })).not.toHaveAttribute("href", /\/n\//);
  await expect(page.getByRole("heading", { level: 1, name: "Accounts" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Settings on a fleet");
});
