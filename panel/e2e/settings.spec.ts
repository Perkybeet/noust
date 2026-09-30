/**
 * The server's settings (General, Notifications, Integrations, About) against the real
 * backend: every one is a T3 subsection that loads without anything still busy and passes
 * axe, and General is one form with one save bar - a value the server refuses comes back
 * beside its field in the server's own words while the rest of the save goes through.
 */

import type { Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";
import { confirmItsYou, stillness } from "./settings.helpers";

const PAGES = [
  { path: "/settings", heading: "This server", title: /^General settings/ },
  { path: "/settings/notifications", heading: "Channels", title: /^Notifications settings/ },
  { path: "/settings/integrations", heading: "GitHub", title: /^Integrations/ },
  { path: "/settings/about", heading: "Version", title: /^About/ },
] as const;

function saveBar(page: Page) {
  return page.getByRole("region", { name: "Unsaved changes" });
}

test("every server settings page loads its sections and passes axe", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/settings");
  const tabs = page.getByRole("navigation", { name: "Settings sections" });
  for (const entry of PAGES) {
    await page.goto(entry.path);
    await expect(page.getByRole("heading", { level: 2, name: entry.heading, exact: true })).toBeVisible();
    await expect(page).toHaveTitle(entry.title);
    await expect(tabs.locator('a[aria-current="page"]')).toHaveCount(1);
    // Nothing still loading when axe looks.
    await expect(page.locator("[aria-busy=true]")).toHaveCount(0);
    await settle(page);
    await expectNoA11yViolations(page, entry.path);
  }
});

test("General saves from one bar; a refused value stays beside its field, verbatim", async ({ page, consoleServer, problems }) => {
  // Saving configuration is sudo mode: the bar asks "Confirm it's you" before the first write.
  problems.expect(/status of 422 .* \/api\/config\/backup$/);
  problems.expect(/status of 400 .* \/api\/config\/apps-directory$/);
  await signIn(page, consoleServer, "/settings");
  const name = page.getByLabel("Server name");
  const retention = page.getByLabel("Backups kept per app");
  await expect(retention).toHaveValue(/^\d+$/);
  const original = await retention.inputValue();
  await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();
  await expect(saveBar(page).getByRole("button", { name: "Save" })).toBeDisabled();

  await name.fill("web-e2e");
  await retention.fill("500");
  await expect(saveBar(page).getByText("2 unsaved changes")).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "General with unsaved changes");
  await saveBar(page).getByRole("button", { name: "Save" }).click();

  await confirmItsYou(page, consoleServer);

  // The name was saved; the count was refused in pydantic's words, beside it.
  await expect(page.getByText(/less than or equal to 100/)).toBeVisible();
  await expect(retention).toHaveAttribute("aria-invalid", "true");
  await expect(retention).toHaveAccessibleDescription(/less than or equal to 100/);
  await expect(saveBar(page).getByText("1 unsaved change")).toBeVisible();
  const saved = (await (await page.request.get("/api/config")).json()) as { config: { server?: { name?: string } } };
  expect(saved.config.server?.name).toBe("web-e2e");
  await stillness(page);
  await expectNoA11yViolations(page, "a refused value");

  // A path the configuration's own rule refuses (a 400 with no field): beside its one field.
  const directory = page.getByLabel("Applications folder");
  const originalDirectory = await directory.inputValue();
  await directory.fill("relative/apps");
  await retention.fill("12");
  await saveBar(page).getByRole("button", { name: "Save" }).click();
  await expect(page.getByText(/apps_directory must be an absolute path/)).toBeVisible();
  await expect(directory).toHaveAttribute("aria-invalid", "true");
  await saveBar(page).getByRole("button", { name: "Discard" }).click();
  await expect(directory).toHaveValue(originalDirectory);
  await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();

  // The server holds what was saved.
  await page.reload();
  await expect(page.getByLabel("Backups kept per app")).toHaveValue("12");
  await expect(page.getByLabel("Server name")).toHaveValue("web-e2e");

  // Leave the worker's server as it was.
  await page.getByLabel("Backups kept per app").fill(original);
  await page.getByLabel("Server name").fill("");
  await saveBar(page).getByRole("button", { name: "Save" }).click();
  await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();
});

test("on a phone each subsection is its own page, with its way back", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, consoleServer, "/settings/notifications");
  await expect(page.getByRole("heading", { level: 2, name: "Channels", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "All sections" })).toBeVisible();
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(width).toBeLessThanOrEqual(390);
  await settle(page);
  await expectNoA11yViolations(page, "notifications on a phone");
});
