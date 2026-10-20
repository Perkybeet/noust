/**
 * Settings, grouped by whose they are (docs/DESIGN.md 6.14, NV-7, NV-11), against the real backend:
 * six titled groups in two blocks, a filter from twelve sections that finds a section by the name it
 * had before, a list that never outgrows the window at 1440 by 900, and on a phone an index page of its own. Both themes.
 */

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

const GROUPS = ["This server", "Monitoring", "Developers", "People and access", "Governance", "Fleet"];

test("the sections are in six groups that say whose they are, in the order of the design", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer, "/settings");
  const list = page.getByRole("navigation", { name: "Settings sections" });
  await expect(list).toBeVisible();
  for (const group of GROUPS) await expect(list.getByText(group, { exact: true })).toBeVisible();
  const headings = await list.locator("p").allTextContents();
  expect(headings.map((text) => text.trim())).toEqual(GROUPS);
  // Within a group, the order an operator sets a server up in.
  await expect(list.getByRole("link").first()).toHaveText("General");
  const links = await list.getByRole("link").allTextContents();
  expect(links.indexOf("Alert rules")).toBeGreaterThan(links.indexOf("Channels"));
  expect(links.indexOf("API tokens")).toBeLessThan(links.indexOf("API reference"));
  // The list stays inside the screen: below the top bar, never taller than the window (it scrolls inside).
  const box = await page.locator("[data-slot=nav] > div").boundingBox();
  expect(box?.height ?? 9_999).toBeLessThanOrEqual(900 - 56);
  await settle(page);
  await expectNoA11yViolations(page, "the settings list");
});

test("a filter finds a section by its name and by the name it had before", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer, "/settings");
  const list = page.getByRole("navigation", { name: "Settings sections" });
  const filter = page.getByRole("searchbox", { name: "Filter sections" });
  await filter.fill("console access");
  await expect(list.getByRole("link")).toHaveCount(1);
  await expect(list.getByRole("link", { name: "Console address" })).toBeVisible();
  await filter.fill("notifications");
  await expect(list.getByRole("link", { name: "Channels" })).toBeVisible();
  await filter.fill("prometheus");
  await expect(list.getByRole("link", { name: "Logs and metrics export" })).toBeVisible();
  await filter.fill("zzzz");
  await expect(list.getByRole("link")).toHaveCount(0);
  await expect(page.getByText("No section matches this filter.")).toBeVisible();
  await filter.fill("");
  await expect(list.getByRole("link").first()).toBeVisible();
});

test("the old names of the sections still open them", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/settings/notifications");
  await expect(page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: "Channels" })).toHaveAttribute("aria-current", "page");
  await page.goto("/settings/console");
  await expect(page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: "Console address" })).toHaveAttribute("aria-current", "page");
});

test("on a phone the list is the index page, with its groups, and a section has its way back", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, consoleServer, "/settings");
  const index = page.getByRole("navigation", { name: "Settings sections" });
  await expect(index.getByText("This server", { exact: true })).toBeVisible();
  await expect(index.getByRole("link", { name: "Channels" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the settings index on a phone");
  await index.getByRole("link", { name: "Channels" }).click();
  await expect(page.getByRole("link", { name: "All sections" })).toBeVisible();
});
