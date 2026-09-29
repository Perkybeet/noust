/**
 * Cumulative Layout Shift of every page, in both themes, measured as the page loads cold: a
 * PerformanceObserver on `layout-shift` is installed before any of the page's own scripts run,
 * and the score is read once the page has settled. Shifts that follow user input are excluded
 * (the browser flags them `hadRecentInput`), and none is made: the test only loads the page.
 *
 * The score is the web-vitals definition - the largest session window of shifts less than a
 * second apart, capped at five seconds - and a page fails above 0.05, half of what the metric
 * itself calls "good": the console is an instrument, and a row that jumps as it is about to
 * be clicked is a misclick. On failure the message lists the elements that moved, largest
 * shift first, so the cause is fixed at its source rather than guessed at.
 *
 * With WASM_CLS_REPORT set to a directory, each page's score is written there as JSON too,
 * for a before-and-after table.
 */

import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

import { CLS_LIMIT, CLS_TAIL_MS, cumulativeLayoutShift, describeShifts, observeLayoutShifts } from "./cls";
import { expect, settle, signIn, test } from "./fixtures";
import { ROUTES, routePath } from "./routes";

const DESKTOP = { width: 1440, height: 900 };

for (const route of ROUTES) {
  test(`${route.name} does not shift as it loads`, async ({ page, consoleServer }, testInfo) => {
    await page.setViewportSize(DESKTOP);
    await signIn(page, consoleServer);
    const target = await routePath(page, route);

    // A cold load of the page itself, not the client-side navigation that follows sign-in.
    await page.addInitScript(observeLayoutShifts);
    await page.goto(target);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await settle(page);
    await page.waitForTimeout(CLS_TAIL_MS);

    const shifts = await page.evaluate(() => window.__wasmShifts ?? []);
    const score = cumulativeLayoutShift(shifts);

    const report = process.env.WASM_CLS_REPORT;
    if (report) {
      mkdirSync(report, { recursive: true });
      writeFileSync(
        path.join(report, `${route.name}-${testInfo.project.name}.json`),
        JSON.stringify({ route: route.name, theme: testInfo.project.name, cls: score, shifts }, null, 2),
      );
    }

    const culprits = describeShifts(shifts);
    expect(score, `layout shift of ${target} is ${score.toFixed(4)}:\n${culprits}`).toBeLessThanOrEqual(CLS_LIMIT);
  });
}
