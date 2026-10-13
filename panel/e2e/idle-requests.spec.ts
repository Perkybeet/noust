/**
 * A console nobody is touching asks little of the server (checklist M-64). Coolify's panel was
 * measured at about thirty requests a minute doing nothing (Livewire polling), which on a small
 * server is a cost and on a phone is battery. The console is driven by one event stream, so an idle
 * page should poll next to nothing: here the Overview, the Applications list and an application's
 * page are left alone for twenty seconds and the requests they make to the API are counted.
 */

import type { Page } from "@playwright/test";

import { expect, settle, signIn, test } from "./fixtures";

const WATCH_MS = 20_000;

/** API requests a minute an idle page may make; today's few, with room for one more poll. */
const BUDGET_PER_MINUTE = 12;

async function idleRequests(page: Page, path: string): Promise<{ count: number; urls: string[] }> {
  const urls: string[] = [];
  await page.goto(path);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await settle(page);
  page.on("request", (request) => {
    const url = new URL(request.url());
    // The event stream is one connection held open, not a request a minute.
    if (url.pathname.startsWith("/api/") && !url.pathname.endsWith("/events")) urls.push(`${request.method()} ${url.pathname}`);
  });
  await page.waitForTimeout(WATCH_MS);
  return { count: urls.length, urls };
}

for (const path of ["/", "/apps", "/apps/shop.example.net"]) {
  test(`${path} left alone asks the API for little`, async ({ page, consoleServer }) => {
    await signIn(page, consoleServer, "/");
    const { count, urls } = await idleRequests(page, path);
    const perMinute = (count * 60_000) / WATCH_MS;
    console.log(`IDLE ${path}: ${String(count)} requests in ${String(WATCH_MS / 1000)} s (${perMinute.toFixed(1)} a minute): ${urls.join(", ")}`);
    expect(perMinute, `${path} made ${String(count)} requests in ${String(WATCH_MS / 1000)} s while nobody touched it:\n${urls.join("\n")}`).toBeLessThanOrEqual(BUDGET_PER_MINUTE);
  });
}
