/**
 * An application's deployments against the real backend: the history as one list that marks
 * what serves, filters that live in the address, a failed deploy's page with its error verbatim
 * and the phase it stopped in, going back to a release through the one rollback dialog, a live
 * update streaming its build log until it succeeds, and a deploy's own actions: rebuilding its
 * commit and going back to it. Runs in both themes, with the CSP and console gates of the
 * `problems` fixture.
 *
 * The seeded machine (scripts/console_server.py, "an application's tabs" and "a deploy's
 * actions"): tienda.example.org is on releases with thirteen deploys, two of whose releases are
 * on disk and not serving; pedidos.example.org is in place with a real tree whose update runs the
 * real update sequence, its npm output streamed a line at a time, and a deployment whose
 * snapshot backup still exists; bodas.example.com is a static site with one deploy.
 */

import type { Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, signIn, stillness, test } from "./fixtures";

const RELEASES_APP = "tienda.example.org";
const LIVE_APP = "pedidos.example.org";
const STATIC_APP = "bodas.example.com";

interface Row {
  id: number;
  status: string;
  error: string | null;
  commit_message: string | null;
  git_commit: string | null;
  release_id: string | null;
  job_id: string | null;
  rollback_available: boolean;
  rollback_unavailable_reason: string | null;
  snapshot_backup: string | null;
}

interface ReleaseRow {
  id: string;
  active: boolean;
}

/** The CSRF header every write through `page.request` carries, mirrored from its cookie. */
async function csrf(page: Page): Promise<Record<string, string>> {
  const cookie = (await page.context().cookies()).find((entry) => entry.name === "noust_csrf");
  return cookie ? { "X-Noust-CSRF": cookie.value } : {};
}

async function serving(page: Page, domain: string): Promise<string> {
  const body = (await (await page.request.get(`/api/apps/${domain}/releases`)).json()) as { items: ReleaseRow[] };
  const active = body.items.find((release) => release.active);
  if (!active) throw new Error(`nothing serves ${domain}`);
  return active.id;
}

/** Puts a release back in service through the API, as the Deployments tab's Activate does. */
/** The seeded deploy of 19d3f6e, whose release is on disk: not a row a rollback to it wrote since. */
function seededOnDisk(row: Row): boolean {
  return row.git_commit === "19d3f6e" && row.release_id !== null && row.rollback_available;
}

/** Waits for a job the console queued to end: the release is switched before the health gate has said its last word. */
async function jobEnded(page: Page, queued: { json: () => Promise<unknown> }): Promise<void> {
  const { job_id: id } = (await queued.json()) as { job_id: string };
  await expect
    .poll(async () => ((await (await page.request.get(`/api/jobs/${id}`)).json()) as { status: string }).status, { timeout: 60_000, intervals: [500] })
    .toMatch(/^(completed|failed)$/);
}

async function activate(page: Page, domain: string, release: string): Promise<void> {
  const response = await page.request.post(`/api/apps/${domain}/releases/${release}/activate`, { headers: await csrf(page) });
  expect(response.ok(), await response.text()).toBe(true);
}

async function deployment(page: Page, domain: string, pick: (row: Row) => boolean): Promise<number> {
  const response = await page.request.get(`/api/deployments?domain=${domain}&limit=50`);
  const body = (await response.json()) as { items: Row[] };
  const row = body.items.find(pick);
  if (!row) throw new Error(`no seeded deployment of ${domain} matches`);
  return row.id;
}

/** Dismisses the toasts a job's end raises, which axe would judge instead of the page. */
async function dismissToasts(page: Page): Promise<void> {
  const toasts = page.locator('.toast button[aria-label="Dismiss notification"]');
  await toasts.evaluateAll((buttons) => {
    for (const button of buttons) (button as HTMLButtonElement).click();
  });
  await expect(toasts).toHaveCount(0);
}

function phase(page: Page, key: string) {
  return page.getByRole("list", { name: "Deploy phases" }).locator(`[data-phase="${key}"]`);
}

test("the history is one list with what serves marked Live, each deploy opening its page", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${RELEASES_APP}/deployments`);
  const table = page.getByRole("region", { name: `Deploys of ${RELEASES_APP}, newest first` });
  // Every deploy the store keeps is in the list at once: no page to ask for, no second list of versions.
  await expect.poll(async () => await table.getByRole("row").count()).toBeGreaterThan(11);
  await expect(page.getByRole("button", { name: "Load more" })).toHaveCount(0);
  // A row is a button that opens its page: the number is its name.
  const first = table.getByRole("button", { name: /^Deployment \d+$/ }).first();
  await expect(first).toBeVisible();

  await expect(table.getByText("Live", { exact: true })).toBeVisible();
  await expect(table.getByText("Removed from disk").first()).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "an app's deployments");

  await first.click();
  await expect(page.getByRole("heading", { level: 2, name: /^Deployment \d+$/ })).toBeVisible();
});

test("a row opens its deploy, and the filters narrow the list and stay in the address", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${RELEASES_APP}/deployments`);
  const table = page.getByRole("region", { name: `Deploys of ${RELEASES_APP}, newest first` });
  await expect.poll(async () => await table.getByRole("row").count()).toBeGreaterThan(11);
  const all = await table.getByRole("row").count();

  await page.getByRole("combobox", { name: "State" }).click();
  await page.getByRole("option", { name: "Failed" }).click();
  await expect(page).toHaveURL(/[?&]state=failed/);
  await expect.poll(async () => await table.getByRole("row").count()).toBeLessThan(all);
  await expect(table.getByText("Failed", { exact: true }).first()).toBeVisible();

  // Typing replaces the history entry, and a reload comes back to the same list.
  await page.getByRole("searchbox", { name: "Search deployments" }).fill("checkout");
  await expect(page).toHaveURL(/[?&]q=checkout/);
  await page.reload();
  await expect(page.getByRole("searchbox", { name: "Search deployments" })).toHaveValue("checkout");
  await expect(page.getByRole("combobox", { name: "State" })).toContainText("Failed");

  await page.getByRole("button", { name: "Clear filters" }).first().click();
  await expect(page).not.toHaveURL(/[?&](state|q)=/);
  await expect.poll(async () => await table.getByRole("row").count()).toBe(all);
});

test("the table shows each deploy's commit message beside its hash", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${RELEASES_APP}/deployments`);
  const response = await page.request.get(`/api/deployments?domain=${RELEASES_APP}&limit=50`);
  const body = (await response.json()) as { items: Row[] };
  const row = body.items.find((item) => item.commit_message !== null);
  if (!row?.commit_message) throw new Error(`no seeded deployment of ${RELEASES_APP} has a commit message`);
  const table = page.getByRole("region", { name: `Deploys of ${RELEASES_APP}, newest first` });
  // Truncated in the table, but the full subject line is there on hover, in the title attribute.
  await expect(table.getByTitle(row.commit_message)).toBeVisible();
});

test("a failed deploy says where it stopped, the fix above and the error verbatim", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${RELEASES_APP}/deployments`);
  const id = await deployment(page, RELEASES_APP, (row) => row.status === "failed" && (row.error ?? "").includes("Type error"));
  await page.goto(`/apps/${RELEASES_APP}/deployments/${String(id)}`);
  await expect(page.getByText("The deploy failed while building")).toBeVisible();
  // The error in its box above, and the end of the phase in its own: both say it.
  await expect(page.locator("pre").filter({ hasText: "Type error: Property 'total' does not exist on type 'Order'." }).first()).toBeVisible();
  await expect(phase(page, "build")).toHaveAttribute("data-state", "failed");
  await expect(phase(page, "install")).toHaveAttribute("data-state", "done");
  await expect(phase(page, "activate")).toHaveAttribute("data-state", "unrecorded");
  const log = page.getByRole("region", { name: `Build log of deployment ${String(id)} of ${RELEASES_APP}` });
  await expect(log.getByText("Failed to compile.")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a failed deploy");
});

test("a release app rolls back to an earlier version in seconds, from a row, and forward again", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${RELEASES_APP}/deployments`);
  const before = await serving(page, RELEASES_APP);
  const id = await deployment(page, RELEASES_APP, seededOnDisk);

  const table = page.getByRole("region", { name: `Deploys of ${RELEASES_APP}, newest first` });
  await table.getByRole("button", { name: `Actions for #${String(id)}` }).click();
  await page.getByRole("menuitem", { name: "Roll back to this" }).click();
  const dialog = page.getByRole("alertdialog", { name: `Roll back ${RELEASES_APP}` });
  await expect(dialog).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the rollback confirmation");
  const queued = page.waitForResponse((response) => response.url().endsWith(`/api/apps/${RELEASES_APP}/deployments/${String(id)}/rollback`));
  await dialog.getByRole("button", { name: `Go back to #${String(id)}` }).click();
  const accepted = await queued;
  expect(accepted.status()).toBe(202);
  await expect(dialog).toBeHidden();
  await jobEnded(page, accepted);
  expect(await serving(page, RELEASES_APP)).toMatch(/-19d3f6e$/);

  // Forward again, so the worker's machine is as it was for the next test. Going back also paused the
  // app's automatic deploys (so the next push does not undo it): they run again too.
  await activate(page, RELEASES_APP, before);
  const resumed = await page.request.post(`/api/apps/${RELEASES_APP}/rollout/resume`, { headers: await csrf(page) });
  expect(resumed.ok(), await resumed.text()).toBe(true);
});

test("an update streams its build log live and flips to success", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${LIVE_APP}/deployments`);
  const latest = await deployment(page, LIVE_APP, () => true);

  await page.locator("main header").getByRole("button", { name: "Update", exact: true }).click();
  // The update records its deploy within moments; opened as soon as it is there.
  let next = latest;
  await expect
    .poll(
      async () => {
        next = await deployment(page, LIVE_APP, () => true);
        return next;
      },
      { intervals: [250] },
    )
    .not.toBe(latest);
  await page.goto(`/apps/${LIVE_APP}/deployments/${String(next)}`);
  const log = page.getByRole("region", { name: `Build log of deployment ${String(next)} of ${LIVE_APP}` });

  // Streamed while running: the install's output arrives before the build's.
  await expect(page.getByText("In progress", { exact: true }).first()).toBeVisible();
  await expect(log.getByText("added 214 packages, and audited 215 packages in 6s")).toBeVisible();
  await expect(log.getByText("Build finished in 4.2s")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("Succeeded", { exact: true }).first()).toBeVisible({ timeout: 20_000 });
  await expect(phase(page, "build")).toHaveAttribute("data-state", "done");
  await expect(phase(page, "activate")).toHaveAttribute("data-state", "done");
  // The header's pill pulses once when the app comes back up; axe judges it after that.
  await expect(page.locator("main header").locator("[data-state]").first()).toHaveAttribute("data-state", "running");

  await dismissToasts(page);
  await settle(page);
  await stillness(page);
  await expectNoA11yViolations(page, "a deploy that just succeeded");
});

test("rebuilding a deploy's commit says what it does first, then follows the job to its end", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${RELEASES_APP}/deployments`);
  const id = await deployment(page, RELEASES_APP, seededOnDisk);
  await page.goto(`/apps/${RELEASES_APP}/deployments/${String(id)}`);

  await page.getByRole("button", { name: "Rebuild this commit" }).click();
  const dialog = page.getByRole("dialog", { name: "Rebuild commit 19d3f6e?" });
  await expect(dialog.getByText(/If a version built from 19d3f6e is still on disk and is not the one live, it is switched to in seconds/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the rebuild confirmation");

  const queued = page.waitForResponse((response) => response.url().endsWith(`/api/apps/${RELEASES_APP}/deployments/${String(id)}/rebuild`));
  await dialog.getByRole("button", { name: "Rebuild" }).click();
  expect((await queued).status()).toBe(202);
  await expect(dialog).toBeHidden();
  // The sandbox's repository cache holds no commits, so the job ends in the updater's own
  // words - the part that matters here is that the page followed the job to its end.
  await expect(page.getByText("The rebuild failed")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator("pre").filter({ hasText: "Commit 19d3f6e does not exist in the repository" }).first()).toBeVisible();
});

test("a release app goes back to a deployment from its page, through its own endpoint, and forward again", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${RELEASES_APP}/deployments`);
  const before = await serving(page, RELEASES_APP);
  const id = await deployment(page, RELEASES_APP, seededOnDisk);
  await page.goto(`/apps/${RELEASES_APP}/deployments/${String(id)}`);

  await page.getByRole("button", { name: "Go back to this version" }).click();
  // The page asks the one rollback dialog, with this deployment chosen: what goes back is said in it.
  const dialog = page.getByRole("alertdialog", { name: `Roll back ${RELEASES_APP}` });
  await expect(dialog.getByRole("combobox", { name: "Version to go back to" })).toContainText(`#${String(id)}`);
  await expect(dialog.getByText(/It starts behind a startup check/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the rollback-to-a-deployment confirmation");
  const queued = page.waitForResponse((response) => response.url().endsWith(`/api/apps/${RELEASES_APP}/deployments/${String(id)}/rollback`));
  await dialog.getByRole("button", { name: `Go back to #${String(id)}` }).click();
  const accepted = await queued;
  expect(accepted.status()).toBe(202);
  await jobEnded(page, accepted);
  expect(await serving(page, RELEASES_APP)).toMatch(/-19d3f6e$/);

  // Forward again, so the worker's machine is as it was for the next test. Going back also paused the
  // app's automatic deploys (so the next push does not undo it), which a test that starts from "they
  // are running" would find still paused: the machine is only as it was once they run again.
  await activate(page, RELEASES_APP, before);
  const resumed = await page.request.post(`/api/apps/${RELEASES_APP}/rollout/resume`, { headers: await csrf(page) });
  expect(resumed.ok(), await resumed.text()).toBe(true);
});

test("the deploy that is live offers nothing to go back to; the header lists the other versions", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${RELEASES_APP}/deployments`);
  const live = await serving(page, RELEASES_APP);
  const id = await deployment(page, RELEASES_APP, (row) => row.release_id === live);
  await page.goto(`/apps/${RELEASES_APP}/deployments/${String(id)}`);
  await expect(page.getByRole("button", { name: "Rebuild this commit" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Go back to this version" })).toHaveCount(0);
  // Roll back is one of the header's daily verbs: on the surface, not in the menu.
  await page.locator("main header").filter({ has: page.getByRole("heading", { level: 1 }) }).getByRole("button", { name: "Roll back", exact: true }).click();
  const chooser = page.getByRole("alertdialog", { name: `Roll back ${RELEASES_APP}` });
  await expect(chooser.getByRole("combobox", { name: "Version to go back to" })).toBeVisible();
  await expect(chooser.getByRole("button", { name: /^Go back to #/ })).toBeVisible();
  await chooser.getByRole("button", { name: "Cancel" }).click();
  await expect(chooser).toBeHidden();
});

test("an in-place deploy whose snapshot still exists offers to go back to it", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${LIVE_APP}/deployments`);
  const response = await page.request.get(`/api/deployments?domain=${LIVE_APP}&limit=50`);
  const rows = ((await response.json()) as { items: Row[] }).items;
  const restorable = rows.find((row) => row.rollback_available && row.snapshot_backup !== null);
  if (!restorable?.snapshot_backup) throw new Error(`no seeded deployment of ${LIVE_APP} has a snapshot`);
  await page.goto(`/apps/${LIVE_APP}/deployments/${String(restorable.id)}`);
  await page.getByRole("button", { name: "Go back to this version" }).click();
  const dialog = page.getByRole("alertdialog", { name: `Roll back ${LIVE_APP}` });
  await expect(dialog.getByRole("combobox", { name: "Version to go back to" })).toContainText(`#${String(restorable.id)}`);
  await expect(dialog.getByText(new RegExp(`from backup ${restorable.snapshot_backup}.*A backup of how it is now is taken first`))).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "an in-place rollback to a deployment");
  // Not confirmed: the worker's app keeps its files.
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
});

test.describe("in a browser five and a half hours from the server", () => {
  test.use({ timezoneId: "Asia/Kolkata" });

  test("a static site's deploy has no health check, and its phases take the log's own seconds", async ({ page, consoleServer }) => {
    await signIn(page, consoleServer, `/apps/${STATIC_APP}/deployments`);
    const id = await deployment(page, STATIC_APP, () => true);
    await page.goto(`/apps/${STATIC_APP}/deployments/${String(id)}`);
    await expect(phase(page, "health")).toHaveAttribute("data-state", "not_applicable");
    await expect(phase(page, "health")).toContainText("Not applicable");
    await expect(page.getByText(/The startup check does not apply: a static site is served as files/)).toBeVisible();
    // Activating to the last line: four seconds, whatever zone the browser is in.
    await expect(phase(page, "activate")).toContainText("4.0s");
    await expect(phase(page, "fetch")).toContainText("6.0s");
    await settle(page);
    await expectNoA11yViolations(page, "a static site's deploy");
  });
});

test("an in-place app lists its backups under the history, and goes back to one through the dialog", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${LIVE_APP}/deployments`);
  const points = page.getByRole("list", { name: `Backups of ${LIVE_APP}, newest first` });
  // Two seeded, and one more for every update a test in this worker ran before.
  await expect(points.getByRole("listitem").nth(1)).toBeVisible();
  await points.getByRole("button", { name: /^Go back/ }).first().click();
  const dialog = page.getByRole("alertdialog", { name: `Roll back ${LIVE_APP}` });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Go back to this backup" })).toBeVisible();
  await expectNoA11yViolations(page, "the backup rollback confirmation");
  // Not confirmed: the worker's app keeps its files.
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
});
