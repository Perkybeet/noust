/**
 * The click budgets of the console's most frequent tasks, measured in a browser against the real
 * backend (checklist M-10, M-12, M-16, M-19, M-31, M-61; section 7 of the 4.0 navigation design): the
 * fifteen tasks an operator does every day or every week, each walked the way a person does it, with
 * the number of clicks it takes and the depth of the address it ends on held to the table of budgets.ts.
 *
 * A click is anything that activates a control (clicks.ts); typing is not one, and neither is the
 * sudo confirmation a destructive action needs: the session is put in sudo mode first and the flow is
 * counted as if the operator were already in it. The walks go by what is on screen, not by the steps
 * there are, so this same file runs against the build before the change:
 *
 *   NOUST_E2E_STATIC=<3.3.1 build> NOUST_E2E_MEASURE=1 npx playwright test click-budget \
 *     --workers=1 --reporter=list,./e2e/click-table-reporter.ts
 *
 * which reports each count without asserting it, says "no path" for what that console could not do,
 * and prints the table of every task. Where a task changes the machine (a rollback, a variable, a
 * domain), the walk puts it back, so the worker's server is as it was seeded for the next spec.
 *
 * T01, T02a and T03b start from a server with nothing on it (`console_server.py --empty`, the first-run
 * server), T15 from a central with a node; each says so and is skipped, with the reason, on a tree
 * whose console server cannot make that machine.
 */

import type { Page } from "@playwright/test";
import { spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import { csrf, forgetApp } from "./apps-cleanup";
import { BUDGETS, budgetOf, renderTable } from "./budgets";
import { MEASURE_ONLY, clicks, countClicks, depthOf, elevate, inViewport, report, startCounting, withinBudget } from "./clicks";
import { expect, settle, signIn, startConsoleServer, test } from "./fixtures";
import type { ConsoleServer, RunningServer } from "./fixtures";
import { wizardSource } from "./wizard-sources";

const SHOP = "shop.example.net";
const RELEASES_APP = "tienda.example.org";
const LIVE_APP = "pedidos.example.org";
const FAILED_APP = "clientes.example.com";
const SITE_APP = "example.net";
const PHONE = { width: 390, height: 844 };

/** The table of budgets is data a walk indexes by id: a row twice, or an id nobody walks, is a typo. */
test("the table of budgets has one row per task and every row is walked", () => {
  const ids = BUDGETS.map((row) => row.id);
  expect(new Set(ids).size).toBe(ids.length);
  expect(renderTable([{ id: "T09", project: "light", count: 4 }])).toContain("T09");
  const source = readFileSync(path.join(import.meta.dirname, "click-budget.spec.ts"), "utf8");
  for (const id of ids) {
    const walks = source.includes(`walked(page, "${id}"`) || source.includes(`withinBudget("${id}"`);
    expect(walks, `${id} is in the table and no walk says "${id}"`).toBe(true);
  }
});

/**
 * Walks a task and holds it to its budget. In a run that only measures, a walk that cannot be done
 * (the control it presses does not exist in the console under test) is reported as "no path" and does not
 * fail; everywhere else it does.
 *
 * @param page - The page the operator is on, with the counter running.
 * @param id - The task's id in `BUDGETS`.
 * @param walk - Presses what an operator presses.
 * @param name - What the report calls it when it is not the table's name.
 */
async function walked(page: Page, id: string, walk: () => Promise<void>, name?: string): Promise<void> {
  const row = budgetOf(id);
  const said = name ?? row.name;
  try {
    await walk();
  } catch (error) {
    if (!MEASURE_ONLY) throw error;
    report(id, said, null);
    console.log(`NO PATH ${id}: ${(error instanceof Error ? error.message : String(error)).split("\n")[0] ?? ""}`);
    return;
  }
  withinBudget(id, said, await clicks(page), row.budget);
  if (!MEASURE_ONLY) {
    const depth = depthOf(page.url());
    expect(depth, `${id} ends on ${page.url()}, ${String(depth)} segments deep; the budget is ${String(row.depth)}`).toBeLessThanOrEqual(row.depth);
  }
}

/**
 * Opens the page a task starts from, counts from there, and leaves sudo mode set up when the task needs it.
 *
 * @param address - The page, or what finds it once signed in (the id of a seeded deployment is not fixed).
 */
async function startAt(
  page: Page,
  server: ConsoleServer,
  id: string,
  address: string | ((page: Page) => Promise<string>),
  ready: (page: Page) => Promise<void>,
): Promise<void> {
  await countClicks(page);
  if (typeof address === "string") {
    await signIn(page, server, address);
  } else {
    await signIn(page, server, "/");
    await page.goto(await address(page));
  }
  await ready(page);
  await settle(page);
  if (budgetOf(id).sudo === true) await elevate(page, server);
  await startCounting(page);
}

function header(page: Page) {
  return page.locator("main header").filter({ has: page.getByRole("heading", { level: 1 }) });
}

/** Presses a verb of the header: on the surface, or (before 3.2) through the "More actions" menu. */
async function press(page: Page, verb: string, inMenu: string): Promise<void> {
  const direct = header(page).getByRole("button", { name: verb, exact: true });
  if ((await direct.count()) > 0) {
    await direct.click();
    return;
  }
  await header(page).getByRole("button", { name: "More actions" }).click();
  await page.getByRole("menuitem", { name: inMenu, exact: true }).click();
}

function sections(page: Page) {
  return page.getByRole("navigation", { name: "Application sections" });
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
 * Goes on with the wizard's Continue (or its Database answer) until the Deploy step, counting each
 * press: the steps there are decide the number, not this walk.
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

/** The press that reads the source: "Continue" in 4.0, "Inspect source" before it. */
async function readSource(page: Page): Promise<void> {
  const old = page.getByRole("button", { name: "Inspect source" });
  if ((await old.count()) > 0) await old.click();
  else await page.getByRole("button", { name: "Continue", exact: true }).click();
}

/** A public repository from the Applications list to the request that makes the application. */
async function repositoryWalk(page: Page, domain: string): Promise<void> {
  await page.getByRole("link", { name: "New application", exact: true }).click();
  await page.getByLabel("Repository or directory").fill(await wizardSource(page, "landing"));
  await readSource(page);
  await expect(stepTitle(page).first()).toBeVisible();
  // A server with no domain of its own passes the address over (the demo's temporary name); one with them asks.
  if ((await stepTitle(page).textContent()) === "Address") await page.getByLabel("Domain", { exact: true }).fill(domain);
  await continueToDeploy(page);
  const queued = page.waitForRequest((request) => request.url().endsWith("/api/apps") && request.method() === "POST");
  await page.getByRole("button", { name: /^Deploy( \S+)?$/ }).click();
  await queued;
}

test.describe("starting a first application", () => {
  test.setTimeout(240_000);

  test("T02b: a public repository goes from the Applications list to its first build", async ({ page, consoleServer }) => {
    const domain = "budget-landing.example.net";
    await withoutGitHubApp(page);
    await startAt(page, consoleServer, "T02b", "/apps", async () => {
      await expect(page.getByRole("heading", { level: 1, name: "Applications" })).toBeVisible();
      await elevate(page, consoleServer);
    });
    await walked(page, "T02b", () => repositoryWalk(page, domain));
    await expect(page).toHaveURL(new RegExp(`/apps/${domain.replace(/\./g, "\\.")}/deployments/\\d+$`), { timeout: 30_000 });
    await forgetApp(page, consoleServer, domain);
  });

  test("T03a: a recipe goes from the Applications list to its first build", async ({ page, consoleServer }) => {
    test.setTimeout(300_000);
    const domain = "budget-wordpress.example.net";
    await startAt(page, consoleServer, "T03a", "/apps", async () => {
      await expect(page.getByRole("heading", { level: 1, name: "Applications" })).toBeVisible();
    });
    await walked(page, "T03a", async () => {
      // Where the recipes are: a tab of the list, or a link beside it, or (3.3.1) the wizard's own choice.
      const tab = page.locator("main").getByRole("link", { name: "Recipes", exact: true });
      const link = page.getByRole("link", { name: "From a recipe" });
      if ((await tab.count()) > 0) {
        await tab.first().click();
      } else if ((await link.count()) > 0) {
        await link.click();
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
    });
    await expect(page.getByText(`${domain} is deployed`)).toBeVisible({ timeout: 120_000 });
    await forgetApp(page, consoleServer, domain);
  });
});

test.describe("an application's daily verbs", () => {
  test("T08a, T08b, T07o: Restart is one click, Stop two because it asks first, Roll back one to reach its chooser", async ({ page, consoleServer }) => {
    await startAt(page, consoleServer, "T08a", `/apps/${SHOP}`, async () => {
      await expect(header(page).getByRole("button", { name: "Update", exact: true })).toBeVisible();
    });

    await walked(page, "T07o", async () => {
      await press(page, "Roll back", "Roll back…");
      await expect(page.getByRole("alertdialog", { name: `Roll back ${SHOP}` })).toBeVisible();
    });
    await page.keyboard.press("Escape");
    await expect(page.getByRole("alertdialog", { name: `Roll back ${SHOP}` })).toBeHidden();

    await startCounting(page);
    await walked(page, "T08a", async () => {
      const restarted = page.waitForResponse((response) => response.url().endsWith(`/api/apps/${SHOP}/restart`) && response.request().method() === "POST");
      await press(page, "Restart", "Restart");
      await restarted;
    });

    await startCounting(page);
    await walked(page, "T08b", async () => {
      await press(page, "Stop", "Stop");
      const confirm = page.getByRole("alertdialog", { name: `Stop ${SHOP}?` });
      await expect(confirm).toBeVisible();
      await confirm.getByRole("button", { name: "Stop application" }).click();
      await expect(confirm).toBeHidden();
    });

    // Started again, so the worker's machine is as it was for the next test.
    await expect(header(page).locator("[data-state]").first()).toHaveAttribute("data-state", "stopped");
    await press(page, "Start", "Start");
    await expect(header(page).locator("[data-state]").first()).toHaveAttribute("data-state", "running");
  });
});

test.describe("an application's logs", () => {
  test("T04b, T10b: the build log of the last deploy is two clicks from the application, the log of what runs one", async ({ page, consoleServer }) => {
    await startAt(page, consoleServer, "T04b", `/apps/${RELEASES_APP}`, async () => {
      await expect(page.getByRole("heading", { level: 1, name: RELEASES_APP })).toBeVisible();
    });
    await walked(page, "T04b", async () => {
      await sections(page).getByRole("link", { name: "Deployments" }).click();
      const table = page.getByRole("region", { name: `Deploys of ${RELEASES_APP}, newest first` });
      await table.getByRole("button", { name: /^Deployment \d+$/ }).first().click();
      await expect(page.getByRole("region", { name: /^Build log of deployment \d+ of / })).toBeVisible();
    });

    await page.goto(`/apps/${RELEASES_APP}`);
    await expect(page.getByRole("heading", { level: 1, name: RELEASES_APP })).toBeVisible();
    await settle(page);
    await startCounting(page);
    await walked(page, "T10b", async () => {
      await sections(page).getByRole("link", { name: "Logs" }).click();
      await expect(page.getByRole("region", { name: `Logs of ${RELEASES_APP}` })).toBeVisible();
    });
  });

  test("T10: the live log and its errors are two clicks from the application", async ({ page, consoleServer }) => {
    await startAt(page, consoleServer, "T10", `/apps/${RELEASES_APP}`, async () => {
      await expect(page.getByRole("heading", { level: 1, name: RELEASES_APP })).toBeVisible();
    });
    await walked(page, "T10", async () => {
      await sections(page).getByRole("link", { name: "Logs" }).click();
      const journal = page.getByRole("region", { name: `Logs of ${RELEASES_APP}` });
      await expect(journal).toBeVisible();
      const errors = page.getByRole("group", { name: "Filter by level" }).getByRole("button", { name: /^Errors?\b/ });
      await errors.click();
      await expect(errors).toHaveAttribute("aria-pressed", "true");
    });
  });
});

test.describe("on a phone, the incident verbs", () => {
  test.use({ viewport: PHONE });

  test("T02p: a public repository goes from the Applications list to its first build within four taps", async ({ page, consoleServer }) => {
    test.setTimeout(240_000);
    const domain = "budget-phone.example.net";
    await withoutGitHubApp(page);
    await startAt(page, consoleServer, "T02p", "/apps", async () => {
      await expect(page.getByRole("heading", { level: 1, name: "Applications" })).toBeVisible();
      await elevate(page, consoleServer);
    });
    await walked(page, "T02p", () => repositoryWalk(page, domain));
    await expect(page).toHaveURL(new RegExp(`/apps/${domain.replace(/\./g, "\\.")}/deployments/\\d+$`), { timeout: 30_000 });
    await forgetApp(page, consoleServer, domain);
  });

  test("T07p, T08p, T10p: the state is on screen, Restart and Roll back take one tap each and the last lines of the log one", async ({ page, consoleServer }) => {
    await startAt(page, consoleServer, "T07p", `/apps/${SHOP}`, async () => {
      await expect(header(page).getByRole("heading", { level: 1, name: SHOP })).toBeVisible();
      // The state needs no tap at all: it is in the header as soon as the page is.
      await expect(header(page).locator("[data-state]").first()).toBeVisible();
    });
    await walked(page, "T07p", async () => {
      await header(page).getByRole("button", { name: "Roll back", exact: true }).click();
      await expect(page.getByRole("alertdialog", { name: `Roll back ${SHOP}` })).toBeVisible();
    });
    await page.keyboard.press("Escape");
    await expect(page.getByRole("alertdialog", { name: `Roll back ${SHOP}` })).toBeHidden();

    await startCounting(page);
    await walked(page, "T08p", async () => {
      const restarted = page.waitForResponse((response) => response.url().endsWith(`/api/apps/${SHOP}/restart`) && response.request().method() === "POST");
      await header(page).getByRole("button", { name: "Restart", exact: true }).click();
      await restarted;
    });

    await startCounting(page);
    await walked(page, "T10p", async () => {
      await sections(page).getByRole("link", { name: "Logs" }).click();
      await expect(page.getByRole("region", { name: `Logs of ${SHOP}` })).toBeVisible();
    });
  });
});

// ---------------------------------------------------------------------------------------------------
// The life of an application: updating it, a failed deploy, going back, a variable, a domain, a limit.
// ---------------------------------------------------------------------------------------------------

interface Deploy {
  id: number;
  status: string;
  error: string | null;
  git_commit: string | null;
  release_id: string | null;
  rollback_available: boolean;
}

async function deploymentsOf(page: Page, domain: string): Promise<Deploy[]> {
  const response = await page.request.get(`/api/deployments?domain=${domain}&limit=50`);
  return ((await response.json()) as { items: Deploy[] }).items;
}

/** The seeded deploy of 19d3f6e, whose release is on disk: a row a rollback can go back to. */
async function goBackTarget(page: Page, domain: string): Promise<number> {
  const row = (await deploymentsOf(page, domain)).find((item) => item.git_commit === "19d3f6e" && item.release_id !== null && item.rollback_available);
  if (row === undefined) throw new Error(`no seeded deployment of ${domain} can be gone back to`);
  return row.id;
}

async function serving(page: Page, domain: string): Promise<string> {
  const body = (await (await page.request.get(`/api/apps/${domain}/releases`)).json()) as { items: { id: string; active: boolean }[] };
  const active = body.items.find((release) => release.active);
  if (active === undefined) throw new Error(`nothing serves ${domain}`);
  return active.id;
}

/** Waits for every job about `domain` to end: the release is switched before the health gate has said its last word. */
async function jobsEnded(page: Page, domain: string): Promise<void> {
  await expect
    .poll(
      async () => {
        const active = (await (await page.request.get("/api/jobs/active")).json()) as { jobs: { metadata?: { domain?: string } }[] };
        return active.jobs.some((job) => job.metadata?.domain === domain);
      },
      { timeout: 90_000, intervals: [500] },
    )
    .toBe(false);
}

/** Puts the worker's machine back after a rollback: the release that served, and the automatic deploys the rollback paused. */
async function forward(page: Page, domain: string, release: string): Promise<void> {
  await jobsEnded(page, domain);
  const headers = await csrf(page);
  const activated = await page.request.post(`/api/apps/${domain}/releases/${release}/activate`, { headers });
  expect(activated.ok(), await activated.text()).toBe(true);
  const rollout = (await (await page.request.get(`/api/apps/${domain}/rollout`)).json()) as { paused: unknown };
  if (rollout.paused !== null) {
    const resumed = await page.request.post(`/api/apps/${domain}/rollout/resume`, { headers });
    expect(resumed.ok(), await resumed.text()).toBe(true);
  }
}

/** The question every way back opens, and the answer that goes: it names the version it goes back to. */
function rollbackDialog(page: Page, domain: string) {
  return page.getByRole("alertdialog", { name: `Roll back ${domain}` });
}

/**
 * Presses the dialog's answer ("Go back to #N" in 4.0, the dialog's own confirmation before it) and
 * waits for the request that goes back.
 */
async function answerRollback(page: Page, domain: string): Promise<void> {
  const dialog = rollbackDialog(page, domain);
  await expect(dialog).toBeVisible();
  if (!MEASURE_ONLY) {
    // What goes back, what stays and what happens to the automatic deploys are said before anything is pressed.
    await expect(dialog.getByText("What stays as it is", { exact: true })).toBeVisible();
    await expect(dialog.getByText("Automatic deploys", { exact: true })).toBeVisible();
  }
  const sent = page.waitForResponse((response) => /\/(rollback|activate)$/.test(response.url()) && response.request().method() === "POST");
  await dialog.getByRole("button", { name: /^(Go back( to .+)?|Roll back|Confirm)$/ }).first().click();
  await sent;
}

test.describe("an application's updates, failures and ways back", () => {
  test.setTimeout(120_000);

  test("T04: the build is followed live from the application, two clicks after the update starts", async ({ page, consoleServer }) => {
    await startAt(page, consoleServer, "T04", `/apps/${LIVE_APP}`, async () => {
      await expect(header(page).getByRole("button", { name: "Update", exact: true })).toBeVisible();
    });
    await walked(page, "T04", async () => {
      await header(page).getByRole("button", { name: "Update", exact: true }).click();
      // 4.0 offers the build's log beside the job in hand; before it, the way is the Deployments tab and the deploy's row.
      const view = page.getByRole("link", { name: "View log" }).or(page.getByRole("button", { name: "View log" })).first();
      if (await view.waitFor({ timeout: 8_000 }).then(() => true, () => false)) {
        await view.click();
      } else {
        await sections(page).getByRole("link", { name: "Deployments" }).click();
        await page.getByRole("region", { name: new RegExp(`^Deploys of ${LIVE_APP}`) }).getByRole("button", { name: /^Deployment \d+$/ }).first().click();
      }
      await expect(page.getByRole("region", { name: /^Build log of deployment \d+ of / }).or(page.getByRole("dialog"))).toBeVisible();
    });
    await jobsEnded(page, LIVE_APP);
  });

  for (const size of [
    { name: "a desktop", width: 1440, height: 900 },
    { name: "a phone", width: PHONE.width, height: PHONE.height },
  ]) {
    test(`T05: on ${size.name}, one click from the banner shows the first error line and Retry`, async ({ page, consoleServer }) => {
      await page.setViewportSize({ width: size.width, height: size.height });
      // The banner of a failed deploy is for an app that still serves, and the seeded machine has none whose newest
      // deploy failed (its one failed deploy is of an app whose service is down too, which is Diagnose's banner).
      // The app is answered as running; the deploys, the log and the error are the real ones.
      await page.route(`**/api/apps/${FAILED_APP}`, async (route) => {
        if (route.request().method() !== "GET") return route.fallback();
        const response = await route.fetch();
        return route.fulfill({ response, json: { ...((await response.json()) as object), status: "running" } });
      });
      await startAt(page, consoleServer, "T05", `/apps/${FAILED_APP}`, async () => {
        await expect(page.getByRole("heading", { level: 1, name: FAILED_APP })).toBeVisible();
      });
      await walked(page, "T05", async () => {
        const banner = page.locator('[data-variant="banner"]');
        await expect(banner).toBeVisible();
        await banner.getByRole("link", { name: /^View logs?$/ }).click();
        await expect(page).toHaveURL(/\/deployments\/\d+$/);
      });
      if (!MEASURE_ONLY) {
        // The cause and the way to try again are in the first screen, with no scrolling to find them.
        await inViewport(page.locator("pre").filter({ hasText: /npm ERR!/ }).first());
        await inViewport(page.getByRole("button", { name: "Retry", exact: true }));
      }
    });
  }

  test("T06: retrying a failed deploy is one click on its page", async ({ page, consoleServer }) => {
    await startAt(
      page,
      consoleServer,
      "T06",
      async () => {
        const failed = (await deploymentsOf(page, FAILED_APP)).find((item) => item.status === "failed");
        if (failed === undefined) throw new Error(`no seeded failed deployment of ${FAILED_APP}`);
        return `/apps/${FAILED_APP}/deployments/${String(failed.id)}`;
      },
      async () => {
        await expect(page.getByRole("heading", { level: 1, name: FAILED_APP })).toBeVisible();
      },
    );
    await walked(page, "T06", async () => {
      // "Retry" is the same call as "Rebuild" (POST .../rebuild); before it, Rebuild was in a menu and asked first.
      const queued = page.waitForRequest((request) => request.url().endsWith("/rebuild") && request.method() === "POST");
      const retry = page.getByRole("button", { name: "Retry", exact: true });
      if ((await retry.count()) > 0) {
        await retry.click();
      } else {
        await page.getByRole("button", { name: "More actions" }).click();
        await page.getByRole("menuitem", { name: /^Rebuild/ }).click();
        await page.getByRole("alertdialog").getByRole("button", { name: /^Rebuild/ }).click();
      }
      await queued;
    });
    await jobsEnded(page, FAILED_APP);
  });

  test("T07a: going back from the header is a click to ask and a click to go, with the consequences said between", async ({ page, consoleServer }) => {
    await startAt(page, consoleServer, "T07a", `/apps/${RELEASES_APP}`, async () => {
      await expect(header(page).getByRole("button", { name: "Update", exact: true })).toBeVisible();
    });
    const before = await serving(page, RELEASES_APP);
    await walked(page, "T07a", async () => {
      await press(page, "Roll back", "Roll back…");
      await answerRollback(page, RELEASES_APP);
    });
    await forward(page, RELEASES_APP, before);
  });

  test("T07b: going back from a deployment's page is two clicks", async ({ page, consoleServer }) => {
    await startAt(
      page,
      consoleServer,
      "T07b",
      async () => `/apps/${RELEASES_APP}/deployments/${String(await goBackTarget(page, RELEASES_APP))}`,
      async () => {
        await expect(page.getByRole("heading", { level: 1, name: RELEASES_APP })).toBeVisible();
      },
    );
    const before = await serving(page, RELEASES_APP);
    await walked(page, "T07b", async () => {
      const direct = page.getByRole("button", { name: "Go back to this version" });
      if ((await direct.count()) > 0) {
        await direct.click();
      } else {
        await page.getByRole("button", { name: "More actions" }).click();
        await page.getByRole("menuitem", { name: /^(Roll back|Go back)/ }).click();
      }
      await answerRollback(page, RELEASES_APP);
    });
    await forward(page, RELEASES_APP, before);
  });

  test("T07c: going back from a row of the history is three clicks", async ({ page, consoleServer }) => {
    await startAt(page, consoleServer, "T07c", `/apps/${RELEASES_APP}/deployments`, async () => {
      await expect(page.getByRole("region", { name: `Deploys of ${RELEASES_APP}, newest first` })).toBeVisible();
    });
    const before = await serving(page, RELEASES_APP);
    const id = await goBackTarget(page, RELEASES_APP);
    await walked(page, "T07c", async () => {
      const table = page.getByRole("region", { name: `Deploys of ${RELEASES_APP}, newest first` });
      await table.getByRole("button", { name: `Actions for #${String(id)}` }).click();
      await page.getByRole("menuitem", { name: /^Roll back to this/ }).click();
      await answerRollback(page, RELEASES_APP);
    });
    await forward(page, RELEASES_APP, before);
  });

  test("T09: a variable is changed and applied in four clicks, without reading the environment in clear", async ({ page, consoleServer }) => {
    const seen: string[] = [];
    page.on("request", (request) => seen.push(`${request.method()} ${request.url()}`));
    await startAt(page, consoleServer, "T09", `/apps/${RELEASES_APP}`, async () => {
      await expect(page.getByRole("heading", { level: 1, name: RELEASES_APP })).toBeVisible();
    });
    seen.length = 0;
    let written: { set: Record<string, string>; unset: string[] } | undefined;
    await walked(page, "T09", async () => {
      await sections(page).getByRole("link", { name: "Environment" }).click();
      await page.getByRole("table", { name: `Environment variables of ${RELEASES_APP}` }).getByText("LOG_LEVEL", { exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Edit LOG_LEVEL" });
      await dialog.getByLabel("Value").fill("debug");
      await dialog.getByRole("button", { name: /^Update( variable)?$/ }).click();
      const patch = page.waitForRequest((request) => request.method() === "PATCH" && request.url().endsWith(`/api/apps/${RELEASES_APP}/env`), { timeout: 30_000 });
      await page.getByRole("button", { name: "Save and restart", exact: true }).click();
      written = (await patch).postDataJSON() as typeof written;
    });
    if (!MEASURE_ONLY) {
      // Nothing was read in clear to do it, and only what changed was written.
      expect(seen.filter((request) => /^GET .*\/env\?.*unmask=true/.test(request))).toEqual([]);
      expect(written?.set).toEqual({ LOG_LEVEL: "debug" });
    }
    await jobsEnded(page, RELEASES_APP);
    const restored = await page.request.patch(`/api/apps/${RELEASES_APP}/env`, { data: { set: { LOG_LEVEL: "info" }, unset: [], dry_run: false }, headers: await csrf(page) });
    expect(restored.ok(), await restored.text()).toBe(true);
    await jobsEnded(page, RELEASES_APP);
  });

  test("T11: a domain is added with its TLS in three clicks", async ({ page, consoleServer }) => {
    const alias = "budget.example.net";
    await startAt(page, consoleServer, "T11", `/apps/${SITE_APP}`, async () => {
      await expect(page.getByRole("heading", { level: 1, name: SITE_APP })).toBeVisible();
    });
    await walked(page, "T11", async () => {
      await sections(page).getByRole("link", { name: "Domains" }).click();
      await page.getByRole("button", { name: "Add domain" }).click();
      const dialog = page.getByRole("dialog", { name: `Add a domain to ${SITE_APP}` });
      await dialog.getByLabel("Domain").fill(alias);
      await dialog.getByLabel("Domain").blur();
      const added = page.waitForRequest((request) => request.url().endsWith(`/api/apps/${SITE_APP}/domains`) && request.method() === "POST");
      await dialog.getByRole("button", { name: /^Add (domain|anyway)$/ }).click();
      await added;
      await expect(dialog).toBeHidden();
    });
    await jobsEnded(page, SITE_APP);
    await elevate(page, consoleServer);
    const removed = await page.request.delete(`/api/apps/${SITE_APP}/domains/${alias}`, { headers: await csrf(page) });
    expect(removed.status(), await removed.text()).toBeLessThan(300);
    await jobsEnded(page, SITE_APP);
  });

  test("T12: the memory limit is changed in two clicks from the application's overview", async ({ page, consoleServer }) => {
    await startAt(page, consoleServer, "T12", `/apps/${SHOP}`, async () => {
      await expect(page.getByRole("heading", { level: 1, name: SHOP })).toBeVisible();
    });
    await walked(page, "T12", async () => {
      await page.getByRole("link", { name: "Edit limits" }).click();
      await page.getByRole("textbox", { name: "Memory", exact: true }).fill("256");
      const patched = page.waitForRequest((request) => request.url().endsWith(`/api/apps/${SHOP}/limits`) && request.method() === "PATCH");
      await page.getByRole("button", { name: "Save", exact: true }).click();
      await patched;
    });
    const cleared = await page.request.patch(`/api/apps/${SHOP}/limits`, { data: { memory_max_mb: null, cpu_quota_percent: null, tasks_max: null, restart: false }, headers: await csrf(page) });
    expect(cleared.status(), await cleared.text()).toBeLessThan(500);
  });
});

// ---------------------------------------------------------------------------------------------------
// Anywhere: what needs me, and any page by its name.
// ---------------------------------------------------------------------------------------------------

test.describe("from any page", () => {
  test("T13: what needs the operator is two clicks away, from a page that has nothing to do with it", async ({ page, consoleServer }) => {
    await startAt(page, consoleServer, "T13", "/databases", async () => {
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    });
    await walked(page, "T13", async () => {
      await page.getByRole("button", { name: /^Inbox, \d+ things? need(s)? you$/ }).click();
      const open = page.getByRole("dialog", { name: "Inbox" });
      const row = open.getByRole("listitem").filter({ has: page.getByText(FAILED_APP, { exact: true }) });
      await row.getByRole("link", { name: `View log of the deploy of ${FAILED_APP}` }).click();
      await expect(page).toHaveURL(new RegExp(`/apps/${FAILED_APP.replace(/\./g, "\\.")}/deployments/\\d+$`));
    });
  });

  /**
   * The twelve pages an operator is most likely to look for by name, as the palette words them and where they are:
   * the five that were not found at all (a tab of an area, the terminal, the export to Prometheus) and seven that were.
   */
  const FINDINGS = [
    { name: "Terminal", to: "/server/terminal" },
    { name: "Schedules", to: "/backups/schedules" },
    { name: "DNS", to: "/domains/dns" },
    { name: "Ports", to: "/domains/ports" },
    { name: "Logs and metrics export", to: "/settings/log-shipping" },
    { name: "System updates", to: "/server/updates" },
    { name: "Alert rules", to: "/settings/alerts" },
    { name: "Channels", to: "/settings/notifications" },
    { name: "Accounts", to: "/settings/accounts" },
    { name: "API tokens", to: "/settings/tokens" },
    { name: "Map", to: "/map" },
    { name: "Sign-in security", to: "/account/security" },
  ] as const;

  test("T14: every one of twelve pages is found by its name with the keyboard alone, and in two clicks with the pointer", async ({ page, consoleServer }) => {
    await startAt(page, consoleServer, "T14", "/databases", async () => {
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    });
    const search = page.getByRole("combobox", { name: "Search apps, databases, domains, servers, settings and actions" });
    const worst = { clicks: 0, task: "" };
    for (const entry of FINDINGS) {
      // With the keyboard: Ctrl+K, the name, Enter. Not a click.
      await page.goto("/databases");
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await startCounting(page);
      await page.keyboard.press("Control+k");
      await search.fill(entry.name);
      await page.keyboard.press("Enter");
      await expect(page, `${entry.name} by keyboard`).toHaveURL(new RegExp(`${entry.to.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&")}(\\?.*)?$`));
      expect(await clicks(page), `${entry.name} took a click by keyboard`).toBe(0);

      // With the pointer: the search button, then the result.
      await page.goto("/databases");
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await startCounting(page);
      await page.getByRole("button", { name: /^Search/ }).first().click();
      await search.fill(entry.name);
      await page.getByRole("option").first().click();
      await expect(page, `${entry.name} by pointer`).toHaveURL(new RegExp(`${entry.to.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&")}(\\?.*)?$`));
      const used = await clicks(page);
      if (used > worst.clicks) {
        worst.clicks = used;
        worst.task = entry.name;
      }
    }
    withinBudget("T14", `the slowest of twelve pages found by name was "${worst.task}"`, worst.clicks, budgetOf("T14").budget);
  });
});

// ---------------------------------------------------------------------------------------------------
// A server with nothing on it (console_server.py --empty): the first minutes.
// ---------------------------------------------------------------------------------------------------

const REPO = path.resolve(import.meta.dirname, "..", "..");

/** Whether this tree's console server can make a server with nothing on it. */
let emptySupported: boolean | undefined;
function canMakeEmptyServer(): boolean {
  if (emptySupported === undefined) {
    const venv = path.join(REPO, ".venv", "bin", "python");
    const python = process.env.NOUST_PYTHON ?? venv;
    const help = spawnSync(python, [path.join(REPO, "scripts", "console_server.py"), "--help"], { encoding: "utf8", cwd: REPO, env: { ...process.env, PYTHONPATH: path.join(REPO, "src") } });
    emptySupported = help.status === 0 && help.stdout.includes("--empty");
  }
  return emptySupported;
}

/**
 * Walks the first run of a server with nothing on it to its Overview: the link the installer prints,
 * the access token, the first administrator, and "Set up later". None of it is counted: the tasks
 * below start from the Overview an operator lands on.
 */
async function firstRun(page: Page, server: RunningServer): Promise<void> {
  await page.goto(`${server.url}/login?with=token`);
  await page.getByLabel("Access token").fill(server.token);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Create the administrator" })).toBeVisible();
  await page.getByLabel("Username").fill("admin");
  await page.getByLabel("Password", { exact: true }).fill("a-long-password-for-the-first-run");
  await page.getByRole("button", { name: "Create administrator" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Protect your account" })).toBeVisible();
  await page.getByRole("button", { name: "Set up later" }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Deploy your first app" })).toBeVisible();
}

test.describe("a server with nothing on it", () => {
  test.setTimeout(240_000);
  let server: RunningServer;

  test.beforeEach(async () => {
    test.skip(!canMakeEmptyServer(), "this tree's console server has no --empty (the first-run server of the arrival lane)");
    server = await startConsoleServer(["--empty"]);
  });

  test.afterEach(async ({ page }) => {
    // The page leaves before the server does: a console left open on a server that went away reports its refusals.
    await page.goto("about:blank");
    if (canMakeEmptyServer()) await server.stop();
  });

  test("T01: the demo is one click from the Overview, and the live address is there when it is done", async ({ page, problems }) => {
    problems.expect(/status of 401 .* \/api\/auth\/session$/);
    await countClicks(page);
    await firstRun(page, server);
    await settle(page);
    await startCounting(page);
    await walked(page, "T01", async () => {
      await page.getByRole("link", { name: "Try a demo" }).click();
      await expect(page.getByRole("link", { name: /\.sslip\.io, the live site/ })).toBeVisible({ timeout: 120_000 });
    });
  });

  test("T02a: a public repository on a server with no domain of its own is three clicks to its first build", async ({ page, problems }) => {
    problems.expect(/status of 401 .* \/api\/auth\/session$/);
    await countClicks(page);
    await withoutGitHubApp(page);
    await firstRun(page, server);
    // The source the wizard is pointed at: this server has none seeded, so it gets the one a static site needs.
    const apps = (await (await page.request.get("/api/config/apps-directory")).json()) as { apps_directory: string };
    const landing = path.join(path.dirname(apps.apps_directory), "src", "landing");
    mkdirSync(landing, { recursive: true });
    writeFileSync(path.join(landing, "index.html"), '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Landing</title></head><body><h1>Coming soon</h1></body></html>\n');
    await page.goto("/apps");
    await expect(page.getByRole("heading", { level: 1, name: "Applications" })).toBeVisible();
    await settle(page);
    await startCounting(page);
    await walked(page, "T02a", () => repositoryWalk(page, "budget-empty.example.net"));
    await expect(page).toHaveURL(/\/apps\/[^/]+\/deployments\/\d+$/, { timeout: 30_000 });
  });

  test("T03b: a recipe from the empty Overview is two clicks to its first build", async ({ page, problems }) => {
    problems.expect(/status of 401 .* \/api\/auth\/session$/);
    await countClicks(page);
    await firstRun(page, server);
    await settle(page);
    await startCounting(page);
    await walked(page, "T03b", async () => {
      await page.getByRole("link", { name: /WordPress/ }).first().click();
      await expect(stepTitle(page)).toBeVisible();
      const queued = page.waitForRequest((request) => request.url().endsWith("/api/apps") && request.method() === "POST");
      await page.getByRole("button", { name: /^Deploy( \S+)?$/ }).click();
      await queued;
    });
  });
});

// ---------------------------------------------------------------------------------------------------
// A fleet: a central and a node.
// ---------------------------------------------------------------------------------------------------

const NODE_NAME = "fleet-node";

interface FleetWorkerFixtures {
  node: RunningServer;
  consoleServer: ConsoleServer;
}

/** A central with one node, as fleet.spec.ts makes them: the ssh dial replaced by a loopback port, the rest the real code. */
const withFleet = test.extend<object, FleetWorkerFixtures>({
  node: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const node = await startConsoleServer([]);
      try {
        await use(node);
      } finally {
        await node.stop();
      }
    },
    { scope: "worker", timeout: 75_000 },
  ],
  consoleServer: [
    async ({ node }, use) => {
      const central = await startConsoleServer(["--totp", "--backup-codes", "500", "--fleet-node", `${NODE_NAME}=127.0.0.1:${new URL(node.url).port}`]);
      try {
        await use(central);
      } finally {
        await central.stop();
      }
    },
    { scope: "worker", timeout: 90_000 },
  ],
});

withFleet.describe("a fleet", () => {
  withFleet("T15a: changing to another server is two clicks and the page stays the same page", async ({ page, consoleServer }) => {
    await startAt(page, consoleServer, "T15a", "/apps", async () => {
      await expect(page.getByRole("heading", { level: 1, name: "Applications" })).toBeVisible();
    });
    await walked(page, "T15a", async () => {
      await page.getByRole("button", { name: /^Server:/ }).click();
      await page.getByRole("menuitemradio", { name: new RegExp(`^${NODE_NAME}`) }).click();
      await expect(page).toHaveURL(new RegExp(`/n/${NODE_NAME}/apps$`));
    });
  });

  withFleet("T15b: changing to all servers is two clicks and the sidebar becomes the fleet's, with seven destinations at most", async ({ page, consoleServer }) => {
    await startAt(page, consoleServer, "T15b", "/apps", async () => {
      await expect(page.getByRole("heading", { level: 1, name: "Applications" })).toBeVisible();
    });
    await walked(page, "T15b", async () => {
      await page.getByRole("button", { name: /^Server:/ }).click();
      await page.getByRole("menuitemradio", { name: /^All servers/ }).click();
      await expect(page.getByRole("button", { name: "Server: all servers" })).toBeVisible();
    });
    if (!MEASURE_ONLY) {
      const destinations = await page.getByRole("navigation", { name: "Main" }).getByRole("link").count();
      expect(destinations, "the fleet's sidebar").toBeLessThanOrEqual(7);
      expect(destinations).toBeGreaterThan(0);
    }
  });
});
