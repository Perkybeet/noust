/**
 * What a field is found by, measured on the pixels a browser draws (docs/DESIGN.md A-10, WCAG 1.4.11). A field
 * has no line of its own: the edge of its hollow has 3:1 against what is around it. styles/tokens.test.ts holds
 * the two tokens; this holds that the box-shadow really paints them, at the top inside (the shadow) and under
 * the box (the lit lip), by reading the colour of the rows of a screenshot of a field in the gallery, in both
 * themes. In light the shadow is the strong edge, in dark the lip, since nothing is darker than the dark page.
 *
 * It reads the gallery (/__design), which only a development build includes (see design-contract.spec.ts):
 *
 *   NODE_ENV=development npx vite build --mode development --outDir /tmp/noust-dev-build
 *   NOUST_E2E_STATIC=/tmp/noust-dev-build npx playwright test field-edge
 */

import type { Locator, Page } from "@playwright/test";

import { expect, settle, signIn, test } from "./fixtures";

async function openGallery(page: Page, server: Parameters<typeof signIn>[1]): Promise<void> {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await signIn(page, server);
  await page.goto("/__design");
  const present = await page
    .locator('nav[aria-label="Design system"]')
    .waitFor({ state: "attached", timeout: 5_000 })
    .then(() => true)
    .catch(() => false);
  test.skip(!present, "The gallery is only in a development build (see the header of this file).");
  await settle(page);
}

interface Sample {
  /** The colour of the stone around the field, a few pixels above it. */
  around: string;
  /** The first two rows inside the top of the box: where the shadow of the hollow is. */
  top: string[];
  /** The row just under the box: where its lit lip is. */
  lip: string;
}

/** The colours at the edges of a box, read from a screenshot of it inside the page, by the page's own canvas. */
async function sampleEdges(page: Page, box: Locator): Promise<Sample> {
  const frame = await box.boundingBox();
  if (frame === null) throw new Error("the field is not visible");
  const clip = { x: Math.floor(frame.x) - 6, y: Math.floor(frame.y) - 8, width: Math.ceil(frame.width) + 12, height: Math.ceil(frame.height) + 16 };
  const png = await page.screenshot({ clip });
  return page.evaluate(
    async ({ data, inset, width, height }) => {
      const bitmap = await createImageBitmap(new Blob([Uint8Array.from(atob(data), (c) => c.charCodeAt(0))], { type: "image/png" }));
      const canvas = document.createElement("canvas");
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      const context = canvas.getContext("2d", { willReadFrequently: true });
      if (!context) throw new Error("no 2d context");
      context.drawImage(bitmap, 0, 0);
      const hex = (x: number, y: number): string => {
        const [r = 0, g = 0, b = 0] = context.getImageData(x, y, 1, 1).data;
        return `#${[r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("")}`;
      };
      // Away from the rounded corners: the middle of the box.
      const x = Math.round(inset.x + width / 2);
      return {
        around: hex(x, Math.max(0, inset.y - 4)),
        top: [hex(x, inset.y), hex(x, inset.y + 1)],
        lip: hex(x, inset.y + height),
      };
    },
    { data: png.toString("base64"), inset: { x: 6, y: 8 }, width: Math.ceil(frame.width), height: Math.ceil(frame.height) },
  );
}

/** WCAG 2.x contrast of two #rrggbb colours. */
function contrast(a: string, b: string): number {
  const luminance = (hex: string): number => {
    const [r = 0, g = 0, bl = 0] = [1, 3, 5].map((i) => {
      const channel = Number.parseInt(hex.slice(i, i + 2), 16) / 255;
      return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl;
  };
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

for (const theme of ["light", "dark"] as const) {
  test.describe(`a field in the ${theme} theme`, () => {
    test("is found by the strong edge of its hollow: 3:1 against what is around it (A-10)", async ({ page, consoleServer }) => {
      await openGallery(page, consoleServer);
      const panel = page.locator(`#stone-controls [data-theme="${theme}"]`).filter({ has: page.getByLabel("Name") }).first();
      const field = panel.getByLabel("Name").first().locator('xpath=ancestor::div[contains(@class, "elev-carved")][1]');
      await field.scrollIntoViewIfNeeded();
      await page.mouse.move(0, 0);
      const seen = await sampleEdges(page, field);
      const shadow = Math.max(...seen.top.map((colour) => contrast(colour, seen.around)));
      const lip = contrast(seen.lip, seen.around);
      // The strong edge of each theme is the one that can be: the shadow in light, the lit lip in dark.
      const strong = theme === "light" ? shadow : lip;
      expect(strong, `${theme}: the edge is ${theme === "light" ? seen.top.join(" and ") : seen.lip} against ${seen.around}`).toBeGreaterThanOrEqual(3);
      // And the other one is the soft edge of the identity, never a line: a field is a hollow, not a box with a border.
      const soft = theme === "light" ? lip : shadow;
      expect(soft, "the other edge stays soft").toBeLessThan(3);
    });

    test("a switch's track and a checkbox have it too", async ({ page, consoleServer }) => {
      await openGallery(page, consoleServer);
      const panel = page.locator(`#stone-controls [data-theme="${theme}"]`).filter({ has: page.getByLabel("Name") }).first();
      for (const control of [panel.getByRole("switch", { name: "Previews" }), panel.getByRole("checkbox", { name: "Back up before updating" })]) {
        await control.scrollIntoViewIfNeeded();
        await page.mouse.move(0, 0);
        const seen = await sampleEdges(page, control);
        const strong = theme === "light" ? Math.max(...seen.top.map((colour) => contrast(colour, seen.around))) : contrast(seen.lip, seen.around);
        expect(strong, `${theme}: ${(await control.getAttribute("role")) ?? "control"}`).toBeGreaterThanOrEqual(3);
      }
    });
  });
}
