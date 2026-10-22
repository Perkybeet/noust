/**
 * The 3.4 monitoring pages against the real backend: one search over the logs of every
 * application, how long the journal keeps them, the probes that ask an address from outside the
 * application and the public status page they make, and taking an alert that is firing.
 *
 * Every page is also swept by axe in both themes, the layout-shift gate and the Spanish pass
 * through `routes.ts`; this holds what is said and done on them. What a spec changes it puts back.
 */

import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

import { confirmItsYou, expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

/**
 * Axe on the open drawer alone. The page behind a modal is out of reach of everyone (it is inert),
 * but axe still measures the contrast of its inputs through the backdrop, which says nothing about
 * what the drawer shows.
 */
async function expectNoDrawerViolations(page: Page, label: string): Promise<void> {
  const results = await new AxeBuilder({ page })
    .include('[role="dialog"]')
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22a", "wcag22aa"])
    .analyze();
  const report = results.violations.map((violation) => `${violation.id}: ${violation.nodes.map((node) => node.target.join(" ")).join(", ")}`);
  expect(report, `axe found WCAG 2.2 AA violations on ${label}`).toEqual([]);
}

function section(page: Page, title: string) {
  return page.getByRole("heading", { level: 2, name: title }).locator("xpath=ancestor::section[1]");
}

test("the logs of every application are searched from one page, and the question stays in the address", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/logs");
  await expect(page.getByRole("heading", { level: 1, name: "Logs" })).toBeVisible();
  // The answer says how much it looked at, whatever it found.
  const summary = page.getByRole("status").filter({ hasText: /examined/ });
  await expect(summary).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the logs page");

  await page.getByRole("searchbox", { name: "Search the logs" }).fill("zzz-nothing-says-this");
  await expect(page).toHaveURL(/q=zzz-nothing-says-this/);
  await expect(page.getByText("No line matches this search.")).toBeVisible();
  await expect(page.getByText("noust log search zzz-nothing-says-this --since 1h")).toBeVisible();

  await page.getByRole("combobox", { name: "How far back" }).click();
  await page.getByRole("option", { name: "24 hours" }).click();
  await expect(page).toHaveURL(/since=24h/);
  await page.getByRole("button", { name: "Clear the search" }).click();
  await expect(page).not.toHaveURL(/q=/);
  await expect(page.getByRole("searchbox", { name: "Search the logs" })).toHaveValue("");
});

test("how long the journal keeps the logs is set from the page, in its own drawer, and goes back to the defaults", async ({
  page,
  consoleServer,
  problems,
}) => {
  // Changing it restarts journald: each write asks "Confirm it's you" first, by design.
  problems.expect(/status of 403 .* \/api\/logs\/retention$/);
  problems.expect(/status of 422 .* \/api\/logs\/retention$/);
  await signIn(page, consoleServer, "/settings/log-shipping");
  const keeping = section(page, "Keeping the logs");
  await expect(keeping.getByText("Nothing is set: journald keeps entries by its own defaults.")).toBeVisible();

  await keeping.getByRole("button", { name: "Change" }).click();
  const drawer = page.getByRole("dialog", { name: "Keep the logs" });
  await drawer.getByLabel("Days to keep").fill("0");
  await drawer.getByRole("button", { name: "Keep the logs" }).click();
  await confirmItsYou(page, consoleServer);
  // The server's own refusal, beside the field it is about.
  await expect(drawer.getByLabel("Days to keep")).toHaveAttribute("aria-invalid", "true");
  await drawer.getByLabel("Days to keep").fill("14");
  await drawer.getByLabel("Most disk to use (MB)").fill("512");
  await settle(page);
  await expectNoDrawerViolations(page, "the journal retention drawer");
  await drawer.getByRole("button", { name: "Keep the logs" }).click();
  await expect(drawer).toBeHidden();
  await expect(keeping.getByText("Entries are kept for 14 days, using at most 512 MB.")).toBeVisible();

  await page.reload();
  await expect(section(page, "Keeping the logs").getByText("Entries are kept for 14 days, using at most 512 MB.")).toBeVisible();

  await section(page, "Keeping the logs").getByRole("button", { name: "Back to the defaults" }).click();
  await page.getByRole("alertdialog", { name: "Go back to journald's defaults?" }).getByRole("button", { name: "Back to the defaults" }).click();
  await expect(section(page, "Keeping the logs").getByText("Nothing is set: journald keeps entries by its own defaults.")).toBeVisible();
});

test("a probe is added, its page is kept up to date, and it goes when it is removed", async ({ page, consoleServer, problems }) => {
  problems.expect(/status of 403 .* \/api\/probes\/status-page$/);
  problems.expect(/status of (400|422) .* \/api\/probes\/shop$/);
  await signIn(page, consoleServer, "/settings/probes");
  await expect(page.getByText("No probes yet")).toBeVisible();
  await expect(page.getByRole("button", { name: "New probe" })).toHaveCount(1);

  await page.getByRole("button", { name: "New probe" }).first().click();
  const drawer = page.getByRole("dialog", { name: "New probe" });
  await drawer.getByLabel("Name").fill("shop");
  // An address with a password in it: the server's refusal lands beside the field.
  await drawer.getByLabel("Address").fill("https://user:secret@shop.example.com/health");
  await drawer.getByRole("button", { name: "Save probe" }).click();
  await expect(drawer.getByLabel("Address")).toHaveAttribute("aria-invalid", "true");
  await drawer.getByLabel("Address").fill("https://shop.example.com/health");
  await drawer.getByRole("checkbox", { name: "Show it on the status page" }).click();
  await drawer.getByLabel(/^Name on the page/).fill("Shop");
  await settle(page);
  await expectNoDrawerViolations(page, "the new probe drawer");
  await drawer.getByRole("button", { name: "Save probe" }).click();
  await expect(drawer).toBeHidden();

  const row = page.getByRole("list", { name: "Probes" }).getByRole("listitem").filter({ hasText: /^shop/ });
  await expect(row.getByText("Waiting")).toBeVisible();
  await expect(row.getByText("https://shop.example.com/health", { exact: true })).toBeVisible();
  await expect(row.getByText("On the status page")).toBeVisible();
  await row.getByRole("button", { name: "Incidents" }).click();
  await expect(row.getByText("It has never stopped answering.")).toBeVisible();

  // The page it is a component of: kept up to date, with the files it wrote.
  const page_ = section(page, "Status page");
  await expect(page_.getByText(/Written to .*: 1 component\./)).toBeVisible();
  await expect(page_.getByText("It has no name of its own: serve the folder above yourself, or give it one.")).toBeVisible();
  await page_.getByRole("switch", { name: "Keep the page's files up to date" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(page_.getByRole("switch", { name: "Keep the page's files up to date" })).toBeChecked();
  await page_.getByRole("button", { name: "Write now" }).click();
  await expect(page.locator(".toast").filter({ hasText: "Wrote 2 files" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the probes page with a probe and its status page");

  // Everything back as it was.
  await page_.getByRole("switch", { name: "Keep the page's files up to date" }).click();
  await expect(page_.getByRole("switch", { name: "Keep the page's files up to date" })).not.toBeChecked();
  await row.getByRole("button", { name: "Actions for shop" }).click();
  await page.getByRole("menuitem", { name: "Remove" }).click();
  await page.getByRole("alertdialog", { name: "Remove shop?" }).getByRole("button", { name: "Remove" }).click();
  await expect(page.getByText("No probes yet")).toBeVisible();
});

test("the traffic and the containers of an application are on its Metrics tab only when there is something to say", async ({
  page,
  consoleServer,
}) => {
  await signIn(page, consoleServer, "/apps/tienda.example.org/metrics");
  await expect(page.getByRole("heading", { level: 1, name: "tienda.example.org" })).toBeVisible();
  await settle(page);
  // A process application with no access log being read has no traffic section, and no containers.
  await expect(page.getByRole("heading", { name: "By container" })).toHaveCount(0);
  await expectNoA11yViolations(page, "the metrics tab");
});
