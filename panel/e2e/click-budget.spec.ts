/**
 * The click budgets, measured in a browser against the real backend (checklist M-10, M-12, M-16, M-19, M-31, M-61).
 *
 * A click is anything that activates a control: a pointer press and release, or Enter on a
 * button. Every one the page receives is counted from the moment the operator is on the list
 * they start from; typing is not a click, and neither is the sudo confirmation a local path
 * needs (a public repository does not, so the session is put in sudo mode first and the flow is
 * counted as if it had been a URL). The server has a GitHub App; a public repository is read
 * on a server without one, so its status is answered "not configured" the way a fresh
 * server's is.
 *
 * Budgets, from the Applications list (Coolify's, counted the same way from a project's page,
 * are 5 and 3):
 *   - a public repository to its first build: 4 (New application, Inspect source, Continue, Deploy);
 *   - a recipe to its first build: 3 (From a recipe, its card, Deploy);
 *   - the daily verbs of an application: Restart 1, Roll back 1 (to open its chooser), Stop 2
 *     (it asks first);
 *   - the logs of an application: the last deploy's build log 2 (the Deployments tab, then the deploy)
 *     and the log of what runs 1 (the Logs tab), on a desk and on a phone (M-16);
 *   - the same verbs on a phone (390 px), where the header keeps them on the surface as it does on a
 *     desk: Restart 1, Roll back 1, and the last lines of the application's log 1 (its tab), because a
 *     phone is where an incident is met away from a desk (M-61).
 *
 * The flows are walked by what is on screen, not by the steps there are, so the same spec runs
 * against the build before this change (`NOUST_E2E_STATIC=src/noust/web/static` at 3.3.1) with
 * `NOUST_E2E_MEASURE=1`, which reports the count without asserting the budget.
 */

import type { Page } from "@playwright/test";

import { csrf, forgetApp } from "./apps-cleanup";
import { expect, settle, signIn, test } from "./fixtures";
import type { ConsoleServer } from "./fixtures";
import { wizardSource } from "./wizard-sources";

/** Only measures: reports the count and leaves the budget unasserted (to run on the old build). */
const MEASURE_ONLY = process.env.NOUST_E2E_MEASURE === "1";

declare global {
  interface Window {
    __noustClicks?: number;
  }
}

/** Counts every activation the page receives, from this document on. */
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

async function startCounting(page: Page): Promise<void> {
  await page.evaluate(() => {
    window.__noustClicks = 0;
  });
}

async function clicks(page: Page): Promise<number> {
  return page.evaluate(() => window.__noustClicks ?? 0);
}

/** Says the measured number in the report, whatever the budget. */
function report(name: string, count: number): void {
  test.info().annotations.push({ type: "clicks", description: `${name}: ${String(count)}` });
  console.log(`CLICKS ${name}: ${String(count)}`);
}

function withinBudget(name: string, count: number, budget: number): void {
  report(name, count);
  if (!MEASURE_ONLY) expect(count, `${name} takes ${String(count)} clicks; the budget is ${String(budget)}`).toBeLessThanOrEqual(budget);
}

/** Sudo mode without a click: a public repository never asks, a local directory does. */
async function elevate(page: Page, server: ConsoleServer): Promise<void> {
  const elevated = await page.request.post("/api/auth/elevate", { data: { code: server.secondFactor() }, headers: await csrf(page) });
  expect(elevated.ok(), await elevated.text()).toBe(true);
}

/** A server without a GitHub App, which is where a public repository is typed as a URL. */
async function withoutGitHubApp(page: Page): Promise<void> {
  await page.route("**/api/integrations/github", (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    return route.fulfill({ json: { configured: false, installations: [], hooks_url: null, hooks_active: false } });
  });
}

/** The wizard's step on screen, by its title. */
function stepTitle(page: Page) {
  return page.locator('[data-template="wizard"] [data-slot="step"] h2');
}

/**
 * Goes on with the wizard's Continue (or its Database answer) until the Deploy step, counting
 * each press: the steps there are decide the number, not this walk.
 */
async function continueToDeploy(page: Page): Promise<void> {
  for (let guard = 0; guard < 8; guard += 1) {
    const title = (await stepTitle(page).textContent()) ?? "";
    if (title === "Deploy") return;
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await expect(stepTitle(page)).not.toHaveText(title);
  }
  throw new Error("the wizard never reached its Deploy step");
}

test("a public repository goes from the Applications list to its first build within four clicks", async ({ page, consoleServer }) => {
  test.setTimeout(240_000);
  const domain = "budget-landing.example.net";
  await countClicks(page);
  await withoutGitHubApp(page);
  await signIn(page, consoleServer, "/apps");
  await expect(page.getByRole("heading", { level: 1, name: "Applications" })).toBeVisible();
  await elevate(page, consoleServer);
  await settle(page);

  await startCounting(page);
  await page.getByRole("link", { name: "New application", exact: true }).click();
  await page.getByLabel("Repository or directory").fill(await wizardSource(page, "landing"));
  await page.getByRole("button", { name: "Inspect source" }).click();
  await expect(stepTitle(page)).toHaveText("Address");
  await page.getByLabel("Domain", { exact: true }).fill(domain);
  await continueToDeploy(page);
  const queued = page.waitForRequest((request) => request.url().endsWith("/api/apps") && request.method() === "POST");
  await page.getByRole("button", { name: `Deploy ${domain}` }).click();
  await queued;
  withinBudget("a public repository, from the Applications list to Deploy", await clicks(page), 4);

  await expect(page).toHaveURL(new RegExp(`/apps/${domain.replace(/\./g, "\\.")}/deployments/\\d+$`), { timeout: 30_000 });
  await forgetApp(page, consoleServer, domain);
});

test("a recipe goes from the Applications list to its first build within three clicks", async ({ page, consoleServer }) => {
  test.setTimeout(300_000);
  const domain = "budget-wordpress.example.net";
  await countClicks(page);
  await signIn(page, consoleServer, "/apps");
  await expect(page.getByRole("heading", { level: 1, name: "Applications" })).toBeVisible();
  await settle(page);

  await startCounting(page);
  // Where the recipes are: one link on the list since this change; before it, the wizard's
  // "From a recipe" choice, a click after "New application".
  const direct = page.getByRole("link", { name: "From a recipe" });
  if ((await direct.count()) > 0) {
    await direct.click();
  } else {
    await page.getByRole("link", { name: "New application", exact: true }).click();
    await page.getByRole("radiogroup", { name: "Where the code is" }).getByRole("radio", { name: "From a recipe" }).click();
  }
  await page.getByRole("button", { name: "Use WordPress" }).click();
  await expect(stepTitle(page)).toBeVisible();
  await page.getByLabel("Domain", { exact: true }).fill(domain);
  // The recipe's own Deploy, or (before) the Continue and Variables steps that led to it.
  const lastStep = page.getByRole("button", { name: /^Deploy( \S+)?$/ });
  for (let guard = 0; guard < 6 && (await lastStep.count()) === 0; guard += 1) {
    const title = (await stepTitle(page).textContent()) ?? "";
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await expect(stepTitle(page)).not.toHaveText(title);
  }
  const queued = page.waitForRequest((request) => request.url().endsWith("/api/apps") && request.method() === "POST");
  await lastStep.click();
  await queued;
  withinBudget("a recipe, from the Applications list to Deploy", await clicks(page), 3);

  await expect(page.getByText(`${domain} is deployed`)).toBeVisible({ timeout: 120_000 });
  await forgetApp(page, consoleServer, domain);
});

test.describe("an application's daily verbs", () => {
  const DOMAIN = "shop.example.net";

  function header(page: Page) {
    return page.locator("main header").filter({ has: page.getByRole("heading", { level: 1 }) });
  }

  /** Presses a verb of the header: on the surface, or (before this change) through the menu. */
  async function press(page: Page, verb: string, inMenu: string): Promise<void> {
    const direct = header(page).getByRole("button", { name: verb, exact: true });
    if ((await direct.count()) > 0) {
      await direct.click();
      return;
    }
    await header(page).getByRole("button", { name: "More actions" }).click();
    await page.getByRole("menuitem", { name: inMenu, exact: true }).click();
  }

  test("Restart is one click, Roll back one to reach its chooser, Stop two because it asks first", async ({ page, consoleServer }) => {
    await countClicks(page);
    await signIn(page, consoleServer, `/apps/${DOMAIN}`);
    await expect(header(page).getByRole("button", { name: "Update", exact: true })).toBeVisible();
    await settle(page);

    await startCounting(page);
    await press(page, "Roll back", "Roll back…");
    const chooser = page.getByRole("alertdialog", { name: `Roll back ${DOMAIN}` });
    await expect(chooser).toBeVisible();
    withinBudget("Roll back, to open its chooser", await clicks(page), 1);
    await page.keyboard.press("Escape");
    await expect(chooser).toBeHidden();

    await startCounting(page);
    const restarted = page.waitForResponse((response) => response.url().endsWith(`/api/apps/${DOMAIN}/restart`) && response.request().method() === "POST");
    await press(page, "Restart", "Restart");
    await restarted;
    withinBudget("Restart", await clicks(page), 1);

    await startCounting(page);
    await press(page, "Stop", "Stop");
    const confirm = page.getByRole("alertdialog", { name: `Stop ${DOMAIN}?` });
    await expect(confirm).toBeVisible();
    await confirm.getByRole("button", { name: "Stop application" }).click();
    await expect(confirm).toBeHidden();
    withinBudget("Stop, with its confirmation", await clicks(page), 2);

    // Started again, so the worker's machine is as it was for the next test.
    await expect(header(page).locator("[data-state]").first()).toHaveAttribute("data-state", "stopped");
    await press(page, "Start", "Start");
    await expect(header(page).locator("[data-state]").first()).toHaveAttribute("data-state", "running");
  });
});

test.describe("an application's logs", () => {
  const APP = "tienda.example.org";

  test("the build log of the last deploy is two clicks from the application, the log of what runs one", async ({ page, consoleServer }) => {
    await countClicks(page);
    await signIn(page, consoleServer, `/apps/${APP}`);
    await expect(page.getByRole("heading", { level: 1, name: APP })).toBeVisible();
    await settle(page);

    await startCounting(page);
    await page.getByRole("navigation", { name: "Application sections" }).getByRole("link", { name: "Deployments" }).click();
    const table = page.getByRole("region", { name: `Deploys of ${APP}, newest first` });
    await table.getByRole("button", { name: /^Deployment \d+$/ }).first().click();
    await expect(page.getByRole("region", { name: /^Build log of deployment \d+ of / })).toBeVisible();
    withinBudget("the build log of the last deploy", await clicks(page), 2);

    await page.goto(`/apps/${APP}`);
    await expect(page.getByRole("heading", { level: 1, name: APP })).toBeVisible();
    await settle(page);
    await startCounting(page);
    await page.getByRole("navigation", { name: "Application sections" }).getByRole("link", { name: "Logs" }).click();
    await expect(page.getByRole("region", { name: `Logs of ${APP}` })).toBeVisible();
    withinBudget("the log of what runs", await clicks(page), 1);
  });
});

test.describe("on a phone, the incident verbs", () => {
  const DOMAIN = "shop.example.net";

  test.use({ viewport: { width: 390, height: 844 } });

  function header(page: Page) {
    return page.locator("main header").filter({ has: page.getByRole("heading", { level: 1 }) });
  }

  test("a public repository goes from the Applications list to its first build within four taps", async ({ page, consoleServer }) => {
    test.setTimeout(240_000);
    const domain = "budget-phone.example.net";
    await countClicks(page);
    await withoutGitHubApp(page);
    await signIn(page, consoleServer, "/apps");
    await expect(page.getByRole("heading", { level: 1, name: "Applications" })).toBeVisible();
    await elevate(page, consoleServer);
    await settle(page);

    await startCounting(page);
    await page.getByRole("link", { name: "New application", exact: true }).click();
    await page.getByLabel("Repository or directory").fill(await wizardSource(page, "landing"));
    await page.getByRole("button", { name: "Inspect source" }).click();
    await expect(stepTitle(page)).toHaveText("Address");
    await page.getByLabel("Domain", { exact: true }).fill(domain);
    await continueToDeploy(page);
    const queued = page.waitForRequest((request) => request.url().endsWith("/api/apps") && request.method() === "POST");
    await page.getByRole("button", { name: `Deploy ${domain}` }).click();
    await queued;
    withinBudget("a public repository on a phone, from the Applications list to Deploy", await clicks(page), 4);

    await expect(page).toHaveURL(new RegExp(`/apps/${domain.replace(/\./g, "\\.")}/deployments/\\d+$`), { timeout: 30_000 });
    await forgetApp(page, consoleServer, domain);
  });

  test("the state is on screen, Restart and Roll back take one tap each and the last lines of the log one", async ({ page, consoleServer }) => {
    await countClicks(page);
    await signIn(page, consoleServer, `/apps/${DOMAIN}`);
    await expect(header(page).getByRole("heading", { level: 1, name: DOMAIN })).toBeVisible();
    await settle(page);
    // The state needs no tap at all: it is in the header as soon as the page is.
    await expect(header(page).locator("[data-state]").first()).toBeVisible();

    await startCounting(page);
    await header(page).getByRole("button", { name: "Roll back", exact: true }).click();
    const chooser = page.getByRole("alertdialog", { name: `Roll back ${DOMAIN}` });
    await expect(chooser).toBeVisible();
    withinBudget("Roll back on a phone, to open its chooser", await clicks(page), 1);
    await page.keyboard.press("Escape");
    await expect(chooser).toBeHidden();

    await startCounting(page);
    const restarted = page.waitForResponse((response) => response.url().endsWith(`/api/apps/${DOMAIN}/restart`) && response.request().method() === "POST");
    await header(page).getByRole("button", { name: "Restart", exact: true }).click();
    await restarted;
    withinBudget("Restart on a phone", await clicks(page), 1);

    await startCounting(page);
    await page.getByRole("navigation", { name: "Application sections" }).getByRole("link", { name: "Logs" }).click();
    await expect(page.getByRole("region", { name: `Logs of ${DOMAIN}` })).toBeVisible();
    withinBudget("the last lines of the log on a phone", await clicks(page), 1);
  });
});
