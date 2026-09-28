/**
 * Every route of the console, in both themes, passes axe (WCAG 2.2 AA) as it first renders
 * with the seeded machine's data. The page specs check their own interactions; this is the
 * sweep that makes a page added to routes.ts accessible by default, the way the layout-shift
 * gate makes it stable and the fixtures make it CSP-clean.
 */

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";
import { ROUTES, routePath } from "./routes";

const DESKTOP = { width: 1440, height: 900 };

for (const route of ROUTES) {
  test(`${route.name} has no WCAG 2.2 AA violations`, async ({ page, consoleServer }) => {
    await page.setViewportSize(DESKTOP);
    await signIn(page, consoleServer);
    await page.goto(await routePath(page, route));
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, route.name);
  });
}
