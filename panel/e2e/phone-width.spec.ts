/**
 * No page is wider than a phone. Every route of routes.ts, in Spanish (the language whose words run
 * longest), at 390 px: the document must not scroll sideways and nothing may stick out past the
 * right edge unless a scrolling box of its own holds it (a table, a log, a code block).
 *
 * It was a gap between the lanes that built the pages one by one: each looked right at 1440, and
 * one page's row of buttons, another's long state word and the fleet's table were wider than the
 * phone. Light theme only: a colour does not change a width.
 */

import { expect, settle, test } from "./fixtures";
import { ROUTES, routePath } from "./routes";
import { signInSpanish, useSpanish } from "./spanish";

const PHONE = { width: 390, height: 844 };

/** What sticks out of the page to the right, outside a box that scrolls on its own. */
function sticksOut(): string[] {
  const width = document.documentElement.clientWidth;
  const clipped = (element: Element): boolean => {
    for (let parent = element.parentElement; parent !== null && parent !== document.body; parent = parent.parentElement) {
      if (getComputedStyle(parent).overflowX !== "visible") return true;
    }
    return false;
  };
  const found: string[] = [];
  for (const element of document.body.querySelectorAll("*")) {
    if (!(element instanceof HTMLElement) || !element.checkVisibility()) continue;
    const box = element.getBoundingClientRect();
    if (box.width === 0 || box.right <= width + 1 || clipped(element)) continue;
    const text = (element.innerText || element.textContent || "").replace(/\s+/g, " ").trim().slice(0, 50);
    found.push(`<${element.tagName.toLowerCase()} class="${element.className.split(/\s+/).slice(0, 4).join(" ")}"> reaches ${String(Math.round(box.right))}: "${text}"`);
  }
  return found.slice(0, 5);
}

for (const route of ROUTES) {
  test(`${route.name} fits a phone`, async ({ page, consoleServer }, testInfo) => {
    test.skip(testInfo.project.name !== "light", "a colour does not change a width");
    await useSpanish(page);
    await page.setViewportSize(PHONE);
    await signInSpanish(page, consoleServer);
    await page.goto(await routePath(page, route));
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await settle(page);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `the page scrolls sideways by ${String(overflow)}px:\n${(await page.evaluate(sticksOut)).join("\n")}`).toBeLessThanOrEqual(0);
    expect(await page.evaluate(sticksOut)).toEqual([]);
  });
}
