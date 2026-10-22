/**
 * A page holds its shape when the second answer it waits for comes late. Some of what a page
 * draws is the answer to a request that follows the first one: whether the sidebar offers
 * Projects is `GET /api/teams/mine`, whether a database's overview has its charts is the
 * metrics read after the overview. On a slow or busy machine that moment is long, and whatever
 * appears then pushes every row under it down while the operator is reaching for one (the
 * Spanish sweep failed on exactly that, one run in ten). These gates hold the answer back for
 * most of a second, which is what a loaded machine does on its own, so the shift is measured
 * every run and not only when the suite happens to be slow.
 */

import type { Page } from "@playwright/test";

import { CLS_LIMIT, CLS_TAIL_MS, cumulativeLayoutShift, describeShifts, observeLayoutShifts } from "./cls";
import type { ShiftRecord } from "./cls";
import { expect, settle, signIn, test } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

const DESKTOP = { width: 1440, height: 900 };
const LATE_MS = 800;

/** Holds back every answer to `pattern`, then lets it through. */
async function late(page: Page, pattern: string): Promise<void> {
  await page.route(pattern, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, LATE_MS));
    await route.continue();
  });
}

/** Loads `path` cold, as the web-vitals definition does, and returns the page's layout shift. */
async function shiftOf(page: Page, server: ConsoleServer, path: string, pattern: string): Promise<{ score: number; report: string; shifts: ShiftRecord[] }> {
  await page.setViewportSize(DESKTOP);
  await signIn(page, server);
  await late(page, pattern);
  await page.addInitScript(observeLayoutShifts);
  await page.goto(path);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await settle(page);
  await page.waitForTimeout(CLS_TAIL_MS);
  const shifts = await page.evaluate(() => window.__noustShifts ?? []);
  const score = cumulativeLayoutShift(shifts);
  return { score, report: `layout shift of ${path} with ${pattern} late is ${score.toFixed(4)}:\n${describeShifts(shifts)}`, shifts };
}

for (const path of ["/apps", "/catalog"]) {
  test(`the sidebar of ${path} does not shift while the teams are read`, async ({ page, consoleServer }) => {
    const { score, report, shifts } = await shiftOf(page, consoleServer, path, "**/api/teams/mine");
    expect(score, report).toBeLessThanOrEqual(CLS_LIMIT);
    // The Projects link was a shift of a few thousandths, far under the limit: what is held is
    // that no row of the sidebar (its links, its lists, the rows of its skeleton) moves at all. A sidebar link is
    // `group relative flex h-control-md`: the page's own tab strip draws `group relative flex h-10`, a count arriving
    // there is the page's and is held by the limit above, so the pattern names the sidebar's height.
    const sidebar = shifts.filter((shift) => shift.sources.some((source) => /<a\.group\.relative\.flex\.h-control-md|<ul\.flex\.flex-col\.gap-px|<li\.flex\.h-control-md/.test(source)));
    expect(sidebar, report).toEqual([]);

    // What the shift was held for: the link did arrive, the nav is no longer busy.
    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav).not.toHaveAttribute("aria-busy", "true");
    await expect(nav.getByRole("link", { name: "Projects" })).toBeVisible();
  });
}

test("a database's overview does not shift while its metrics are read", async ({ page, consoleServer }) => {
  const { score, report } = await shiftOf(page, consoleServer, "/databases/postgresql/example_production", "**/api/databases/databases/postgresql/example_production/metrics");
  expect(score, report).toBeLessThanOrEqual(CLS_LIMIT);

  // The charts the place was held for did arrive.
  await expect(page.getByRole("heading", { level: 2, name: "The last 24 hours" })).toBeVisible();
});
