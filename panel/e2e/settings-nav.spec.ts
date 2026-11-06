/**
 * Settings, grouped by what each section is for (docs/DESIGN.md 6.14, NV-7, NV-11), against the real backend:
 * seven titled groups, a hub that opens on every section as a door, a filter from twelve sections that finds a section by the name it
 * had before, a list that never outgrows the window at 1440 by 900, and on a phone an index page of its own. Both themes.
 */

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

// A server on its own has six: the seventh, Fleet, is for the console that manages others (4.2: Settings > Servers is gone, and the group has the
// fleet's own settings and what a central may do on a node only where there is a fleet).
const GROUPS = ["People and access", "Notices", "Connections", "Compliance and audit", "API", "Noust"];

test("the sections are in groups that say what they are for, in the order of the design", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer, "/settings/general");
  const list = page.getByRole("navigation", { name: "Settings sections" });
  await expect(list).toBeVisible();
  // A group and one of its sections can share a name ("Connections"): the titles are the paragraphs.
  for (const group of GROUPS) await expect(list.locator("p").getByText(group, { exact: true })).toBeVisible();
  const headings = await list.locator("p").allTextContents();
  expect(headings.map((text) => text.trim())).toEqual(GROUPS);
  // Within a group, the order an operator sets a server up in.
  await expect(list.getByRole("link").first()).toHaveText("Accounts");
  const links = await list.getByRole("link").allTextContents();
  // What warns is Alerts, an area of the bar, and no longer a section of Settings.
  expect(links).not.toContain("Alert rules");
  expect(links.indexOf("Logs and metrics export")).toBeGreaterThan(links.indexOf("Notification channels"));
  expect(links.indexOf("API tokens")).toBeLessThan(links.indexOf("API reference"));
  // The list stays inside the screen: the column is the window's height and the list scrolls inside it.
  const box = await page.locator('[data-frame="column"]').boundingBox();
  expect(box?.height ?? 9_999).toBeLessThanOrEqual(900);
  expect(await list.evaluate((nav) => getComputedStyle(nav).overflowY)).toBe("auto");
  await settle(page);
  await expectNoA11yViolations(page, "the settings list");
});

test("the hub opens on every section, by what it is for, with a line on what is inside", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer, "/settings");
  await expect(page.getByRole("heading", { level: 1, name: "Settings", exact: true })).toBeVisible();
  for (const group of GROUPS) await expect(page.getByRole("heading", { level: 2, name: group, exact: true })).toBeVisible();
  // A door is a link to its section and says what is in it, so a newcomer finds it without knowing its name.
  const door = page.getByRole("link", { name: /^Notification channels Where messages go/ });
  await expect(door).toHaveAttribute("href", "/settings/notifications");
  await door.click();
  await expect(page.getByRole("heading", { level: 1, name: "Notification channels", exact: true })).toBeVisible();
  await page.goto("/settings");
  await settle(page);
  await expectNoA11yViolations(page, "the settings hub");
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
  await expect(list.getByRole("link", { name: "Notification channels" })).toBeVisible();
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
  await expect(page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: "Notification channels" })).toHaveAttribute("aria-current", "page");
  await page.goto("/settings/console");
  await expect(page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: "Console address" })).toHaveAttribute("aria-current", "page");
});

test("on a phone the hub is the index page, with its groups, and a section has its way back", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, consoleServer, "/settings");
  await expect(page.getByRole("heading", { level: 2, name: "People and access", exact: true })).toBeVisible();
  const channels = page.getByRole("link", { name: /^Notification channels / });
  await expect(channels).toBeVisible();
  await settle(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await expectNoA11yViolations(page, "the settings hub on a phone");
  await channels.click();
  // The way back is the selector at the top of the page (4.2): it says the section and opens the list of every one.
  const selector = page.getByRole("main").getByRole("button", { name: /Notification channels/ });
  await expect(selector).toBeVisible();
  await selector.click();
  await expect(page.getByRole("dialog").getByRole("navigation", { name: "Settings sections" })).toBeVisible();
});
