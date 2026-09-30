/**
 * Screenshots of the Server area for review: every tab and view, the service pages and the
 * dialogs that decide something, at 1440, 1920 and 390, in both themes (the light and dark
 * projects) and in English and Spanish. Tagged @screens, so only `NOUST_SCREENS=1` runs it;
 * the files go to $NOUST_SCREENS_DIR/<theme>/ (e2e/__screens__ by default).
 */

import type { Page } from "@playwright/test";
import path from "node:path";

import { expect, settle, test } from "./fixtures";
import type { ConsoleServer } from "./fixtures";
import { useSpanish } from "./spanish";

const OUT = process.env.NOUST_SCREENS_DIR ?? path.join(import.meta.dirname, "__screens__");

const SIZES: readonly { suffix: string; size: { width: number; height: number } }[] = [
  { suffix: "desktop", size: { width: 1440, height: 900 } },
  { suffix: "wide", size: { width: 1920, height: 1080 } },
  { suffix: "mobile", size: { width: 390, height: 844 } },
];

const PAGES: readonly { name: string; path: string }[] = [
  { name: "server-overview", path: "/server" },
  { name: "server-updates", path: "/server/updates" },
  { name: "server-security", path: "/server/security" },
  { name: "server-security-ssh", path: "/server/security?view=ssh" },
  { name: "server-security-firewall", path: "/server/security?view=firewall" },
  { name: "server-security-bans", path: "/server/security?view=bans" },
  { name: "server-storage", path: "/server/storage" },
  { name: "server-services", path: "/server/services" },
  { name: "server-logs", path: "/server/logs" },
  { name: "server-system", path: "/server/system" },
  { name: "server-system-network", path: "/server/system?view=network" },
  { name: "server-system-monitor", path: "/server/system?view=monitor" },
  { name: "service", path: "/server/services/wasm-shop-example-net" },
  { name: "service-logs", path: "/server/services/wasm-shop-example-net/logs" },
  { name: "service-unit", path: "/server/services/wasm-shop-example-net/unit" },
];

/**
 * Signs in through the API rather than the sign-in page: these captures are about the Server
 * area, and should not depend on what the sign-in page is doing this week.
 */
async function signInDirect(page: Page, server: ConsoleServer, next: string): Promise<void> {
  const secondFactor = server.totpSecret !== null ? { totp_code: server.secondFactor() } : {};
  const answer = await page.request.post("/api/auth/login", { data: { token: server.token, ...secondFactor } });
  expect(answer.ok(), await answer.text()).toBe(true);
  await page.goto(next);
}

/** The first screen of each size: what the operator sees before scrolling. */
async function capture(page: Page, dir: string, name: string): Promise<void> {
  for (const { suffix, size } of SIZES) {
    await page.setViewportSize(size);
    await settle(page);
    await page.screenshot({ path: path.join(dir, `${name}-${suffix}.png`) });
    await page.screenshot({ path: path.join(dir, `${name}-${suffix}-full.png`), fullPage: true });
  }
}

for (const language of ["en", "es"] as const) {
  test.describe(`server screens, ${language} @screens`, () => {
    for (const target of PAGES) {
      test(target.name, async ({ page, consoleServer }, testInfo) => {
        const dir = path.join(OUT, testInfo.project.name, language);
        await page.setViewportSize({ width: 1440, height: 900 });
        if (language === "es") await useSpanish(page);
        await signInDirect(page, consoleServer, target.path);
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
        await capture(page, dir, target.name);
      });
    }

    test("dialogs", async ({ page, consoleServer }, testInfo) => {
      const dir = path.join(OUT, testInfo.project.name, language);
      await page.setViewportSize({ width: 1440, height: 900 });
      if (language === "es") await useSpanish(page);
      await signInDirect(page, consoleServer, "/server/updates");
      await page.getByRole("button", { name: language === "es" ? /^Instalar las de seguridad/ : /^Install security updates/ }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await settle(page);
      await page.screenshot({ path: path.join(dir, "dialog-install.png") });
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: language === "es" ? "Reiniciar" : "Reboot", exact: true }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await settle(page);
      await page.screenshot({ path: path.join(dir, "dialog-reboot.png") });
      await page.keyboard.press("Escape");
      await page.goto("/server/security");
      const fix = page.getByRole("button", { name: language === "es" ? "Arreglar…" : "Fix…" }).first();
      await fix.click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await settle(page);
      await page.screenshot({ path: path.join(dir, "dialog-fix.png") });
    });
  });
}
