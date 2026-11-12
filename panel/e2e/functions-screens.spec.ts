/**
 * Screenshots of the three small functions of 4.3 for review (item 164): the scheduled tasks with a task that runs
 * once and what ran once, the dialog that makes one, the output of one that failed, the dialog that changes where a
 * firewall rule admits from, and the Audit page with its export menu open and a filter on. Tagged @screens, so left
 * out of the default run; write them with
 *
 *     NOUST_SCREENS=1 npx playwright test e2e/functions-screens.spec.ts
 *
 * into $NOUST_FUNCTIONS_SCREENS (default e2e/__screens__/functions) under the theme's name, at 1440x900 and 390x844.
 */

import type { Page } from "@playwright/test";
import path from "node:path";

import { expect, settle, signIn, test } from "./fixtures";

const OUT = process.env.NOUST_FUNCTIONS_SCREENS ?? path.join(import.meta.dirname, "__screens__", "functions");

const SIZES = [
  { suffix: "1440", size: { width: 1440, height: 900 } },
  { suffix: "390", size: { width: 390, height: 844 } },
] as const;

interface Screen {
  name: string;
  path: string;
  /** What to do once the page is up: open what the screenshot is about. */
  ready?: (page: Page) => Promise<void>;
}

const SCREENS: readonly Screen[] = [
  {
    name: "cron-list",
    path: "/cron",
    ready: async (page) => {
      await expect(page.getByRole("region", { name: "Ran once" })).toBeVisible();
    },
  },
  {
    name: "cron-once-dialog",
    path: "/cron",
    ready: async (page) => {
      await page.getByRole("button", { name: "New task" }).first().click();
      const dialog = page.getByRole("dialog", { name: "New scheduled task" });
      await dialog.getByLabel("Name", { exact: true }).fill("reindex-search");
      await dialog.getByLabel("Command", { exact: true }).fill("/usr/bin/node scripts/reindex.js");
      await dialog.getByRole("radio", { name: "Once" }).check();
      await expect(dialog.getByText("Runs once, then retires itself")).toBeVisible();
    },
  },
  {
    name: "cron-once-passed",
    path: "/cron",
    ready: async (page) => {
      await page.getByRole("button", { name: "New task" }).first().click();
      const dialog = page.getByRole("dialog", { name: "New scheduled task" });
      await dialog.getByLabel("Name", { exact: true }).fill("reindex-search");
      await dialog.getByLabel("Command", { exact: true }).fill("/usr/bin/node scripts/reindex.js");
      await dialog.getByRole("radio", { name: "Once" }).check();
      await dialog.getByLabel("Date").fill("2026-01-05");
      await expect(dialog.getByText(/has already passed on this server/)).toBeVisible();
    },
  },
  {
    name: "cron-once-output",
    path: "/cron",
    ready: async (page) => {
      await page.getByRole("region", { name: "Ran once" }).getByText("purge-old-exports").click();
      await expect(page.getByRole("dialog", { name: "What purge-old-exports printed" })).toBeVisible();
    },
  },
  {
    name: "firewall-menu",
    path: "/network/firewall",
    ready: async (page) => {
      const rules = page.getByRole("table", { name: /rules/i }).first();
      await rules.getByRole("row").filter({ hasText: "temporary: migration from the old host" }).getByRole("button", { name: /^Actions for/ }).click();
      await expect(page.getByRole("menuitem", { name: "Change where it admits from" })).toBeVisible();
    },
  },
  {
    name: "firewall-change-source",
    path: "/network/firewall",
    ready: async (page) => {
      const rules = page.getByRole("table", { name: /rules/i }).first();
      await rules.getByRole("row").filter({ hasText: "temporary: migration from the old host" }).getByRole("button", { name: /^Actions for/ }).click();
      await page.getByRole("menuitem", { name: "Change where it admits from" }).click();
      const dialog = page.getByRole("alertdialog", { name: "Change where 5432/tcp admits from" });
      await dialog.getByRole("textbox", { name: "Address or network" }).fill("203.0.113.0/24");
      await expect(dialog.getByText("Anywhere")).toBeVisible();
    },
  },
  {
    name: "firewall-change-source-ssh",
    path: "/network/firewall",
    ready: async (page) => {
      const rules = page.getByRole("table", { name: /rules/i }).first();
      await rules.getByRole("row").filter({ hasText: "22" }).first().getByRole("button", { name: /^Actions for/ }).click();
      await page.getByRole("menuitem", { name: "Change where it admits from" }).click();
      await expect(page.getByRole("alertdialog", { name: /Change where 22\/tcp admits from/ })).toBeVisible();
    },
  },
  {
    name: "audit-export-menu",
    path: "/settings/audit?category=access&result=failure",
    ready: async (page) => {
      await page.getByRole("button", { name: "Export" }).click();
      await expect(page.getByRole("menuitem", { name: /CSV, for a spreadsheet/ })).toBeVisible();
    },
  },
];

test.describe("the three small functions @screens", () => {
  for (const screen of SCREENS) {
    test(`functions ${screen.name}`, async ({ page, consoleServer, problems }, testInfo) => {
      // The moment that has passed is refused by the server, which the browser logs as a failed request.
      problems.expect(/status of 400 .*\/api\/cron\/preview$/);
      const dir = path.join(OUT, testInfo.project.name);
      await page.setViewportSize(SIZES[0].size);
      await signIn(page, consoleServer, screen.path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await screen.ready?.(page);
      await settle(page);
      await page.waitForTimeout(500);
      await page.screenshot({ path: path.join(dir, `${screen.name}-${SIZES[0].suffix}.png`) });

      await page.setViewportSize(SIZES[1].size);
      await settle(page);
      await page.waitForTimeout(400);
      await page.screenshot({ path: path.join(dir, `${screen.name}-${SIZES[1].suffix}.png`) });
    });
  }
});
