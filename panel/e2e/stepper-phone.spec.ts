/**
 * The step in hand reads whole at 390px (docs/DESIGN.md, T5). The label of the current step of a dialog's stepper was squeezed between its
 * marker and the next one and drawn over it, or wrapped to eight lines of one word each: the markers and the label overlapped. Now every
 * step on a phone is its marker and the step in hand is written under the row, over the width of the dialog.
 *
 * Read in the design gallery, which is only in a development build (see gallery-screens.spec.ts); the test skips where there is none.
 */

import { expect, settle, signIn, test } from "./fixtures";

const PHONE = { width: 390, height: 900 };

test.describe("the stepper on a phone", () => {
  test("shows the markers apart and the step in hand whole, at the first, the middle and the last step", async ({ page, consoleServer }) => {
    await page.setViewportSize(PHONE);
    await signIn(page, consoleServer);
    await page.goto("/__design");
    const present = await page
      .locator('nav[aria-label="Design system"]')
      .waitFor({ state: "attached", timeout: 5_000 })
      .then(() => true)
      .catch(() => false);
    test.skip(!present, "The gallery is only in a development build.");
    await settle(page);

    const frame = page.locator('[data-template-demo="stepper-phone"]');
    await frame.scrollIntoViewIfNeeded();
    const cases = await frame.locator('ol[aria-label="Steps"]').evaluateAll((lists) =>
      lists.map((list) => {
        const box = list.getBoundingClientRect();
        const markers = [...list.querySelectorAll<HTMLElement>("li > span:first-child > span:first-child, li > button > span:first-child")].map((marker) => marker.getBoundingClientRect());
        const overlap = markers.some((a, index) => markers.slice(index + 1).some((b) => a.left < b.right - 0.5 && b.left < a.right - 0.5));
        const under = list.parentElement?.querySelector<HTMLElement>('[data-slot="stepper-current"]');
        const written = under === null || under === undefined ? "" : getComputedStyle(under, "::after").content.replace(/^"|"$/g, "");
        // The words a screen reader gets ("Authorize the connection: current step") hold the label itself.
        const current = list.querySelector('li[aria-current="step"] .sr-only')?.textContent.replace(/: current step$/, "") ?? "";
        const underBox = under?.getBoundingClientRect();
        return {
          markers: markers.length,
          overlap,
          listFits: list.scrollWidth <= list.clientWidth + 1,
          written,
          current,
          below: underBox !== undefined && underBox.top >= box.bottom - 0.5,
          whole: underBox !== undefined && underBox.right <= box.right + 0.5 && underBox.width > 0,
        };
      }),
    );
    expect(cases).toHaveLength(3);
    for (const entry of cases) {
      expect(entry.markers).toBe(5);
      expect(entry.overlap, "a marker is drawn over another").toBe(false);
      expect(entry.listFits, "the row of markers is wider than its box").toBe(true);
      // The label under the row is the step's own, and the label inside the row is only for a screen reader on a phone.
      expect(entry.written).not.toBe("");
      expect(entry.written).toBe(entry.current.trim());
      expect(entry.below, "the step in hand is not under the markers").toBe(true);
      expect(entry.whole, "the step in hand is wider than its dialog").toBe(true);
    }
  });
});
