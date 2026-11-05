/**
 * The new-app wizard's Source step has a height of its own (owner item 117: "the vertical scroll never ends and,
 * scrolled far enough, the whole page comes apart").
 *
 * Two causes, proved in a browser before either was touched:
 *
 * - Every row of the repositories list the GitHub App reaches has a screen-reader-only label. That label is
 *   absolutely positioned, and the list's scroll box was not its containing block, so the labels were laid out against
 *   the page, outside the list's clip: with 1,500 repositories the page was 54,466px tall, 1,323px with the labels out,
 *   and scrolled to its end it showed nothing but background.
 * - Browsing the catalog from the step drew its 440 recipes at once, in a column of 736px: 23,611 elements and a page
 *   of 32,353px.
 *
 * The page is judged by what is on it: how far it scrolls past the end of its content, and how many screens it is.
 */

import type { Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

const DESKTOP = { width: 1440, height: 900 };
const PHONE = { width: 390, height: 844 };

/** The page's own bottom padding (64px) and the step's sticky bar, with room for the browser's rounding. */
const PAST_THE_CONTENT = 160;

/** Screens a step may be: the template says "at most a screen and a fifth", and a page of recipes adds its cards and its footer. */
const SCREENS_AT_MOST = 8;

/** The repositories the seeded GitHub App reaches, repeated until the list is as long as an organisation's. */
async function manyRepositories(page: Page, copies: number): Promise<void> {
  await page.route("**/api/integrations/github/repositories", async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as { items: Record<string, unknown>[]; total: number };
    const items: Record<string, unknown>[] = [];
    for (let copy = 0; copy < copies; copy += 1) {
      for (const item of body.items) {
        items.push({ ...item, full_name: `${String(item.full_name)}-${String(copy)}`, source: `${String(item.source)}-${String(copy)}` });
      }
    }
    await route.fulfill({ response, json: { ...body, items, total: items.length } });
  });
}

/** How far the page scrolls past the end of what is drawn: blank background with the content already out of sight. */
async function scrollsPastContent(page: Page): Promise<number> {
  return page.evaluate(() => {
    const main = document.querySelector("main");
    if (main === null) throw new Error("no main");
    return Math.round(document.documentElement.scrollHeight - (main.getBoundingClientRect().bottom + window.scrollY));
  });
}

async function pageHeight(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollHeight);
}

/** Nothing is wider than the window: the page does not scroll sideways. */
async function expectNoSidewaysScroll(page: Page): Promise<void> {
  const widths = await page.evaluate(() => ({ page: document.documentElement.scrollWidth, window: document.documentElement.clientWidth }));
  expect(widths.page, "the page scrolls sideways").toBeLessThanOrEqual(widths.window);
}

test.describe("the Source step", () => {
  test("a long list of repositories scrolls inside its box, and the page ends where its content does", async ({ page, consoleServer }) => {
    test.setTimeout(240_000);
    // The five ways to start are above the list: a window tall enough to keep its bottom edge in view, where the pointer rests.
    await page.setViewportSize({ width: DESKTOP.width, height: 1200 });
    await manyRepositories(page, 120);
    await signIn(page, consoleServer, "/apps/new");
    const list = page.getByRole("listbox", { name: "Repositories" });
    await expect(list).toBeVisible();
    await expect(page.getByText(/600 repositories the App can read/)).toBeVisible();
    await settle(page);

    expect(await scrollsPastContent(page), "the page scrolls past its content").toBeLessThanOrEqual(PAST_THE_CONTENT);
    expect(await pageHeight(page)).toBeLessThanOrEqual(DESKTOP.height * SCREENS_AT_MOST);
    const box = await list.boundingBox();
    if (box === null) throw new Error("the list has no box");
    expect(box.height).toBeLessThanOrEqual(300);

    // The pointer over the list's bottom edge, and nothing else: no row is brought into view for it, so the list stays
    // where it was and the page does not move.
    const before = await list.evaluate((element) => element.scrollTop);
    await page.mouse.move(box.x + 200, box.y + box.height - 6);
    await page.mouse.move(box.x + 220, box.y + box.height - 8);
    await page.waitForTimeout(1_200);
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
    expect(await list.evaluate((element) => element.scrollTop)).toBe(before);

    // The wheel over the list moves the list, not the page.
    await page.mouse.wheel(0, 600);
    await expect.poll(async () => list.evaluate((element) => element.scrollTop)).toBeGreaterThan(before);
    expect(await page.evaluate(() => window.scrollY)).toBe(0);

    // The keyboard does follow the list: the row it reaches is inside the box.
    const search = page.getByRole("combobox", { name: "Repository" });
    await search.click();
    for (let step = 0; step < 40; step += 1) await search.press("ArrowDown");
    const inside = await page.evaluate(() => {
      const box = document.querySelector("[role=listbox]")?.getBoundingClientRect();
      const active = document.querySelector("[role=option][aria-selected=true]")?.getBoundingClientRect();
      if (!box || !active) return null;
      return active.top >= box.top - 1 && active.bottom <= box.bottom + 1;
    });
    expect(inside, "the row the keyboard reached is not in view").toBe(true);
    expect(await page.evaluate(() => window.scrollY)).toBe(0);

    // The page's end is the end of its content, however far it is scrolled.
    await page.evaluate(() => {
      window.scrollTo(0, document.documentElement.scrollHeight);
    });
    await expect(page.getByRole("button", { name: "Continue" })).toBeInViewport();
    await settle(page);
    await expectNoA11yViolations(page, "the repository picker with 600 repositories");
  });

  test("browsing all the recipes is a page of cards with its footer, not 440 cards at once", async ({ page, consoleServer }) => {
    test.setTimeout(240_000);
    await page.setViewportSize(DESKTOP);
    await signIn(page, consoleServer, "/apps/new");
    // The catalog card is the whole catalog, paged: no button to browse it.
    await page.getByRole("radio", { name: /^From the catalog/ }).click();
    const recipes = page.getByRole("list", { name: "Recipes" });
    await expect(recipes).toBeVisible();
    await settle(page);

    // A page of cards, as the catalog's page begins.
    await expect(recipes.getByRole("heading", { level: 3 })).toHaveCount(20);
    const footer = page.getByRole("group", { name: "Pagination" });
    await expect(footer.getByText(/^Showing 1–20 of \d+$/)).toBeVisible();
    expect(await pageHeight(page)).toBeLessThanOrEqual(DESKTOP.height * SCREENS_AT_MOST);
    expect(await scrollsPastContent(page)).toBeLessThanOrEqual(PAST_THE_CONTENT);
    expect(await page.evaluate(() => document.getElementsByTagName("*").length), "the step draws the whole catalog").toBeLessThan(6_000);
    await expectNoSidewaysScroll(page);

    // The way to the rest is the footer, and it is on the page: the first card of the next page replaces the first.
    const first = await recipes.getByRole("heading", { level: 3 }).first().innerText();
    await footer.getByRole("button", { name: "Next page" }).click();
    await expect(footer.getByText(/^Showing 21–40 of \d+$/)).toBeVisible();
    expect(await recipes.getByRole("heading", { level: 3 }).first().innerText()).not.toBe(first);
    await expect(recipes.getByRole("heading", { level: 3 })).toHaveCount(20);
    expect(await scrollsPastContent(page)).toBeLessThanOrEqual(PAST_THE_CONTENT);

    // Scrolled to the end, the step's bar and its terminal hint are what the page ends with.
    await page.evaluate(() => {
      window.scrollTo(0, document.documentElement.scrollHeight);
    });
    await expect(page.getByRole("button", { name: "Continue" })).toBeInViewport();
    await settle(page);
    await expectNoA11yViolations(page, "the Source step browsing the catalog");
  });

  test("what is typed in the box finds a page of recipes, not every one of them", async ({ page, consoleServer }) => {
    test.setTimeout(240_000);
    await page.setViewportSize(DESKTOP);
    await signIn(page, consoleServer, "/apps/new");
    await page.getByLabel("What to deploy").fill("a");
    const recipes = page.getByRole("list", { name: "Recipes" });
    await expect(recipes).toBeVisible();
    await settle(page);
    expect(await recipes.getByRole("heading", { level: 3 }).count()).toBeLessThanOrEqual(20);
    await expect(page.getByRole("group", { name: "Pagination" }).getByText(/^Showing 1–20 of \d+$/)).toBeVisible();
    expect(await pageHeight(page)).toBeLessThanOrEqual(DESKTOP.height * SCREENS_AT_MOST);
    expect(await scrollsPastContent(page)).toBeLessThanOrEqual(PAST_THE_CONTENT);
  });

  test("on a phone the step is as wide as the screen and ends where its content does", async ({ page, consoleServer }) => {
    test.setTimeout(240_000);
    await page.setViewportSize(PHONE);
    await manyRepositories(page, 100);
    await signIn(page, consoleServer, "/apps/new");
    await expect(page.getByRole("listbox", { name: "Repositories" })).toBeVisible();
    await settle(page);
    await expectNoSidewaysScroll(page);
    expect(await scrollsPastContent(page)).toBeLessThanOrEqual(PAST_THE_CONTENT);
    expect(await pageHeight(page)).toBeLessThanOrEqual(PHONE.height * 12);

    // The repositories' labels are hidden below 640px, so a phone never lengthened the page for them; the recipes did.
    await page.getByLabel("What to deploy").fill("a");
    await expect(page.getByRole("list", { name: "Recipes" })).toBeVisible();
    await settle(page);
    await expectNoSidewaysScroll(page);
    expect(await scrollsPastContent(page)).toBeLessThanOrEqual(PAST_THE_CONTENT);
    expect(await page.getByRole("list", { name: "Recipes" }).getByRole("heading", { level: 3 }).count()).toBeLessThanOrEqual(20);
    expect(await pageHeight(page)).toBeLessThanOrEqual(PHONE.height * 12);
  });
});
