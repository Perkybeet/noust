/**
 * The map of the server against the real backend (checklist M-69): the seeded machine's names,
 * applications, databases and scheduled work in fixed columns, the same connections as a table with
 * links to each thing, nothing moving when it is read again, and a list per column on a phone.
 */

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

async function seededApps(page: import("@playwright/test").Page): Promise<string[]> {
  const response = await page.request.get("/api/apps");
  return ((await response.json()) as { apps: { domain: string }[] }).apps.map((app) => app.domain);
}

test("the server's applications, databases and scheduled work are drawn in columns and listed as a table", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/map");
  await expect(page.getByRole("heading", { level: 1, name: "Map" })).toBeVisible();
  const figure = page.getByRole("figure", { name: "How this server fits together" });
  await expect(figure).toBeVisible();
  const domains = await seededApps(page);
  expect(domains.length).toBeGreaterThan(1);
  // The columns are lists whose items are placed, so a list itself has no box: it is there, with its items.
  for (const title of ["Names", "Applications"]) await expect(figure.getByRole("list", { name: title })).toBeAttached();
  await expect(figure.getByRole("button", { name: new RegExp(`^Application ${domains[0]?.replaceAll(".", "\\.") ?? ""}`) })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the map");

  await page.getByRole("button", { name: "Show the connections as a table" }).click();
  const table = page.getByRole("table", { name: "Connections of How this server fits together" });
  await expect(table.getByRole("link", { name: domains[0] ?? "" }).first()).toBeVisible();
  await expect(table.getByText("answers on").first()).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the map's table");

  // A thing in the table leads to where it is.
  await table.getByRole("link", { name: domains[0] ?? "" }).first().click();
  await expect(page).toHaveURL(new RegExp(`/apps/${(domains[0] ?? "").replaceAll(".", "\\.")}$`));
});

test("the map is the same picture when it is read again", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/map");
  const figure = page.getByRole("figure", { name: "How this server fits together" });
  await expect(figure).toBeVisible();
  await settle(page);
  const before = await figure.locator("[data-node]").evaluateAll((nodes) => nodes.map((node) => `${node.getAttribute("data-node") ?? ""}@${(node as HTMLElement).style.top}:${(node as HTMLElement).style.left}`));
  await page.reload();
  await expect(figure).toBeVisible();
  await settle(page);
  const after = await figure.locator("[data-node]").evaluateAll((nodes) => nodes.map((node) => `${node.getAttribute("data-node") ?? ""}@${(node as HTMLElement).style.top}:${(node as HTMLElement).style.left}`));
  expect(after).toEqual(before);
});

test("on a phone each column is a list, and the page never scrolls sideways", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, consoleServer, "/map");
  const figure = page.getByRole("figure", { name: "How this server fits together" });
  await expect(figure.getByRole("list", { name: "Applications" })).toBeVisible();
  await settle(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  await expectNoA11yViolations(page, "the map on a phone");
});

test("the map is found from the palette and from the Overview's tabs", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/");
  await settle(page);
  await page.keyboard.press("Control+k");
  await page.getByRole("combobox", { name: "Search apps, databases, domains, servers, settings and actions" }).fill("map of this");
  await page.getByRole("option", { name: "Open the map of this server" }).click();
  await expect(page).toHaveURL(/\/map$/);
  await expect(page.getByRole("heading", { level: 1, name: "Map" })).toBeVisible();

  await page.goto("/");
  await page.getByRole("navigation", { name: "Overview sections" }).getByRole("link", { name: "Map" }).click();
  await expect(page).toHaveURL(/\/map$/);
});
