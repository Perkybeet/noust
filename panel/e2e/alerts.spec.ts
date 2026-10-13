/**
 * Settings > Alerts against the real backend: a rule is written in a drawer in two clicks, the
 * server's refusal of a limit lands beside the field in its own words, a rule can be switched
 * off and removed, and the monitor's built-in checks are one form saved from the page's bar.
 *
 * The rules and the checks are written into the server, so this runs on a console server of its
 * own, in both themes' worth of axe on the page that holds them.
 */

import type { Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, signIn, startConsoleServer, test as base } from "./fixtures";
import type { ConsoleServer } from "./fixtures";
import { confirmItsYou, stillness } from "./settings.helpers";

const test = base.extend<object, { consoleServer: ConsoleServer }>({
  consoleServer: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const server = await startConsoleServer([]);
      try {
        await use(server);
      } finally {
        await server.stop();
      }
    },
    { scope: "worker", timeout: 75_000 },
  ],
});

function row(page: Page, name: string) {
  return page.getByRole("list", { name: "Alert rules" }).getByRole("listitem").filter({ hasText: new RegExp(`^${name}`) });
}

test("a rule is two clicks from the list, says what it watches, and goes when it is removed", async ({ page, consoleServer, problems }) => {
  problems.expect(/status of 400 .* \/api\/alerts\/rules\/disk-full$/);
  await signIn(page, consoleServer, "/settings/alerts");
  await expect(page.getByText("No alert rules yet")).toBeVisible();
  await expect(page.getByText("No alert is firing.")).toBeVisible();

  // The two clicks: New rule, Save rule. What is typed is not a click.
  await page.evaluate(() => {
    (window as unknown as { __clicks: number }).__clicks = 0;
    document.addEventListener(
      "click",
      (event) => {
        if (event.isTrusted) (window as unknown as { __clicks: number }).__clicks += 1;
      },
      true,
    );
  });
  await page.getByRole("button", { name: "New rule" }).first().click();
  const drawer = page.getByRole("dialog", { name: "New alert rule" });
  await drawer.getByLabel("Name").fill("disk-full");
  await drawer.getByLabel("Limit").fill("plenty");
  await stillness(page);
  await expectNoA11yViolations(page, "the new alert rule drawer");
  // A limit that is not a number: the server's words, beside the field.
  await drawer.getByRole("button", { name: "Save rule" }).click();
  await expect(drawer.getByText("Not a limit: 'plenty'")).toBeVisible();
  await expect(drawer.getByLabel("Limit")).toHaveAttribute("aria-invalid", "true");
  await drawer.getByLabel("Limit").fill("85%");
  await drawer.getByRole("button", { name: "Save rule" }).click();
  await expect(drawer).toBeHidden();
  // One to open it, and one to save it, each time it was tried.
  expect(await page.evaluate(() => (window as unknown as { __clicks: number }).__clicks)).toBeLessThanOrEqual(4);

  const created = row(page, "disk-full");
  await expect(created.getByText("CPU above 85% for 5m 00s")).toBeVisible();
  await expect(created.getByText("OK", { exact: true })).toBeVisible();
  await expect(created.getByText("On the server.")).toBeVisible();
  await expect(page.getByText("No alert is firing.")).toBeVisible();

  // Edited: the name does not change, the limit is written as it was typed.
  await created.getByRole("button", { name: "Edit disk-full" }).click();
  const edit = page.getByRole("dialog", { name: "Edit disk-full" });
  await expect(edit.getByLabel("Name")).toHaveAttribute("readonly", "");
  await expect(edit.getByLabel("Limit")).toHaveValue("85");
  await edit.getByRole("button", { name: "Cancel" }).click();

  // Off, and gone after one question.
  await created.getByRole("switch", { name: "Judge disk-full" }).click();
  await expect(created.getByText("Off", { exact: true })).toBeVisible();
  await page.reload();
  await expect(row(page, "disk-full").getByText("Off", { exact: true })).toBeVisible();
  await row(page, "disk-full").getByRole("button", { name: "Remove disk-full" }).click();
  await page.getByRole("alertdialog", { name: "Remove disk-full?" }).getByRole("button", { name: "Remove" }).click();
  await expect(page.getByText("No alert rules yet")).toBeVisible();
});

test("the built-in checks are one form: only what changed is written, and the bar says so", async ({ page, consoleServer, problems }) => {
  problems.expect(/status of 403 .* \/api\/config$/);
  problems.expect(/status of 400 .* \/api\/config$/);
  await signIn(page, consoleServer, "/settings/alerts");
  const disk = page.getByLabel("Disk almost full at (%)");
  await expect(disk).toHaveValue("90");
  await expect(page.getByLabel("Certificates, days before they expire")).toHaveValue("14");

  await disk.fill("120");
  await page.getByRole("region", { name: "Unsaved changes" }).getByRole("button", { name: "Save" }).click();
  await confirmItsYou(page, consoleServer);
  // The configuration's own rule, beside the field it is about.
  await expect(page.getByText(/^monitor\.disk_threshold /)).toBeVisible();
  await expect(disk).toHaveAttribute("aria-invalid", "true");

  await disk.fill("95");
  await page.getByRole("region", { name: "Unsaved changes" }).getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("region", { name: "Unsaved changes" }).getByText("No unsaved changes")).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Disk almost full at (%)")).toHaveValue("95");
  await expect(page.getByLabel("Certificates, days before they expire")).toHaveValue("14");
  await settle(page);
  await expectNoA11yViolations(page, "the alerts page");
});
