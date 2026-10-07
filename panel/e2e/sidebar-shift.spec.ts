/**
 * The sidebar does not move when the server's teams answer late. Whether it offers Projects is
 * the answer to `GET /api/teams/mine`, which arrives a moment after the shell is drawn; on a
 * slow or busy machine that moment is long, and a link that appears then pushes every row under
 * it down while the operator is reaching for one. The gate holds the answer back for most of a
 * second, which is what a loaded machine does on its own, so the shift is measured every time
 * and not only when the suite happens to be slow.
 */

import { CLS_LIMIT, CLS_TAIL_MS, cumulativeLayoutShift, describeShifts, observeLayoutShifts } from "./cls";
import { expect, settle, signIn, test } from "./fixtures";

const DESKTOP = { width: 1440, height: 900 };
const LATE_MS = 800;

for (const path of ["/apps", "/catalog"]) {
  test(`the sidebar of ${path} does not shift while the teams are read`, async ({ page, consoleServer }) => {
    await page.setViewportSize(DESKTOP);
    await signIn(page, consoleServer);

    // The teams answer late, however busy the machine is: the shift is measured every run.
    await page.route("**/api/teams/mine", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, LATE_MS));
      await route.continue();
    });
    await page.addInitScript(observeLayoutShifts);
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await settle(page);
    await page.waitForTimeout(CLS_TAIL_MS);

    const shifts = await page.evaluate(() => window.__noustShifts ?? []);
    const score = cumulativeLayoutShift(shifts);
    expect(score, `layout shift of ${path} with late teams is ${score.toFixed(4)}:\n${describeShifts(shifts)}`).toBeLessThanOrEqual(CLS_LIMIT);

    // What the shift was held for: the link did arrive, the nav is no longer busy.
    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav).not.toHaveAttribute("aria-busy", "true");
    await expect(nav.getByRole("link", { name: "Projects" })).toBeVisible();
  });
}
