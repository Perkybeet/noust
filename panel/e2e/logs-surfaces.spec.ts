/**
 * The three places that read logs have one set of controls (docs/DESIGN.md, LG-1): the Logs page
 * over the applications, the same page over the system's journal, and an application's own Logs
 * tab. Each has the query box, the source, the range with "Live" among its choices, the syntax and
 * a link to the exact view, and the viewer's own level chips and Follow under them. Reading the
 * live log and keeping the errors is two clicks from an application (budget T10).
 */

import type { Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

const DOMAIN = "tienda.example.org";

const SURFACES = [
  { name: "the applications' logs", path: "/logs" },
  { name: "the system's logs", path: "/logs?source=system" },
  { name: "an application's logs", path: `/apps/${DOMAIN}/logs` },
] as const;

async function expectTheToolbar(page: Page): Promise<void> {
  const bar = page.getByRole("search", { name: "Filter the logs" });
  await expect(bar).toBeVisible();
  await expect(bar.getByRole("searchbox", { name: "Search the logs" })).toHaveAttribute("data-page-search", "");
  await expect(bar.getByRole("radiogroup", { name: /^(Where from|Source)$/ })).toBeVisible();
  await expect(bar.getByRole("combobox", { name: "How far back" })).toBeVisible();
  await expect(bar.getByRole("button", { name: "Query syntax" })).toBeVisible();
  await expect(bar.getByRole("button", { name: "Copy link to this search" })).toBeVisible();
  // Live is one of the ranges, the first.
  await bar.getByRole("combobox", { name: "How far back" }).click();
  await expect(page.getByRole("option", { name: "Live" })).toBeVisible();
  await page.keyboard.press("Escape");
}

for (const surface of SURFACES) {
  test(`${surface.name} have the one toolbar`, async ({ page, consoleServer }) => {
    await signIn(page, consoleServer, surface.path);
    await expectTheToolbar(page);
    // Following is the viewer's, once: the page adds no switch of its own.
    await expect(page.getByRole("switch", { name: /^(Live|Follow)$/ })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Follow" })).toHaveCount(1);
    await settle(page);
    await expectNoA11yViolations(page, `${surface.name}, with their toolbar`);
  });
}

test("the range is written in the address of every surface, so the link to a view is the address", async ({ page, consoleServer }) => {
  for (const surface of SURFACES) {
    await signIn(page, consoleServer, surface.path);
    await page.getByRole("combobox", { name: "How far back" }).click();
    await page.getByRole("option", { name: "24 hours" }).click();
    await expect(page).toHaveURL(/since=24h/);
    await page.getByRole("searchbox", { name: "Search the logs" }).fill("timeout -debug");
    await expect(page).toHaveURL(/q=timeout(%20|\+)-debug/);
    await expect(page.getByText("Understood as")).toBeVisible();
    // The view comes back from its address alone.
    await page.reload();
    await expect(page.getByRole("combobox", { name: "How far back" })).toHaveText("24 hours");
    await expect(page.getByRole("searchbox", { name: "Search the logs" })).toHaveValue("timeout -debug");
  }
});

test("an application's live log and its errors are two clicks away", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${DOMAIN}`);
  await page.getByRole("link", { name: "Logs" }).first().click();
  await expect(page).toHaveURL(new RegExp(`/apps/${DOMAIN}/logs`));
  const journal = page.getByRole("region", { name: `Logs of ${DOMAIN}` });
  await expect(page.getByRole("combobox", { name: "How far back" })).toHaveText("Live");
  await expect(journal.getByText(/tienda-example-org\[\d+\]: /).first()).toBeVisible();
  // The second click: the viewer's chip keeps the lines that look like a problem.
  await page.getByRole("button", { name: /^Error \d+$/ }).click();
  await expect(journal.getByText(/ECONNREFUSED/).first()).toBeVisible();
  await expect(journal.locator("[data-index]").filter({ hasNotText: /ECONNREFUSED|error|Error/i })).toHaveCount(0);
});

test("an application's logs read a window of the past, bound to it, and the choice of source hides it where it has no meaning", async ({
  page,
  consoleServer,
}) => {
  await signIn(page, consoleServer, `/apps/${DOMAIN}/logs`);
  await page.getByRole("combobox", { name: "How far back" }).click();
  await page.getByRole("option", { name: "24 hours" }).click();
  await expect(page).toHaveURL(/since=24h/);
  await expect(page.getByRole("status").filter({ hasText: /examined/ })).toBeVisible();
  await expect(page.getByText(`noust log search --app ${DOMAIN} --since 24h`)).toBeVisible();

  await page.getByRole("radio", { name: "Web server requests" }).click();
  await expect(page.getByRole("combobox", { name: "How far back" })).toHaveCount(0);
  await settle(page);
  await expectNoA11yViolations(page, "an application's logs, over a window and over the web server's file");
});
