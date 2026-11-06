/**
 * Screenshots of the Settings area for review (@screens, left out of the default run): the hub, the people (accounts,
 * single sign-on and its first step and form, access security, roles), Compliance with nothing on, its question and with a
 * framework on, the central and its seal, and the notification channels with their question and silenced; at 1440, at 1920
 * and at 390, in the project's theme, on a fleet (so "All the fleet" is drawn the way the column says it).
 *
 *   NOUST_SCREENS=1 NOUST_SCREENS_DIR=/tmp/ajustes-screens npx playwright test settings-screens --workers=1
 *
 * It runs on a central of its own with one server beside it and three accounts, and puts back what it turns on. The pages
 * still pass the CSP and console gates of every test.
 */

import type { Page } from "@playwright/test";
import { appendFileSync } from "node:fs";
import path from "node:path";

import { csrf } from "./apps-cleanup";
import { expect, settle, signIn, startConsoleServer, test } from "./fixtures";
import type { ConsoleServer, RunningServer } from "./fixtures";

const OUT = process.env.NOUST_SCREENS_DIR ?? path.join(import.meta.dirname, "__screens__", "settings");
const SIZES = [
  { name: "1440", width: 1440, height: 900 },
  { name: "1920", width: 1920, height: 1080 },
  { name: "390", width: 390, height: 844 },
] as const;
const PASSWORD = "correct-horse-battery-staple-1";

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
      const central = await startConsoleServer(["--totp", "--backup-codes", "300", "--fleet-node", `fleet-node=127.0.0.1:${new URL(node.url).port}`]);
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

async function shot(page: Page, theme: string, size: string, name: string): Promise<void> {
  await settle(page);
  await page.screenshot({ path: path.join(OUT, `${name}-${theme}-${size}.png`) });
  // The words on view, to hold each screen to the batch's 400: what the person can read, the frame around the page included.
  const words = await page.evaluate(() => (document.body.innerText.match(/\S+/g) ?? []).length);
  appendFileSync(path.join(OUT, "words.txt"), `${String(words).padStart(5)} ${name}-${theme}-${size}\n`);
}

async function elevate(page: Page, server: ConsoleServer): Promise<void> {
  const answer = await page.request.post("/api/auth/elevate", { data: { code: server.secondFactor() }, headers: await csrf(page) });
  expect(answer.ok()).toBe(true);
}

for (const size of SIZES) {
  withFleet(`@screens the settings at ${size.name}`, async ({ page, central }, testInfo) => {
    test.setTimeout(420_000);
    const theme = testInfo.project.name;
    await page.setViewportSize({ width: size.width, height: size.height });

    await signIn(page, central, "/settings");
    await elevate(page, central);
    // The people of the accounts page: an administrator, an operator, and a viewer with no second factor.
    for (const [username, role] of [
      ["yago", "admin"],
      ["marta", "operator"],
      ["luis", "viewer"],
    ] as const) {
      const made = await page.request.post("/api/auth/accounts", { data: { username, role, password: PASSWORD }, headers: await csrf(page) });
      // The same accounts are made at each size: a second pass finds them there already.
      if (!made.ok()) expect([400, 409]).toContain(made.status());
    }

    await page.goto("/settings");
    await expect(page.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible();
    await shot(page, theme, size.name, "89-hub");

    await page.goto("/settings/accounts");
    await expect(page.getByRole("heading", { level: 1, name: "Accounts" })).toBeVisible();
    await expect(page.getByText("marta").first()).toBeVisible();
    await shot(page, theme, size.name, "90-accounts");

    await page.goto("/settings/sso");
    await expect(page.getByRole("heading", { level: 1, name: "Single sign-on" })).toBeVisible();
    await expect(page.getByText(/Connect the company's Microsoft or Google account/)).toBeVisible();
    await shot(page, theme, size.name, "91-sso");
    await page.getByRole("button", { name: "Add a provider" }).click();
    await expect(page.getByRole("dialog", { name: "Add a provider" }).getByRole("radio", { name: "Microsoft Entra ID" })).toBeChecked();
    await shot(page, theme, size.name, "92-sso-add");
    await page.getByRole("dialog", { name: "Add a provider" }).getByRole("button", { name: "Continue" }).click();
    await expect(page.getByRole("dialog", { name: "Add an identity provider" }).getByLabel("Button label")).toHaveValue("Microsoft Entra ID");
    await shot(page, theme, size.name, "92-sso-form");
    await page.keyboard.press("Escape");

    await page.goto("/settings/access-security");
    await expect(page.getByRole("heading", { level: 2, name: "At the door" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Sessions" }).getByText(/session/).first()).toBeVisible();
    await shot(page, theme, size.name, "93-access-security");

    await page.goto("/settings/compliance");
    await expect(page.getByRole("heading", { level: 2, name: "No framework is on" })).toBeVisible();
    await shot(page, theme, size.name, "94-compliance");
    const card = page.locator("article").filter({ has: page.getByRole("heading", { level: 3, name: "NIS2" }) });
    await card.getByRole("button", { name: "Turn on…" }).click();
    await expect(page.getByRole("alertdialog", { name: "Turn on NIS2" }).getByText("Encrypted backups")).toBeVisible();
    await shot(page, theme, size.name, "95-compliance-turn-on");
    await page.getByRole("alertdialog", { name: "Turn on NIS2" }).getByRole("button", { name: "Turn on" }).click();
    const report = page.locator("section").filter({ has: page.getByRole("heading", { level: 2, name: "NIS2" }) }).last();
    await expect(report.getByText(/controls in force|The control is in force/)).toBeVisible();
    // The card that was pressed sat below the fold: the picture is of the report, from its title.
    await page.getByRole("heading", { level: 1, name: "Compliance" }).scrollIntoViewIfNeeded();
    await shot(page, theme, size.name, "96-compliance-on");
    // Put back as it was, so the next size starts from nothing on.
    await report.getByRole("button", { name: "Turn off…" }).click();
    // The window of sudo mode was opened at the start: turning it off asks nothing more.
    await page.getByRole("alertdialog", { name: "Turn off NIS2" }).getByRole("button", { name: "Turn off" }).click();
    await expect(page.getByRole("heading", { level: 2, name: "No framework is on" })).toBeVisible();

    await page.goto("/settings/central");
    await expect(page.getByRole("heading", { level: 1, name: "Central and sealing" })).toBeVisible();
    await expect(page.getByRole("heading", { level: 2, name: "Sealed secrets" })).toBeVisible();
    await shot(page, theme, size.name, "97-central");

    await page.goto("/settings/notifications");
    await expect(page.getByRole("heading", { level: 1, name: "Notification channels" })).toBeVisible();
    const master = page.getByRole("switch", { name: "Send notifications" });
    if (!(await master.isChecked())) await master.click();
    await expect(master).toBeChecked();
    await settle(page);
    await shot(page, theme, size.name, "98-channels");
    await master.click();
    await expect(page.getByRole("dialog", { name: "Silence the notifications until when?" })).toBeVisible();
    await shot(page, theme, size.name, "98-channels-silence");
    await page.getByRole("dialog", { name: "Silence the notifications until when?" }).getByRole("button", { name: "Silence" }).click();
    await expect(page.getByText(/^Silenced until /)).toBeVisible();
    await shot(page, theme, size.name, "98-channels-silenced");
    await page.getByRole("button", { name: "Turn on now" }).click();
    await expect(master).toBeChecked();
    await settle(page);
    await page.goto("/alerts");
    await expect(page.getByRole("button", { name: "Silence notifications…" })).toBeVisible();
    await shot(page, theme, size.name, "98-alerts");
    // The server starts every size with them off, as it was found.
    await page.goto("/settings/notifications");
    await page.getByRole("switch", { name: "Send notifications" }).click();
    const again = page.getByRole("dialog", { name: "Silence the notifications until when?" });
    await again.getByRole("radio", { name: /Until I turn them on/ }).check();
    await again.getByRole("button", { name: "Silence" }).click();
    await expect(again).toBeHidden();

    await page.goto("/settings/access");
    await expect(page.getByRole("heading", { level: 1, name: "Roles" })).toBeVisible();
    // A phone draws the table as cards, which are not a region: the slab's own heading is what to wait for.
    await expect(page.getByRole("heading", { level: 2, name: "What each role allows" })).toBeVisible();
    await expect(page.getByText("Owner").first()).toBeVisible();
    await shot(page, theme, size.name, "99-roles");

    await page.goto("/settings/sign-in-image");
    await expect(page.getByRole("heading", { level: 1, name: "Sign-in image" })).toBeVisible();
    await shot(page, theme, size.name, "99-sign-in-image");
  });
}
