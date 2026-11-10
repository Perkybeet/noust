/**
 * Customising Home does not make the page longer than what it holds. The gallery keeps the sizes of its cards for a
 * screen reader as `sr-only` text, which is `position: absolute`; with nothing positioned between them and the second
 * column of the frame (as tall as the window) they were placed against the column, and sixty-five of them below the
 * fold made the page 2499px for 1418px of content at 1440 and 1920. Only a browser lays that out, so it is held here.
 */

import { expect, settle, signIn, test } from "./fixtures";

for (const [width, height] of [
  [1440, 900],
  [1920, 1080],
] as const) {
  test(`the page is no longer than its content while it is customised, at ${String(width)} x ${String(height)}`, async ({ page, consoleServer }) => {
    await page.setViewportSize({ width, height });
    await signIn(page, consoleServer);
    await page.goto("/?customise=true");
    await expect(page.getByRole("heading", { level: 1, name: "Customize “This server”" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Move Needs attention", exact: true })).toBeVisible();
    await settle(page);
    const { pageHeight, contentBottom } = await page.evaluate(() => {
      const main = document.querySelector("main");
      if (main === null) throw new Error("no main");
      return { pageHeight: document.documentElement.scrollHeight, contentBottom: Math.round(main.getBoundingClientRect().bottom + window.scrollY) };
    });
    // The window itself is the least a page can be.
    expect(pageHeight).toBeLessThanOrEqual(Math.max(contentBottom, height) + 2);
  });
}
