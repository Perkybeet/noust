/**
 * Screenshots of "Report a problem" for review (@screens, left out of the default run): the help menu, the three
 * steps and the end, and the error screen with the report opened from it; at 1440 and at 390, in the project's
 * theme. On a phone the way in is "More", which is photographed instead of the menu.
 *
 *   NOUST_SCREENS=1 NOUST_SCREENS_DIR=/tmp/informe-screens npx playwright test report-screens
 *
 * The support report is the real one of the seeded server, and the picture is one the page draws for itself, so
 * what is photographed is what a person would see.
 */

import type { Page } from "@playwright/test";
import path from "node:path";

import { expect, settle, signIn, test } from "./fixtures";

const OUT = process.env.NOUST_SCREENS_DIR ?? path.join(import.meta.dirname, "__screens__", "report");
const SIZES = [
  { name: "1440", width: 1440, height: 900 },
  { name: "390", width: 390, height: 844 },
] as const;

async function shot(page: Page, theme: string, size: string, name: string): Promise<void> {
  await settle(page);
  await page.screenshot({ path: path.join(OUT, `${name}-${theme}-${size}.png`) });
}

/** A picture of a page, drawn in the browser: two columns and a table, in stone. */
async function drawnPicture(page: Page): Promise<Buffer> {
  const base64 = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 640;
    canvas.height = 400;
    const context = canvas.getContext("2d");
    if (context === null) return "";
    context.fillStyle = "#e5e8e3";
    context.fillRect(0, 0, 640, 400);
    context.fillStyle = "#c9cec6";
    context.fillRect(0, 0, 90, 400);
    context.fillStyle = "#f4f6f2";
    context.fillRect(120, 70, 480, 290);
    context.fillStyle = "#767f77";
    for (let row = 0; row < 6; row += 1) context.fillRect(140, 100 + row * 40, 440, 2);
    context.fillStyle = "#6a4df5";
    context.fillRect(490, 24, 110, 30);
    return canvas.toDataURL("image/png").split(",")[1] ?? "";
  });
  return Buffer.from(base64, "base64");
}

for (const size of SIZES) {
  test(`@screens report a problem at ${size.name}`, async ({ page, consoleServer }, testInfo) => {
    test.setTimeout(180_000);
    const theme = testInfo.project.name;
    await page.setViewportSize({ width: size.width, height: size.height });
    await page.context().route("https://github.com/**", (route) => route.fulfill({ status: 200, contentType: "text/html", body: "<title>GitHub</title>" }));
    await signIn(page, consoleServer, "/databases");

    // The way in: the help menu in the bar, or "More" on a phone.
    if (size.width >= 1024) {
      await page.getByRole("button", { name: "Help" }).click();
      await expect(page.getByRole("menuitem", { name: "Report a problem" })).toBeVisible();
      await shot(page, theme, size.name, "45-help-menu");
      await page.getByRole("menuitem", { name: "Report a problem" }).click();
    } else {
      await page.getByRole("button", { name: "More", exact: true }).click();
      await expect(page.getByRole("button", { name: "Report a problem" })).toBeVisible();
      await shot(page, theme, size.name, "45-more");
      await page.getByRole("button", { name: "Report a problem" }).click();
    }
    const dialog = page.getByRole("dialog", { name: "Report a problem" });
    await expect(dialog).toBeVisible();

    // Step 1.
    await dialog.getByRole("textbox", { name: "Title" }).fill("The table stays grey when I open Data");
    await dialog.getByRole("textbox", { name: "What happened" }).fill("Opening Data of shop_prod the table stays grey and never loads. The console of the browser shows an error.");
    await dialog.getByRole("textbox", { name: "Steps to make it happen again" }).fill("1. Open Databases and enter shop_prod.\n2. Press Data in the column.");
    await dialog.locator('input[type="file"]').setInputFiles({ name: "capture-1.png", mimeType: "image/png", buffer: await drawnPicture(page) });
    await expect(dialog.getByText("capture-1.png")).toBeVisible();
    await shot(page, theme, size.name, "40-what-happened");

    // Step 2, with the support report open.
    await dialog.getByRole("button", { name: "Next: what is sent" }).click();
    const parts = dialog.getByRole("list", { name: "Parts of the file" });
    await expect(parts.getByRole("button", { name: /support-report\.md/ })).toBeVisible({ timeout: 30_000 });
    await parts.getByRole("button", { name: /support-report\.md/ }).click();
    await expect(dialog.getByRole("region", { name: "support-report.md" })).toBeVisible();
    await shot(page, theme, size.name, "41-what-is-sent");

    // Step 3.
    await dialog.getByRole("button", { name: "Next: on GitHub" }).click();
    await expect(dialog.getByText("A file is downloaded")).toBeVisible();
    await shot(page, theme, size.name, "42-on-github");

    // The end.
    await dialog.getByRole("button", { name: "Download and open GitHub" }).click();
    await expect(dialog.getByText("GitHub was opened in another tab, with the form filled in")).toBeVisible();
    await shot(page, theme, size.name, "43-done");
  });

  test(`@screens the error screen and its report at ${size.name}`, async ({ page, consoleServer, problems }, testInfo) => {
    problems.expect(/A page failed to render|Cannot read|apps/);
    const theme = testInfo.project.name;
    await page.setViewportSize({ width: size.width, height: size.height });
    await signIn(page, consoleServer, "/apps");
    await page.route("**/api/apps*", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ total: 1, apps: [{ domain: 5, name: null }] }) }));
    await page.goto("/apps");
    await expect(page.getByRole("heading", { level: 1, name: "This page broke" })).toBeVisible();
    await shot(page, theme, size.name, "44-error-screen");

    await page.getByRole("button", { name: "Report a problem" }).click();
    await expect(page.getByRole("dialog", { name: "Report a problem" })).toBeVisible();
    await shot(page, theme, size.name, "44-error-report");
  });
}
