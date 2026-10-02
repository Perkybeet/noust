/**
 * An application's settings against the real backend, one subsection per URL: the navigation
 * between them, General's facts, the Deploys form saved from its one save bar, an in-place app
 * turned onto instant rollback through its dry run, resource limits refused and then saved with
 * the exact body, deploy on push set up step by step, a sandboxed test build run as a job, and
 * the deletion that starts with nothing destructive ticked and waits for the domain. Sudo-mode
 * actions go through the real "Confirm it's you".
 *
 * Instant rollback cannot be turned off through the API and both theme projects may run in the
 * same worker, so each project turns it on for its own app; the limits alternate by project for
 * the same reason. Screenshots of the states worth reviewing are written when NOUST_TABS_SCREENS
 * is set.
 */

import type { Page, TestInfo } from "@playwright/test";
import path from "node:path";

import { confirmItsYou, expect, expectNoA11yViolations, settle, signIn, stillness, test } from "./fixtures";

const RELEASE_APP = "tienda.example.org";
const IN_PLACE_APP = "pedidos.example.org";

/** The save bar of the subsection on screen. */
function saveBar(page: Page) {
  return page.getByRole("region", { name: "Unsaved changes" });
}

/** A card of the subsection, by its title. */
function card(page: Page, title: string) {
  return page.locator("section").filter({ has: page.getByRole("heading", { name: title, exact: true }) }).last();
}

/** A screenshot for review, when asked for: `NOUST_TABS_SCREENS=/tmp/console-tabs`. */
async function review(page: Page, testInfo: TestInfo, name: string): Promise<void> {
  const out = process.env.NOUST_TABS_SCREENS;
  if (!out) return;
  await settle(page);
  await page.screenshot({ path: path.join(out, testInfo.project.name, `${name}.png`), fullPage: false });
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

interface AppFacts {
  source: string | null;
  branch: string | null;
  build_command: string[];
  start_command: string | null;
  keep_releases: number;
  health_path: string | null;
}

async function factsOf(page: Page, domain: string): Promise<AppFacts> {
  return (await (await page.request.get(`/api/apps/${domain}`)).json()) as AppFacts;
}

test("the subsections are URLs of their own, and on a phone the list is a page of its own", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer, `/apps/${RELEASE_APP}/settings`);
  const nav = page.getByRole("navigation", { name: "Application settings" }).first();
  await expect(nav.getByRole("link")).toHaveText(["General", "Deploys", "Deploy on push", "Deploy hooks", "Builds", "Resources", "Previews", "Export", "Delete"]);
  await nav.getByRole("link", { name: "Deploy on push" }).click();
  await expect(page).toHaveURL(new RegExp(`/apps/${escapeRegExp(RELEASE_APP)}/settings/deploy-on-push$`));
  await expect(page.getByRole("heading", { level: 2, name: "Deploy on push" })).toBeVisible();
  // The app's tab stays Settings on every subsection.
  await expect(page.getByRole("navigation", { name: "Application sections" }).getByRole("link", { name: "Settings" })).toHaveAttribute("aria-current", "page");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/apps/${RELEASE_APP}/settings`);
  const index = page.locator('nav[aria-label="Application settings"]:visible');
  await expect(index.getByRole("link", { name: "Resources" })).toBeVisible();
  await index.getByRole("link", { name: "Resources" }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Resources" })).toBeVisible();
  await page.getByRole("link", { name: "All sections" }).click();
  await expect(page).toHaveURL(new RegExp(`/apps/${escapeRegExp(RELEASE_APP)}/settings$`));
  await expectNoA11yViolations(page, "the settings index on a phone");
});

test("General reads what the API records", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${RELEASE_APP}/settings`);
  const facts = await factsOf(page, RELEASE_APP);
  if (facts.source === null) throw new Error(`${RELEASE_APP} has no recorded source to assert against`);
  const general = page.getByRole("region", { name: "General" });
  const repo = general.getByRole("link", { name: new RegExp(`^${escapeRegExp(facts.source)}`) });
  await expect(repo).toHaveAttribute("href", facts.source);
  if (facts.branch !== null) await expect(general.getByText(facts.branch, { exact: true })).toBeVisible();
  await expect(general.getByText(facts.build_command.length > 0 ? facts.build_command.join(" ") : "None", { exact: true })).toBeVisible();
  await expect(general.getByText("Instant rollback", { exact: true })).toBeVisible();
  await expect(saveBar(page)).toHaveCount(0);
  await expectNoA11yViolations(page, "General");

  await page.goto("/apps/bodas.example.com/settings");
  const staticSite = page.getByRole("region", { name: "General" });
  await expect(staticSite.getByText("The web server, no process", { exact: true })).toBeVisible();
  await expect(staticSite.getByText("Start command")).toHaveCount(0);
});

test("Deploys saves the startup check and the versions kept from one save bar, refusing on the field first", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${RELEASE_APP}/settings/deploys`);
  const before = await factsOf(page, RELEASE_APP);
  const check = card(page, "Startup check");
  const path = check.getByRole("textbox", { name: "Path" });
  await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();

  await path.fill("https://example.com/health");
  await saveBar(page).getByRole("button", { name: "Save" }).click();
  await expect(check.getByText(/A scheme or a host is not accepted/)).toBeVisible();
  await expect(path).toHaveAttribute("aria-invalid", "true");
  await expectNoA11yViolations(page, "a refused startup check");

  await path.fill("/healthz");
  await check.getByRole("textbox", { name: "Timeout" }).fill("60");
  const keep = card(page, "Instant rollback").getByRole("textbox", { name: "Versions to keep" });
  await keep.fill(String(before.keep_releases + 3));
  await expect(saveBar(page).getByText("3 unsaved changes")).toBeVisible();
  const health = page.waitForRequest((r) => r.url().endsWith(`/api/apps/${RELEASE_APP}/health`) && r.method() === "PATCH");
  const retention = page.waitForRequest((r) => r.url().endsWith(`/api/apps/${RELEASE_APP}/releases/retention`) && r.method() === "PATCH");
  await saveBar(page).getByRole("button", { name: "Save" }).click();
  await confirmItsYou(page, consoleServer);
  expect((await health).postDataJSON()).toEqual({ path: "/healthz", expect: null, timeout: 60 });
  expect((await retention).postDataJSON()).toEqual({ keep: before.keep_releases + 3 });
  await expect(page.getByText(`Saved. It keeps ${String(before.keep_releases + 3)} versions; nothing was removed.`)).toBeVisible();
  await expect(check.getByText("GET /healthz", { exact: true })).toBeVisible();
  await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();

  // Back as it was, so the worker's machine is the same for the next test.
  await check.getByRole("button", { name: "Use defaults" }).click();
  await keep.fill(String(before.keep_releases));
  await saveBar(page).getByRole("button", { name: "Save" }).click();
  await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();
  expect(await factsOf(page, RELEASE_APP)).toMatchObject({ health_path: null, keep_releases: before.keep_releases });
});

test("an in-place app turns on instant rollback through its dry run", async ({ page, consoleServer, problems }, testInfo) => {
  const domain = testInfo.project.name === "dark" ? "docs.example.org" : "blog.example.org";
  // Once moved, the app has no plan: a read racing the move answers 409 (see migrationPlanQuery).
  problems.expect(new RegExp(`status of 409 .*/api/apps/${escapeRegExp(domain)}/migrate/plan$`));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await signIn(page, consoleServer, `/apps/${domain}/settings/deploys`);
  const current = (await (await page.request.get(`/api/apps/${domain}`)).json()) as { layout: string };
  test.skip(current.layout === "releases", `${domain} was moved by an earlier attempt in this worker`);

  const rollback = card(page, "Instant rollback");
  await expect(rollback.getByText(/^Safe updates: if a new version fails/)).toBeVisible();
  await expect(rollback.getByText("Turning it on restarts the app once, for a moment.")).toBeVisible();
  await rollback.getByRole("button", { name: "Turn on instant rollback…" }).click();
  const dialog = page.getByRole("dialog", { name: `Turn on instant rollback for ${domain}` });
  await expect(dialog.getByText("uploads, storage", { exact: true })).toBeVisible();
  await expect(dialog.getByText(/, rewritten to run the live version$/)).toBeVisible();
  await stillness(page);
  await review(page, testInfo, "settings-rollback-dry-run-1440");
  await expectNoA11yViolations(page, "the dry run of instant rollback");

  await dialog.getByRole("button", { name: "Continue" }).click();
  await expect(dialog.getByText("The app restarts once")).toBeVisible();
  const moved = page.waitForResponse((r) => r.url().endsWith(`/api/apps/${domain}/migrate`) && r.request().method() === "POST");
  await dialog.getByRole("button", { name: "Turn it on" }).click();
  await confirmItsYou(page, consoleServer);
  const response = await moved;
  expect(response.status()).toBe(202);
  expect(response.request().postDataJSON()).toEqual({ persist: ["uploads", "storage"] });

  const summary = dialog.getByText(/files before, .* after \(/);
  await expect(summary).toBeVisible({ timeout: 15_000 });
  const match = /^([\d,]+) files before, ([\d,]+) after/.exec((await summary.textContent()) ?? "");
  expect(match?.[2]).toBe(match?.[1]);
  await dialog.getByRole("button", { name: "Done" }).click();
  await expect(dialog).toBeHidden();
  await expect(rollback.getByText("Live version")).toBeVisible();
});

test("resource limits are refused as the backend would, then saved with exactly what the form says", async ({ page, consoleServer }, testInfo) => {
  const memory = testInfo.project.name === "dark" ? 320 : 256;
  await signIn(page, consoleServer, `/apps/${IN_PLACE_APP}/settings/resources`);
  const memoryField = page.getByRole("textbox", { name: "Memory" });
  await memoryField.fill("32");
  await saveBar(page).getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("A memory limit of 32M is too small. Allow at least 64M, or no limit.")).toBeVisible();

  await memoryField.fill(String(memory));
  await page.getByRole("textbox", { name: "CPU" }).fill("50");
  await page.getByRole("textbox", { name: "Processes and threads" }).fill("128");
  const patched = page.waitForRequest((r) => r.url().endsWith(`/api/apps/${IN_PLACE_APP}/limits`) && r.method() === "PATCH");
  await saveBar(page).getByRole("button", { name: "Save" }).click();
  await confirmItsYou(page, consoleServer);
  expect((await patched).postDataJSON()).toEqual({ memory_max_mb: memory, cpu_quota_percent: 50, tasks_max: 128, restart: false });
  await expect(page.getByText(/^Saved\. .* was rewritten; the running app keeps its old limits until it restarts\.$/)).toBeVisible();
  await expect(page.getByText(`MemoryMax=${String(memory)}M CPUQuota=50% TasksMax=128`)).toBeVisible();
  await expectNoA11yViolations(page, "saved limits");
});

test("deploy on push says where the setup stands, step by step, and shows the secret only in sudo mode", async ({ page, consoleServer }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await signIn(page, consoleServer, `/apps/${RELEASE_APP}/settings/deploy-on-push`);
  await expect(page.getByText(/When you push to the repository, GitHub, GitLab or Gitea tells this server/)).toBeVisible();
  const setup = card(page, "Set it up");
  await expect(setup.getByRole("heading", { name: "A public address for webhooks" })).toBeVisible();
  await expect(setup.getByText(/^Done: every delivery must be signed with it/)).toBeVisible();
  const hookUrl = setup.getByText(new RegExp(`/hooks/deploy/${escapeRegExp(RELEASE_APP)}$`));
  await expect(hookUrl).toBeVisible();
  await expect(setup.getByText("application/json", { exact: true })).toBeVisible();

  const revealed = page.waitForResponse((r) => r.url().endsWith(`/api/apps/${RELEASE_APP}/webhook/reveal`) && r.request().method() === "POST");
  await setup.getByRole("button", { name: "Show" }).click();
  await confirmItsYou(page, consoleServer);
  const body = (await (await revealed).json()) as { secret: string };
  await expect(setup.getByText(body.secret, { exact: true })).toBeVisible();
  await stillness(page);
  await review(page, testInfo, "settings-push-secret-1440");
  await expectNoA11yViolations(page, "deploy on push with its secret shown");
  await setup.getByRole("button", { name: "Hide the secret" }).click();
  await expect(page.getByText(body.secret, { exact: true })).toHaveCount(0);

  // A new secret replaces the one the forge has: asked first.
  await page.getByRole("button", { name: "New secret…" }).click();
  const rotate = page.getByRole("alertdialog", { name: "Create a new secret?" });
  await expect(rotate.getByText(/refused until GitHub has the new one/)).toBeVisible();
  await rotate.getByRole("button", { name: "Cancel" }).click();
  await expect(rotate).toBeHidden();
});

test("a sandboxed test build runs as a job, and its failure is shown in the system's words", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${RELEASE_APP}/settings/builds`);
  const sandbox = card(page, "Sandboxed builds");
  await expect(sandbox.getByText("Its builds still run as root")).toBeVisible();
  await expect(sandbox.getByText("It can be turned on once a test build passes.")).toBeVisible();
  await expectNoA11yViolations(page, "builds before a test");

  const queued = page.waitForResponse((r) => r.url().endsWith(`/api/apps/${RELEASE_APP}/sandbox/test`) && r.request().method() === "POST");
  await sandbox.getByRole("button", { name: "Test a sandboxed build" }).click();
  expect((await queued).status()).toBe(202);
  // This machine is a sandbox that does not run as root: the trial says so, verbatim.
  await expect(sandbox.getByText("The test build did not finish")).toBeVisible({ timeout: 20_000 });
  await expect(sandbox.getByText(/A trial build in the sandbox needs root/)).toBeVisible();
});

test("deleting starts with nothing destructive ticked, waits for the domain, and cancelling deletes nothing", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${RELEASE_APP}/settings/delete`);
  const deletions: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "DELETE" && request.url().includes(`/api/apps/${RELEASE_APP}`)) deletions.push(request.url());
  });
  await page.getByRole("button", { name: "Delete application…" }).click();
  await confirmItsYou(page, consoleServer);

  const dialog = page.getByRole("alertdialog", { name: `Delete ${RELEASE_APP}` });
  await expect(dialog.getByRole("checkbox", { name: /Also delete its files/ })).not.toBeChecked();
  await expect(dialog.getByRole("checkbox", { name: /Also delete its certificate/ })).not.toBeChecked();
  const action = dialog.getByRole("button", { name: "Delete application" });
  await expect(action).toBeDisabled();
  await dialog.getByRole("textbox").fill(RELEASE_APP);
  await expect(action).toBeEnabled();
  await stillness(page);
  await expectNoA11yViolations(page, "the delete confirmation");

  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  expect(deletions).toEqual([]);
  await expect(page).toHaveURL(new RegExp(`/apps/${escapeRegExp(RELEASE_APP)}/settings/delete$`));
});
