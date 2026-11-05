/**
 * Screenshots of the Databases area for review (@screens, left out of the default run): the list, the dialog that makes a
 * database, the engines, the upgrade, a database's pages one by one, a database whose engine is stopped, and the two
 * pages that are new (Settings and Delete); at 1440, at 1920 and at 390, in the project's theme.
 *
 *   NOUST_SCREENS=1 NOUST_SCREENS_DIR=/tmp/bases-screens npx playwright test databases-screens --workers=1
 *
 * It runs on the suite's own console server, over the seeded machine: the Redis of that machine is stopped, which is the
 * state the stopped-engine pictures are of. The pages still pass the CSP and console gates of every test.
 */

import type { Page } from "@playwright/test";
import { appendFileSync } from "node:fs";
import path from "node:path";

import { expect, settle, signIn, test } from "./fixtures";

const OUT = process.env.NOUST_SCREENS_DIR ?? path.join(import.meta.dirname, "__screens__", "databases");
const SIZES = [
  { name: "1440", width: 1440, height: 900 },
  { name: "1920", width: 1920, height: 1080 },
  { name: "390", width: 390, height: 844 },
] as const;
const DATABASE = "/databases/postgresql/example_production";

async function shot(page: Page, theme: string, size: string, name: string): Promise<void> {
  await settle(page);
  await page.screenshot({ path: path.join(OUT, `${name}-${theme}-${size}.png`) });
  // The words on view, to hold each screen to the batch's 400: what the person can read, the frame around the page included.
  const words = await page.evaluate(() => (document.body.innerText.match(/\S+/g) ?? []).length);
  appendFileSync(path.join(OUT, "words.txt"), `${String(words).padStart(5)} ${name}-${theme}-${size}\n`);
}

for (const size of SIZES) {
  test(`@screens the databases at ${size.name}`, async ({ page, consoleServer, problems }, testInfo) => {
    test.setTimeout(240_000);
    // Once its engine runs, the seeded Redis database is read by a name Redis does not take: the engine's refusal is the page's.
    problems.expect(/status of 500 .*\/api\/databases\/databases\/redis\/sessions\//);
    const theme = testInfo.project.name;
    await page.setViewportSize({ width: size.width, height: size.height });

    await signIn(page, consoleServer, "/databases");
    // A phone draws the list as cards, not as a table region: the page's heading and its first row are what to wait for.
    await expect(page.getByRole("heading", { level: 1, name: "Databases" })).toBeVisible();
    await expect(page.getByText("example_production").first()).toBeVisible();
    await shot(page, theme, size.name, "40-list");

    await page.getByRole("button", { name: "New database" }).click();
    await expect(page.getByRole("dialog", { name: "New database" }).getByRole("radio", { name: /PostgreSQL 16/ })).toBeChecked();
    await shot(page, theme, size.name, "41-new");
    await page.getByRole("dialog", { name: "New database" }).getByRole("radio", { name: "From a dump" }).click();
    await shot(page, theme, size.name, "41-new-dump");
    await page.keyboard.press("Escape");

    await page.goto("/databases/engines");
    await expect(page.getByRole("list", { name: "Engines" })).toBeVisible();
    await shot(page, theme, size.name, "42-engines");
    await page.getByRole("button", { name: "Add an engine" }).click();
    await page.getByRole("dialog", { name: "Add an engine" }).getByRole("radio", { name: /In a container/ }).click();
    await shot(page, theme, size.name, "42-engines-add");
    await page.keyboard.press("Escape");

    await page.goto("/databases/mysql/shop_wp");
    await expect(page.getByRole("region", { name: "Engine" }).getByRole("button", { name: "Upgrade version" })).toBeVisible();
    await shot(page, theme, size.name, "43-old-engine");
    await page.getByRole("region", { name: "Engine" }).getByRole("button", { name: "Upgrade version" }).click();
    await expect(page.getByRole("dialog", { name: "Upgrade MySQL 8.0" })).toBeVisible();
    await shot(page, theme, size.name, "43-upgrade");
    await page.keyboard.press("Escape");

    await page.goto(DATABASE);
    await expect(page.getByRole("region", { name: "Engine" })).toBeVisible();
    await shot(page, theme, size.name, "44-overview");

    await page.goto(`${DATABASE}/query`);
    await expect(page.getByRole("textbox", { name: /Statement to run/ })).toBeVisible();
    await shot(page, theme, size.name, "45-query");

    await page.goto(`${DATABASE}/metrics`);
    await expect(page.getByRole("heading", { level: 3, name: "Connections" })).toBeVisible();
    await shot(page, theme, size.name, "46-metrics");

    await page.goto(`${DATABASE}/access`);
    // A phone draws these tables as cards, which are not regions: the page's own heading is what to wait for.
    await expect(page.getByRole("heading", { level: 1, name: "Access" })).toBeVisible();
    await expect(page.getByText("analytics_reader").first()).toBeVisible();
    await shot(page, theme, size.name, "50-access");

    await page.goto(`${DATABASE}/connect`);
    await expect(page.getByRole("heading", { level: 2, name: "From your computer" })).toBeVisible();
    await shot(page, theme, size.name, "49-connect");

    await page.goto(`${DATABASE}/backups`);
    await expect(page.getByRole("heading", { level: 1, name: "Backups" })).toBeVisible();
    await shot(page, theme, size.name, "51-backups");

    await page.goto(`${DATABASE}/settings`);
    await expect(page.getByRole("heading", { level: 2, name: "This database" })).toBeVisible();
    await shot(page, theme, size.name, "52-settings");

    await page.goto(`${DATABASE}/delete`);
    await expect(page.getByRole("heading", { level: 1, name: "Delete the database" })).toBeVisible();
    await shot(page, theme, size.name, "53-delete");

    // The engine that is stopped: the database says so, and what it needs is not asked of it.
    await page.goto("/databases/redis/sessions");
    await expect(page.getByLabel("Engine stopped")).toBeVisible();
    await shot(page, theme, size.name, "48-stopped");
    await page.goto("/databases/redis/sessions/data");
    await expect(page.getByLabel("Engine stopped")).toBeVisible();
    await shot(page, theme, size.name, "47-stopped-keys");
  });
}
