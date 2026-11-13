/**
 * The page of the assistant while it is off and the four steps that turn it on, photographed at 1440 by 900 and at 390 px in both themes
 * for a person to look at (item 171). Tagged @screens: it is slow and writes files, so the default run leaves it out; `NOUST_SHOTS` names
 * the folder. One server of its own where the model runs and the assistant is not yet configured (`--assistant ready`).
 */

import { mkdirSync } from "node:fs";
import path from "node:path";

import type { Page } from "@playwright/test";

import { csrf } from "./apps-cleanup";
import { expect, settle, signIn, startConsoleServer, test } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

const OUT = process.env.NOUST_SHOTS ?? path.join(import.meta.dirname, "..", "test-results", "assistant-screens");

const serial = test.extend<object, { consoleServer: ConsoleServer }>({
  consoleServer: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const server = await startConsoleServer(["--totp", "--backup-codes", "60", "--assistant", "ready"]);
      try {
        await use(server);
      } finally {
        await server.stop();
      }
    },
    { scope: "worker", timeout: 75_000 },
  ],
});
serial.describe.configure({ mode: "serial" });

const SIZES = [
  { name: "1440", width: 1440, height: 900 },
  { name: "390", width: 390, height: 844 },
] as const;

/** One picture of the page as it is, at both sizes; the test goes on at the size it had. */
async function shoot(page: Page, name: string, theme: string): Promise<void> {
  mkdirSync(OUT, { recursive: true });
  const before = page.viewportSize();
  for (const size of SIZES) {
    await page.setViewportSize({ width: size.width, height: size.height });
    await settle(page);
    await page.screenshot({ path: path.join(OUT, `${name}-${size.name}-${theme}.png`) });
  }
  if (before !== null) await page.setViewportSize(before);
}

serial.describe("@screens the assistant off and the four steps that turn it on", () => {
  serial("walks the page from off to the last step", async ({ page, consoleServer }, info) => {
    const theme = info.project.name;
    await page.setViewportSize({ width: 1440, height: 900 });
    await signIn(page, consoleServer, "/settings/assistant");
    expect((await page.request.post("/api/auth/elevate", { data: { code: consoleServer.secondFactor() }, headers: await csrf(page) })).ok()).toBe(true);
    await expect(page.getByRole("link", { name: "Turn on…" })).toBeVisible();
    await shoot(page, "40-apagado", theme);

    await page.getByRole("link", { name: "Turn on…" }).click();
    await expect(page.getByRole("heading", { name: "Where does the model come from?" })).toBeVisible();
    await page.getByRole("radio", { name: /This machine/ }).click();
    await page.getByLabel("Address", { exact: true }).fill(`${String(consoleServer.extras.assistant_model)}/v1`);
    await page.getByLabel("Model", { exact: true }).fill("tiny-model");
    await page.getByRole("checkbox", { name: /I allow this server to call that address/ }).check();
    await shoot(page, "41-modelo", theme);

    const address = String(consoleServer.extras.assistant_model);
    const scripted = await page.request.post(`${address}/__script`, { data: { replies: [{ calls: [{ name: "get_status", arguments: { service: "noust" } }] }] } });
    expect(scripted.ok()).toBe(true);
    await page.getByRole("button", { name: "Test the connection" }).click();
    await expect(page.getByText("With this model it explains, investigates and proposes.")).toBeVisible();
    await shoot(page, "42-modelo-probado", theme);

    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByRole("heading", { name: "What leaves the server" })).toBeVisible();
    await shoot(page, "43-datos-paso", theme);
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByRole("heading", { name: "Limits" })).toBeVisible();
    await shoot(page, "44-limites", theme);
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByRole("heading", { name: "Who may use it" })).toBeVisible();
    await shoot(page, "45-quien", theme);
  });
});
