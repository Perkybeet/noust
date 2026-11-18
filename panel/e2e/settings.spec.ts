/**
 * The server's settings (General, Notifications, Integrations, About) against the real
 * backend: every one is a T3 subsection that loads without anything still busy and passes
 * axe, and what was General is three forms with a save bar each - the policy in Settings,
 * the folders and builds and the machine's name in Server (item 166) - where a value the
 * server refuses comes back beside its field in the server's own words while the rest of
 * the save goes through.
 */

import type { Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";
import { confirmItsYou, stillness } from "./settings.helpers";

const PAGES = [
  { path: "/settings/general", heading: "Policy", title: /^General settings/ },
  // Nothing recorded yet: the first time is one sentence in a region named by what the page holds, not a heading.
  { path: "/settings/registries", region: "Accounts", title: /^Image registries/ },
  { path: "/settings/notifications", heading: "Channels", title: /^Notifications settings/ },
  { path: "/settings/integrations", heading: "GitHub", title: /^Integrations/ },
  { path: "/settings/variables", heading: "Server variables", title: /^Server variables settings/ },
  { path: "/settings/secret-managers", heading: "Secret managers", title: /^Secret managers settings/ },
  { path: "/settings/about", heading: "Version and updates", title: /^About/ },
] as const;

function saveBar(page: Page) {
  return page.getByRole("region", { name: "Unsaved changes" });
}

test("every server settings page loads its sections and passes axe", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/settings/general");
  const tabs = page.getByRole("navigation", { name: "Settings sections" });
  for (const entry of PAGES) {
    await page.goto(entry.path);
    await expect(
      "region" in entry ? page.getByRole("region", { name: entry.region, exact: true }) : page.getByRole("heading", { level: 2, name: entry.heading, exact: true }),
    ).toBeVisible();
    await expect(page).toHaveTitle(entry.title);
    await expect(tabs.locator('a[aria-current="page"]')).toHaveCount(1);
    // Nothing still loading when axe looks.
    await expect(page.locator("[aria-busy=true]")).toHaveCount(0);
    await settle(page);
    await expectNoA11yViolations(page, entry.path);
  }
});

test("About says a security release is out, with what it fixes, and passes axe in a real browser", async ({ page, consoleServer }) => {
  // The seeded server cannot reach GitHub, so the answer of the version check is the one thing
  // faked: what the console does with it is what is measured (CSP included).
  await page.route("**/api/system/version", async (route) => {
    await route.fulfill({
      json: {
        current_version: "3.3.1",
        latest_version: "3.3.2",
        has_update: true,
        update_state: "update_available",
        update_command: "sudo apt update && sudo apt install --only-upgrade noust",
        release_url: "https://github.com/Perkybeet/noust/releases/tag/v3.3.2",
        security_update: true,
        security_version: "3.3.2",
        advisory_url: "https://github.com/Perkybeet/noust/security/advisories/GHSA-abcd-1234-wxyz",
        status: "checked",
      },
    });
  });
  await signIn(page, consoleServer, "/settings/about");
  const version = page.getByRole("region", { name: "Version" });
  await expect(version.getByText("Version 3.3.2 fixes a security vulnerability")).toBeVisible();
  await expect(version.getByRole("link", { name: /What 3.3.2 fixes/ })).toHaveAttribute(
    "href",
    "https://github.com/Perkybeet/noust/security/advisories/GHSA-abcd-1234-wxyz",
  );
  await expect(version.getByText("Version 3.3.2 can be installed")).toBeVisible();
  await expect(page.locator("[aria-busy=true]")).toHaveCount(0);
  await settle(page);
  await expectNoA11yViolations(page, "About with a security release");
});

test("General saves from one bar; a refused value stays beside its field, verbatim", async ({ page, consoleServer, problems }) => {
  // Saving configuration is sudo mode: the bar asks "Confirm it's you" before the first write.
  problems.expect(/status of 422 .* \/api\/config\/backup$/);
  await signIn(page, consoleServer, "/settings/general");
  const email = page.getByLabel("Email for certificates");
  const retention = page.getByLabel("Backups kept per app");
  await expect(retention).toHaveValue(/^\d+$/);
  const original = await retention.inputValue();
  const originalEmail = await email.inputValue();
  await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();
  await expect(saveBar(page).getByRole("button", { name: "Save" })).toBeDisabled();

  await email.fill("ops-e2e@example.org");
  await retention.fill("500");
  await expect(saveBar(page).getByText("2 unsaved changes")).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "General with unsaved changes");
  await saveBar(page).getByRole("button", { name: "Save" }).click();

  await confirmItsYou(page, consoleServer);

  // The email was saved; the count was refused in pydantic's words, beside it.
  await expect(page.getByText(/less than or equal to 100/)).toBeVisible();
  await expect(retention).toHaveAttribute("aria-invalid", "true");
  await expect(retention).toHaveAccessibleDescription(/less than or equal to 100/);
  await expect(saveBar(page).getByText("1 unsaved change")).toBeVisible();
  const saved = (await (await page.request.get("/api/config/ssl")).json()) as { email: string };
  expect(saved.email).toBe("ops-e2e@example.org");
  await stillness(page);
  await expectNoA11yViolations(page, "a refused value");

  // A value the server accepts goes through the same bar, and is what the server holds after a reload.
  await retention.fill("12");
  await saveBar(page).getByRole("button", { name: "Save" }).click();
  await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Backups kept per app")).toHaveValue("12");
  await expect(page.getByLabel("Email for certificates")).toHaveValue("ops-e2e@example.org");

  // Leave the worker's server as it was.
  await page.getByLabel("Backups kept per app").fill(original);
  await page.getByLabel("Email for certificates").fill(originalEmail);
  await saveBar(page).getByRole("button", { name: "Save" }).click();
  await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();
});

test("Folders and builds saves from one bar; a path the configuration refuses stays beside its field while the rest goes through", async ({ page, consoleServer, problems }) => {
  // Saving configuration is sudo mode, and a path that is not absolute is a 400 by the configuration's own rule.
  problems.expect(/status of 400 .* \/api\/config\/apps-directory$/);
  await signIn(page, consoleServer, "/server/folders");
  const directory = page.getByLabel("Applications folder");
  const waiting = page.getByLabel("Deployments that may wait");
  await expect(waiting).toHaveValue(/^\d+$/);
  const originalDirectory = await directory.inputValue();
  const original = await waiting.inputValue();
  await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();

  await directory.fill("relative/apps");
  await waiting.fill(String(Number(original) + 1));
  await expect(saveBar(page).getByText("2 unsaved changes")).toBeVisible();
  await saveBar(page).getByRole("button", { name: "Save" }).click();
  await confirmItsYou(page, consoleServer);
  // The refusal has no field of its own in the server's answer: it is shown beside the one field it is about.
  await expect(page.getByText(/apps_directory must be an absolute path/)).toBeVisible();
  await expect(directory).toHaveAttribute("aria-invalid", "true");
  await stillness(page);
  await expectNoA11yViolations(page, "a refused path");
  await saveBar(page).getByRole("button", { name: "Discard" }).click();
  await expect(directory).toHaveValue(originalDirectory);
  await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();

  // The server holds what was saved: the number that was accepted in the same save.
  await page.reload();
  await expect(page.getByLabel("Deployments that may wait")).toHaveValue(String(Number(original) + 1));

  // Leave the worker's server as it was.
  await page.getByLabel("Deployments that may wait").fill(original);
  await saveBar(page).getByRole("button", { name: "Save" }).click();
  await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();
});

test("Machine saves the server's name from its bar, and the server holds it", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/server/machine");
  const name = page.getByLabel("Server name");
  await expect(name).toBeVisible();
  await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();

  await name.fill("web-e2e");
  await expect(saveBar(page).getByText("1 unsaved change")).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "Machine with an unsaved name");
  await saveBar(page).getByRole("button", { name: "Save" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();
  const saved = (await (await page.request.get("/api/config")).json()) as { config: { server?: { name?: string } } };
  expect(saved.config.server?.name).toBe("web-e2e");

  await page.reload();
  await expect(page.getByLabel("Server name")).toHaveValue("web-e2e");

  // Leave the worker's server as it was.
  await page.getByLabel("Server name").fill("");
  await saveBar(page).getByRole("button", { name: "Save" }).click();
  await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();
});

test("on a phone each subsection is its own page, with its way back", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, consoleServer, "/settings/notifications");
  await expect(page.getByRole("heading", { level: 2, name: "Channels", exact: true })).toBeVisible();
  // The way back is the selector at the top of the page (4.2): it says the section and opens the list of every one.
  await expect(page.getByRole("main").getByRole("button", { name: /Notification channels/ })).toBeVisible();
  // The whole page, not what has drawn so far: the widest thing on it (the language of the messages)
  // is far below the channels and arrives with them. Measured as soon as the way back showed, the
  // width was 390 on a fast machine and 409 on a slow one, for the same page.
  await expect(page.getByRole("radiogroup", { name: "Language of notifications" })).toBeVisible();
  await settle(page);
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(width).toBeLessThanOrEqual(390);
  await expectNoA11yViolations(page, "notifications on a phone");
});
