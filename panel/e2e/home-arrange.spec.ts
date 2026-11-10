/**
 * Arranging Home with the pointer, in a real browser: what a held edge shows (the outline of the size it will give and
 * its name, and nothing moving until it is let go), the offer to undo that follows, and the page that scrolls under a
 * held widget for as long as the pointer rests at the bottom of the window, with no more movement. The sizes and the
 * undo are what a person sees; the scroll is what a stream of move events could not give them.
 */

import type { Locator, Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

async function centre(locator: Locator): Promise<{ x: number; y: number }> {
  const box = await locator.boundingBox();
  if (box === null) throw new Error("not on screen");
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

const sizeButton = (page: Page, widget: string): Locator => page.getByRole("button", { name: new RegExp(`^Size and position of ${widget}: `) });

test.beforeEach(async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer);
  await page.goto("/?customise=true");
  await expect(page.getByRole("heading", { level: 1, name: "Customize “This server”" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Move Resources", exact: true })).toBeVisible();
  await settle(page);
});

test("an edge shows the size it will give, with its name, and changes the widget only when it is let go; the toast undoes it", async ({ page }) => {
  await expect(sizeButton(page, "Resources")).toHaveAccessibleName(/: Small$/);
  const edge = page.locator('[data-cell="resources"] [data-resize-edge="right"]');
  const from = await centre(edge);
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(from.x + 40, from.y + 4, { steps: 4 });
  await page.mouse.move(from.x + 260, from.y + 4, { steps: 12 });

  const outline = page.locator("[data-resize-outline]");
  await expect(outline).toHaveAttribute("data-resize-outline", "medium");
  await expect(outline).toContainText("Medium, 2 × 1");
  // The widget keeps the size it has while the edge is held, and what is around it stays where it was.
  await expect(sizeButton(page, "Resources")).toHaveAccessibleName(/: Small$/);

  await page.mouse.up();
  await expect(outline).toHaveCount(0);
  await expect(sizeButton(page, "Resources")).toHaveAccessibleName(/: Medium$/);
  await expect(page.locator("html")).not.toHaveClass(/widget-dragging/);

  const toast = page.locator('[data-toast-region="polite"]');
  await expect(toast).toContainText("Resized Resources");
  await toast.getByRole("button", { name: "Undo" }).click();
  await expect(sizeButton(page, "Resources")).toHaveAccessibleName(/: Small$/);
});

test("the corner takes both measures, and Escape puts the widget back as it was", async ({ page }) => {
  const corner = page.locator('[data-cell="resources"] [data-resize="corner"]');
  const from = await centre(corner);
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(from.x + 40, from.y + 40, { steps: 4 });
  await page.mouse.move(from.x + 260, from.y + 120, { steps: 12 });
  await expect(page.locator("[data-resize-outline]")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator("[data-resize-outline]")).toHaveCount(0);
  await expect(sizeButton(page, "Resources")).toHaveAccessibleName(/: Small$/);
  await page.mouse.up();
  await expect(sizeButton(page, "Resources")).toHaveAccessibleName(/: Small$/);
});

test("a held widget scrolls the page for as long as the pointer rests at the bottom edge, and stops when it is let go", async ({ page }) => {
  const handle = page.getByRole("button", { name: "Move Needs attention", exact: true });
  const from = await centre(handle);
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(from.x + 20, from.y + 30, { steps: 3 });
  // To the bottom of the window, and not another move.
  await page.mouse.move(from.x + 40, 890, { steps: 10 });
  const scrolled = (): Promise<number> => page.evaluate(() => Math.round(window.scrollY));
  // The page is longer than the window, and the pointer has not moved since: it is the edge that carries the page to its end.
  const end = await page.evaluate(() => document.documentElement.scrollHeight - window.innerHeight);
  expect(end).toBeGreaterThan(200);
  await expect.poll(scrolled, { timeout: 8_000 }).toBeGreaterThanOrEqual(end - 2);
  await page.mouse.up();
  // Let go, it stays where it is.
  await page.waitForTimeout(200);
  const rest = await scrolled();
  await page.waitForTimeout(300);
  expect(await scrolled()).toBe(rest);
});

test("the cards of the gallery say everything across the card, and pass axe", async ({ page }) => {
  const figures = page.locator('[data-frame="column"] li').filter({ hasText: "The six figures" });
  await expect(figures).toBeVisible();
  // The description takes the width of the card, not what the icon and the + leave: three lines at most.
  expect((await figures.boundingBox())?.height ?? 0).toBeLessThan(150);
  await expect(figures).toContainText("The six figures: apps, deploys, certificates, backups, disk and updates");
  await settle(page);
  await expectNoA11yViolations(page, "home, customized, gallery");
});
