/**
 * The console in Spanish: every route of routes.ts, in both themes, loaded cold with the
 * language already chosen, passes the same gates the English pages do - axe (WCAG 2.2 AA),
 * no CSP violation or console error (the `problems` fixture), a layout shift under 0.05 -
 * says `<html lang="es">` so a screen reader speaks Spanish, and shows no text of the
 * English catalog that has a Spanish translation. The wizard's recipe and import starts,
 * which are states of /apps/new rather than routes, are swept too.
 */

import type { Page } from "@playwright/test";

import { spanish } from "./catalog";
import { CLS_LIMIT, CLS_TAIL_MS, cumulativeLayoutShift, describeShifts, observeLayoutShifts } from "./cls";
import { expect, expectNoA11yViolations, settle, test } from "./fixtures";
import { ROUTES, routePath } from "./routes";
import { expectNoEnglishLeftovers, signInSpanish, useSpanish } from "./spanish";

const DESKTOP = { width: 1440, height: 900 };

/** The gates every page passes in Spanish, once it has settled. */
async function expectSpanishPage(page: Page, label: string): Promise<void> {
  await expect(page.locator("html")).toHaveAttribute("lang", "es");
  await expectNoA11yViolations(page, `${label} (es)`);
  await expectNoEnglishLeftovers(page, label);
}

for (const route of ROUTES) {
  test(`${route.name} in Spanish`, async ({ page, consoleServer }) => {
    await page.setViewportSize(DESKTOP);
    await useSpanish(page);
    await signInSpanish(page, consoleServer);
    const target = await routePath(page, route);

    // A cold load, measured from before the console's own scripts run.
    await page.addInitScript(observeLayoutShifts);
    await page.goto(target);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await settle(page);
    await page.waitForTimeout(CLS_TAIL_MS);
    const shifts = await page.evaluate(() => window.__noustShifts ?? []);
    const score = cumulativeLayoutShift(shifts);
    expect(score, `layout shift of ${target} in Spanish is ${score.toFixed(4)}:\n${describeShifts(shifts)}`).toBeLessThanOrEqual(CLS_LIMIT);

    await expectSpanishPage(page, route.name);
  });
}

for (const mode of ["recipe", "import"] as const) {
  test(`the wizard's ${mode} start in Spanish`, async ({ page, consoleServer }) => {
    await page.setViewportSize(DESKTOP);
    await useSpanish(page);
    await signInSpanish(page, consoleServer, "/apps/new");
    const choice = page.getByRole("radiogroup", { name: spanish.newApp.modes.label }).getByRole("radio", { name: spanish.newApp.modes[mode] });
    await choice.click();
    await expect(choice).toHaveAttribute("aria-checked", "true");
    if (mode === "recipe") await expect(page.getByRole("button", { name: /WordPress/ })).toBeVisible();
    else await expect(page.getByLabel(spanish.newApp.importApp.file)).toBeVisible();
    await settle(page);
    await expectSpanishPage(page, `/apps/new (${mode})`);
  });
}

// The sweep is only worth its verdicts if it still finds what it is for, so it is held to both
// sides: a value that stands alone in data markup is not a sentence of the console's (a project
// named "default" in a cell is the operator's word), and English text is found even when a value
// in data markup sits inside it.
test("the English sweep ignores a value alone in a cell and still finds a sentence around one", async ({ page }) => {
  await page.setContent('<table><tbody><tr><td><span class="block"><span translate="no">default</span></span></td></tr></tbody></table>');
  await expectNoEnglishLeftovers(page, "a value alone");

  await page.setContent('<p>The command in <span translate="no">shop.example.com</span> failed</p>');
  await expect(expectNoEnglishLeftovers(page, "a sentence around a value")).rejects.toThrow(/terminal\.exec\.failedTitle/);
});
