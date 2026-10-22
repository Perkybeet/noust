/**
 * The first run of a server with nothing on it, against the real backend started with `--empty`
 * (no applications, no accounts, an address of its own so a temporary name can be made): from the
 * link the installer prints to a page that is live, in four clicks (Sign in, Create administrator,
 * Set up later, Try a demo), with axe on every screen it crosses. Runs once per theme.
 *
 * The server is started for each test and stopped after it: what the road does (an account, an
 * application) is what the next run would find.
 */

import type { Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, startConsoleServer, test } from "./fixtures";
import type { RunningServer } from "./fixtures";

declare global {
  interface Window {
    __noustClicks?: number;
  }
}

/** Counts every activation the page receives, from this document on (as click-budget.spec.ts does). */
async function countClicks(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.__noustClicks = 0;
    document.addEventListener(
      "click",
      (event) => {
        if (event.isTrusted) window.__noustClicks = (window.__noustClicks ?? 0) + 1;
      },
      true,
    );
  });
}

let server: RunningServer;

test.beforeEach(async () => {
  server = await startConsoleServer(["--empty"]);
});

test.afterEach(async ({ page }) => {
  // The page leaves before the server does: a console left open on a server that went away reports
  // the refusals of its event stream and its session probe, which are not the road's.
  await page.goto("about:blank");
  await server.stop();
});

/** The temporary name the demo gets: the recipe's name and the dashed address the sandbox declares. */
const DEMO = "hello.93-184-216-34.sslip.io";

test("from the installer's link to a live page in four clicks, with nothing to read in between", async ({ page, problems }) => {
  test.setTimeout(180_000);
  // The sign-in page asks the server who it is before anyone has signed in.
  problems.expect(/status of 401 .* \/api\/auth\/session$/);
  await countClicks(page);

  // 1. The link the installer prints opens on the access token, focused, with the licence in view.
  await page.goto(`${server.url}/login?with=token`);
  await expect(page.getByRole("heading", { level: 1, name: "Sign in with the access token" })).toBeVisible();
  const token = page.getByLabel("Access token");
  await expect(token).toBeFocused();
  await expect(page.getByText(/© 2024-2026 Yago López Prado/)).toBeVisible();
  await expect(page.getByRole("link", { name: /AGPL-3\.0-or-later/ })).toHaveAttribute("href", "https://github.com/Perkybeet/noust/blob/main/LICENSE");
  await settle(page);
  await expectNoA11yViolations(page, "the access token page");
  await token.fill(server.token);
  await page.getByRole("button", { name: "Sign in" }).click();

  // 2. A server with no accounts: one form, a username and a password.
  await expect(page.getByRole("heading", { level: 1, name: "Create the administrator" })).toBeVisible();
  await page.getByLabel("Username").fill("admin");
  await page.getByLabel("Password", { exact: true }).fill("a-long-password-for-the-first-run");
  await settle(page);
  await expectNoA11yViolations(page, "the first administrator");
  await page.getByRole("button", { name: "Create administrator" }).click();

  // 3. Protect it now or later; later is one press.
  await expect(page.getByRole("heading", { level: 1, name: "Protect your account" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "protecting the account");
  await page.getByRole("button", { name: "Set up later" }).click();

  // The Overview of a server with nothing on it: one card of ways to get something live.
  await expect(page.getByRole("heading", { level: 2, name: "Deploy your first app" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Try a demo" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Use a repository" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Overview sections" }).getByRole("link", { name: "Map" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the Overview of an empty server");

  // 4. The demo: a temporary address, no DNS, deployed without a form.
  await page.getByRole("link", { name: "Try a demo" }).click();
  await expect(page.getByText(`${DEMO} is deployed`)).toBeVisible({ timeout: 120_000 });
  const live = page.getByRole("link", { name: new RegExp(`${DEMO.replace(/\./g, "\\.")}, the live site`) });
  await expect(live).toBeVisible();
  await expect(live).toHaveAttribute("href", /^https?:\/\/hello\.93-184-216-34\.sslip\.io$/);
  await expect(page.locator("#main").getByText("Live", { exact: true })).toBeVisible();
  // The certificate is said, whichever way it went: ready, or served over HTTP with what certbot said.
  await expect(page.getByText(/HTTPS is ready\.|Served over HTTP/)).toBeVisible();
  await expect(page.getByRole("link", { name: "Add your own domain" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Add a database" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Schedule a backup" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the first deploy's success row");

  const clicks = await page.evaluate(() => window.__noustClicks ?? 0);
  test.info().annotations.push({ type: "clicks", description: `first run: ${String(clicks)}` });
  expect(clicks, `the first run takes ${String(clicks)} clicks; the budget is 4`).toBeLessThanOrEqual(4);
});

test.describe("on a phone", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("the same road fits the screen, with no sideways scrolling", async ({ page, problems }) => {
    test.setTimeout(120_000);
    problems.expect(/status of 401 .* \/api\/auth\/session$/);
    const overflows = (): Promise<boolean> => page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);

    await page.goto(`${server.url}/login?with=token`);
    await expect(page.getByLabel("Access token")).toBeFocused();
    await settle(page);
    expect(await overflows(), "the access token page overflows 390px").toBe(false);
    await expectNoA11yViolations(page, "the access token page on a phone");
    await page.getByLabel("Access token").fill(server.token);
    await page.getByRole("button", { name: "Sign in" }).click();

    await expect(page.getByRole("heading", { level: 1, name: "Create the administrator" })).toBeVisible();
    await page.getByLabel("Username").fill("admin");
    await page.getByLabel("Password", { exact: true }).fill("a-long-password-for-the-first-run");
    await settle(page);
    expect(await overflows(), "the first administrator overflows 390px").toBe(false);
    await page.getByRole("button", { name: "Create administrator" }).click();
    await page.getByRole("button", { name: "Set up later" }).click();

    await expect(page.getByRole("heading", { level: 2, name: "Deploy your first app" })).toBeVisible();
    await settle(page);
    expect(await overflows(), "the Overview of an empty server overflows 390px").toBe(false);
    await expectNoA11yViolations(page, "the Overview of an empty server on a phone");
  });
});
