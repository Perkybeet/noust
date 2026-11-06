/**
 * Where the pages end on a 1440px window: at the right edge of the shell's column, every one of them. A section of Settings
 * is a template with a measure of its own (`--width-settings`) and ended 16px before the edge the other pages end at, which on
 * Compliance and Access security showed beside the pages around them. The check reads the boxes the browser laid out, so a
 * token that drifts from the column it is meant to fill fails here and not in a screenshot.
 */

import { expect, settle, signIn, test } from "./fixtures";

const ROUTES = ["/apps", "/server", "/settings", "/settings/compliance", "/settings/access-security", "/settings/notifications", "/settings/general"];

for (const route of ROUTES) {
  test(`${route} ends where the shell's column does`, async ({ page, consoleServer }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await signIn(page, consoleServer, route);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await settle(page);

    const edges = await page.evaluate(() => {
      const column = document.querySelector<HTMLElement>("main > div");
      const root = document.querySelector<HTMLElement>("main [data-template]");
      if (column === null || root === null) return null;
      const padding = Number.parseFloat(getComputedStyle(column).paddingRight);
      return { column: Math.round(column.getBoundingClientRect().right - padding), page: Math.round(root.getBoundingClientRect().right) };
    });
    expect(edges, "a page with a template").not.toBeNull();
    // A pixel for the rounding of fractional widths; the gap this guards was sixteen.
    expect(Math.abs((edges?.page ?? 0) - (edges?.column ?? 0)), JSON.stringify(edges)).toBeLessThanOrEqual(1);
  });
}
