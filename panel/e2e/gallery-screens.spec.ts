/**
 * Pictures of the design gallery (/__design), section by section, for a person to review: one file per
 * section of the dry-stone kit, each showing both themes side by side (the gallery pins each panel), and one
 * of the whole page in each theme. Not part of the default run (tag @screens, so `npm run e2e:screens`),
 * and only with NOUST_KIT_SHOTS=<directory>, since the gallery is only in a development build:
 *
 *   NODE_ENV=development npx vite build --mode development --outDir /tmp/noust-dev-build
 *   NOUST_KIT_SHOTS=/tmp/kit NOUST_E2E_STATIC=/tmp/noust-dev-build NOUST_SCREENS=1 npx playwright test gallery-screens --project=light
 */

import path from "node:path";

import { expect, settle, signIn, test } from "./fixtures";

const OUT = process.env.NOUST_KIT_SHOTS;
const SECTIONS = ["stone-heights", "stone-type", "stone-controls", "stone-surfaces", "stone-motion", "stone-fleet", "stone-logos"] as const;

test.describe("the design gallery, in pictures @screens", () => {
  test.skip(OUT === undefined, "NOUST_KIT_SHOTS names the directory the pictures are written to.");

  for (const width of [1440, 390]) {
    test(`takes every section of the kit at ${String(width)}`, async ({ page, consoleServer }) => {
      test.setTimeout(180_000);
      await page.setViewportSize({ width, height: 1000 });
      await signIn(page, consoleServer);
      await page.goto("/__design");
      const gallery = page.locator('nav[aria-label="Design system"]');
      const present = await gallery
        .waitFor({ state: "attached", timeout: 5_000 })
        .then(() => true)
        .catch(() => false);
      test.skip(!present, "The gallery is only in a development build.");
      await settle(page);
      for (const id of SECTIONS) {
        const section = page.locator(`#${id}`);
        await section.scrollIntoViewIfNeeded();
        await expect(section).toBeVisible();
        await section.screenshot({ path: path.join(OUT ?? ".", `${id}-${String(width)}.png`) });
      }
    });
  }
});
