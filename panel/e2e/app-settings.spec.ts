/**
 * An application's settings against the real backend. In 4.2 (item 116) they are not a list of their own: the
 * application's column lists seventeen sections in five groups, and what were eighteen subsections are four pages of
 * titled cards (Code and build, Deploys and triggers, Resources and running, Access and protection) beside Export and
 * Delete. Here: the column, the old addresses, the facts of how it runs, the Deploys form saved from the page's one save
 * bar, an in-place app turned onto instant rollback through its dry run, resource limits refused and then saved with the
 * exact body, deploy on push set up step by step, a sandboxed test build run as a job, and the deletion that starts with
 * nothing destructive ticked and waits for the domain. Sudo-mode actions go through the real "Confirm it's you".
 *
 * Instant rollback cannot be turned off through the API and both theme projects may run in the
 * same worker, so each project turns it on for its own app; the limits alternate by project for
 * the same reason. Screenshots of the states worth reviewing are written when NOUST_TABS_SCREENS
 * is set.
 */

import type { Page, TestInfo } from "@playwright/test";
import path from "node:path";

import { confirmItsYou, expect, expectNoA11yViolations, settle, signIn, stillness, test } from "./fixtures";
import { openFold } from "./part-folds";

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

test("the column lists seventeen sections in five groups, and an old address of the settings goes to its page", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer, `/apps/${RELEASE_APP}/code`);
  const column = page.getByRole("navigation", { name: "Application sections" });
  await expect(column.getByRole("link")).toHaveText([
    "Overview",
    "Deployments",
    "Logs",
    "Metrics",
    "Variables",
    "Domains",
    "Database",
    "Code and build",
    "Deploys and triggers",
    "Resources and running",
    "Access and protection",
    "Diagnose",
    "Terminal",
    "Files",
    "Export and declaration",
    "Several servers",
    "Delete",
  ]);
  await expect(column.getByRole("link", { name: "Code and build" })).toHaveAttribute("aria-current", "page");
  await column.getByRole("link", { name: "Deploys and triggers" }).click();
  await expect(page).toHaveURL(new RegExp(`/apps/${escapeRegExp(RELEASE_APP)}/triggers$`));
  // What the page is for is open; what deploys by itself, the push, the hooks and the previews are one press each.
  await expect(page.getByRole("heading", { level: 2, name: "Releases and rollback" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Deploy on push", expanded: false })).toBeVisible();

  // The address a bookmark or the terminal's link kept: it goes where the subsection went.
  // A folded part names itself, so that its fold is open when the page is.
  await page.goto(`/apps/${RELEASE_APP}/settings/deploy-on-push`);
  await expect(page).toHaveURL(new RegExp(`/apps/${escapeRegExp(RELEASE_APP)}/triggers#deploy-on-push$`));
  await expect(page.getByRole("button", { name: "Deploy on push", expanded: true })).toBeVisible();
  await page.goto(`/apps/${RELEASE_APP}/settings/export`);
  await expect(page).toHaveURL(new RegExp(`/apps/${escapeRegExp(RELEASE_APP)}/export$`));

  // On a phone the same list is the section selector at the top of the page.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/apps/${RELEASE_APP}/resources`);
  await expect(page.getByRole("heading", { level: 2, name: "Resources" })).toBeVisible();
  await expectNoA11yViolations(page, "an application's page on a phone");
});

test("how it runs is a card of facts the API records, and a static site has no process to say", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${RELEASE_APP}/resources`);
  const facts = await factsOf(page, RELEASE_APP);
  const how = page.getByRole("region", { name: "How it runs" });
  await expect(how.getByText("Instant rollback", { exact: true })).toBeVisible();
  await expect(how.getByText(facts.source === null ? "Not recorded" : "Starts at boot", { exact: true })).toBeVisible();
  // One bar for the page, and nothing in it to save before anything is typed.
  await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();
  await expectNoA11yViolations(page, "Resources and running");

  await page.goto("/apps/bodas.example.com/resources");
  const staticSite = page.getByRole("region", { name: "How it runs" });
  await expect(staticSite.getByText("The web server, no process", { exact: true })).toBeVisible();
  await expect(staticSite.getByText("Starts at boot")).toHaveCount(0);
});

test("Releases and rollback saves the startup check and the versions kept from one save bar, refusing on the field first", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${RELEASE_APP}/triggers`);
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
  expect((await health).postDataJSON()).toEqual({
    kind: "http",
    path: "/healthz",
    expect: null,
    timeout: 60,
    command: null,
    service: null,
    method: null,
    scheme: null,
    host: null,
    port: null,
    interval: null,
    retries: null,
    start_period: null,
  });
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
  await signIn(page, consoleServer, `/apps/${domain}/triggers`);
  const current = (await (await page.request.get(`/api/apps/${domain}`)).json()) as { layout: string };
  test.skip(current.layout === "releases", `${domain} was moved by an earlier attempt in this worker`);

  const rollback = card(page, "Instant rollback");
  await expect(rollback.getByText(/^Safe updates: if a new version fails/)).toBeVisible();
  // What turning it on does is one fold away; the dry run says it again before anything changes.
  await rollback.getByRole("button", { name: "What turning it on does" }).click();
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
  await expect(rollback.getByText("Live now")).toBeVisible();
});

test("resource limits are refused as the backend would, then saved with exactly what the form says", async ({ page, consoleServer }, testInfo) => {
  const memory = testInfo.project.name === "dark" ? 320 : 256;
  await signIn(page, consoleServer, `/apps/${IN_PLACE_APP}/resources`);
  const memoryField = page.getByRole("textbox", { name: "Memory", exact: true });
  await memoryField.fill("32");
  await saveBar(page).getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("A memory limit of 32M is too small. Allow at least 64M, or no limit.")).toBeVisible();

  await memoryField.fill(String(memory));
  await page.getByRole("textbox", { name: "CPU", exact: true }).fill("50");
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
  await signIn(page, consoleServer, `/apps/${RELEASE_APP}/triggers`);
  await openFold(page, "Deploy on push");
  const setup = card(page, "Set it up");
  // Only the step the setup is at is drawn: the seeded machine publishes an address and the app has its
  // secret, so it is the last, where the values are pasted; the two before are one click back.
  await expect(setup.getByRole("heading", { name: "Add the webhook in GitHub" })).toBeVisible();
  await expect(setup.getByRole("list", { name: "Steps" }).getByRole("listitem")).toHaveCount(3);
  await setup.getByRole("button", { name: "Secret: done" }).click();
  await expect(setup.getByText(/^Done: every delivery must be signed with it/)).toBeVisible();
  await setup.getByRole("button", { name: "Continue" }).click();
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
  await signIn(page, consoleServer, `/apps/${RELEASE_APP}/code`);
  // The sandbox, the packages and the cache are one press under Build.
  await openFold(page, "Sandbox, packages and cache");
  const sandbox = card(page, "Build sandbox");
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
  await signIn(page, consoleServer, `/apps/${RELEASE_APP}/delete`);
  const deletions: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "DELETE" && request.url().includes(`/api/apps/${RELEASE_APP}`)) deletions.push(request.url());
  });
  // One box for each thing that holds data, none ticked: the page says it before anything is pressed.
  await expect(page.getByRole("checkbox", { name: /Also delete its files/ })).not.toBeChecked();
  await expect(page.getByRole("checkbox", { name: /Also delete its certificate/ })).not.toBeChecked();
  await expectNoA11yViolations(page, "the page of deleting an application");

  await page.getByRole("button", { name: "Delete…" }).click();
  await confirmItsYou(page, consoleServer);

  const dialog = page.getByRole("alertdialog", { name: `Delete ${RELEASE_APP}` });
  await expect(dialog.getByText(/^Removed: its service and its web server site\.$/)).toBeVisible();
  const action = dialog.getByRole("button", { name: "Delete application" });
  await expect(action).toBeDisabled();
  await dialog.getByRole("textbox").fill(RELEASE_APP);
  await expect(action).toBeEnabled();
  await stillness(page);
  await expectNoA11yViolations(page, "the delete confirmation");

  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  expect(deletions).toEqual([]);
  await expect(page).toHaveURL(new RegExp(`/apps/${escapeRegExp(RELEASE_APP)}/delete$`));
});
