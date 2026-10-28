/**
 * A page holds its shape when the second answer it waits for comes late. Some of what a page
 * draws is the answer to a request that follows the first one: whether the list of applications offers
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
  test(`${path} does not shift while the teams are read`, async ({ page, consoleServer }) => {
    const { score, report, shifts } = await shiftOf(page, consoleServer, path, "**/api/teams/mine");
    expect(score, report).toBeLessThanOrEqual(CLS_LIMIT);
    // The main bar is the same twelve areas whether the teams are known or not (Projects is a link in the header
    // of the list of applications, not a destination of the bar), so none of its links is ever a shift. A link of
    // the bar is `group elev-lift relative flex h-10` (the rail) or `h-9` (one bar).
    const bar = shifts.filter((shift) => shift.sources.some((source) => /<a\.group\.elev-lift\.relative\.flex\.h-(?:9|10)\b/.test(source)));
    expect(bar, report).toEqual([]);
    await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link")).toHaveCount(12);
    // What was waited for did arrive, in the header of the list, where a link arriving moves nothing.
    if (path === "/apps") await expect(page.getByRole("link", { name: "Projects" })).toBeVisible();
  });
}

test("a database's overview does not shift while its metrics are read", async ({ page, consoleServer }) => {
  const { score, report } = await shiftOf(page, consoleServer, "/databases/postgresql/example_production", "**/api/databases/databases/postgresql/example_production/metrics");
  expect(score, report).toBeLessThanOrEqual(CLS_LIMIT);

  // The charts the place was held for did arrive.
  await expect(page.getByRole("heading", { level: 2, name: "The last 24 hours" })).toBeVisible();
});
