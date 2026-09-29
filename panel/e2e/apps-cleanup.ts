/**
 * Leaving a worker's machine as it was seeded after a spec created an application: other
 * specs on the same worker count its apps.
 */

import type { Page } from "@playwright/test";

import { expect } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

/** The CSRF header every write through `page.request` carries, mirrored from its cookie. */
export async function csrf(page: Page): Promise<Record<string, string>> {
  const cookie = (await page.context().cookies()).find((entry) => entry.name === "wasm_csrf");
  return cookie ? { "X-WASM-CSRF": cookie.value } : {};
}

/**
 * Waits for every job about `domain` to end, then deletes the application if it exists. A
 * first deploy that failed has removed its app already; one that succeeded is deleted, which
 * needs sudo mode, confirmed here with an unspent second factor.
 */
export async function forgetApp(page: Page, server: ConsoleServer, domain: string): Promise<void> {
  // Nothing on screen may keep asking about the app once it is gone.
  await page.goto("about:blank");
  await expect
    .poll(
      async () => {
        const active = (await (await page.request.get("/api/jobs/active")).json()) as { jobs: { metadata?: { domain?: string } }[] };
        return active.jobs.some((job) => job.metadata?.domain === domain);
      },
      { timeout: 120_000, intervals: [1_000] },
    )
    .toBe(false);
  if ((await page.request.get(`/api/apps/${domain}`)).status() === 404) return;
  const elevated = await page.request.post("/api/auth/elevate", { data: { code: server.secondFactor() }, headers: await csrf(page) });
  expect(elevated.ok(), await elevated.text()).toBe(true);
  const deleted = await page.request.delete(`/api/apps/${domain}?remove_files=true&remove_ssl=true`, { headers: await csrf(page) });
  expect(deleted.ok(), await deleted.text()).toBe(true);
  await expect.poll(async () => (await page.request.get(`/api/apps/${domain}`)).status(), { timeout: 60_000 }).toBe(404);
}
