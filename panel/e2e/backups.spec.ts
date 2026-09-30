/**
 * The Backups page against the real backend and its seeded machine: the full option set on
 * create, and the typed-domain confirmation before a restore. Runs in both themes
 * (playwright.config.ts's two projects), with the CSP and console gates of the `problems`
 * fixture.
 */

import { expect, expectNoA11yViolations, settle, signIn, startConsoleServer, test, toasts } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

/** The coverage table: one row per application. */
function coverage(page: import("@playwright/test").Page) {
  return page.getByRole("region", { name: /^Applications/ });
}

/** An application's row, by the button that opens its backups. */
function appRow(page: import("@playwright/test").Page, domain: string) {
  return coverage(page)
    .getByRole("row")
    .filter({ has: page.getByRole("button", { name: domain, exact: true }) });
}

/** The drawer with one application's backups. */
async function openBackupsOf(page: import("@playwright/test").Page, domain: string) {
  await appRow(page, domain).getByRole("button", { name: domain, exact: true }).click();
  const drawer = page.getByRole("dialog", { name: `Backups of ${domain}` });
  await expect(drawer).toBeVisible();
  return drawer;
}

test("every application is listed with whether it is protected, and the page passes axe", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/backups");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Backups");
  // One row per application, and per domain that still has backups: other tests in this worker
  // create backups too, so the count comes from the API.
  const apps = (await (await page.request.get("/api/apps")).json()) as { apps: { domain: string }[] };
  const listed = (await (await page.request.get("/api/backups")).json()) as { backups: { domain: string }[] };
  expect(listed.backups.length, "the seed has backups").toBeGreaterThan(0);
  const domains = new Set([...apps.apps.map((app) => app.domain), ...listed.backups.map((backup) => backup.domain)]);
  await expect(coverage(page).getByRole("row").filter({ hasNot: page.getByRole("columnheader") })).toHaveCount(domains.size);
  // The seeded schedule copies shop.example.net's backups off the server.
  await expect(appRow(page, "shop.example.net")).toContainText("Every day at 02:00");
  await expect(appRow(page, "shop.example.net")).toContainText("offsite-sftp");
  // An application with no backup says so, as a warning.
  await expect(appRow(page, "blog.example.org")).toContainText("No backups");

  await settle(page);
  await expectNoA11yViolations(page, "the backups page");
});

test("creating a backup with databases included sends the right request", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/backups");

  await page.getByRole("button", { name: "Back up now" }).click();
  const dialog = page.getByRole("dialog", { name: "Back up an application" });
  await expectNoA11yViolations(page, "the create backup dialog");

  await dialog.getByRole("combobox", { name: "Application" }).click();
  await page.getByRole("option", { name: "shop.example.net" }).click();
  await dialog.getByLabel("Note").fill("Before the migration");
  await dialog.getByRole("checkbox", { name: "Databases" }).check();
  // Tags are folded under "More options", with what most operators never change.
  await dialog.getByRole("button", { name: "More options" }).click();
  await dialog.getByLabel("Tags").fill("manual, pre-migration");

  const queued = page.waitForRequest((request) => request.url().endsWith("/api/backups") && request.method() === "POST");
  await dialog.getByRole("button", { name: "Create backup" }).click();
  expect((await queued).postDataJSON()).toEqual({
    domain: "shop.example.net",
    description: "Before the migration",
    include_env: true,
    include_node_modules: false,
    include_build: false,
    include_database: true,
    include_docker_volumes: false,
    schemas: [],
    redis_method: "rdb",
    tags: ["manual", "pre-migration"],
  });
  await expect(toasts(page).getByText("Backup queued for shop.example.net", { exact: true })).toBeVisible();
  await expect(dialog).not.toBeVisible();
});

test("restoring the latest backup is confirmed by typing the target domain", async ({ page, consoleServer, problems }) => {
  // Restoring is sudo mode (D5): the first attempt asks the operator to confirm it's them.
  problems.expect(/status of 403 .*\/api\/backups\/.*\/restore$/);
  await signIn(page, consoleServer, "/backups");

  await page.getByRole("button", { name: "Actions for shop.example.net", exact: true }).click();
  await page.getByRole("menuitem", { name: "Restore the latest…" }).click();

  const dialog = page.getByRole("alertdialog", { name: "Restore a backup of shop.example.net" });
  await expect(dialog).toBeVisible();
  const confirmButton = dialog.getByRole("button", { name: "Restore" });
  await expect(confirmButton).toBeDisabled();

  const domainField = dialog.getByRole("textbox").nth(0);
  await expect(domainField).toHaveValue("shop.example.net");
  const confirmField = dialog.getByRole("textbox").nth(1);
  await confirmField.fill("not-the-domain");
  await expect(confirmButton).toBeDisabled();
  await confirmField.fill("shop.example.net");
  await expect(confirmButton).toBeEnabled();
  await expectNoA11yViolations(page, "the restore confirmation");

  await confirmButton.click();

  const elevate = page.getByRole("dialog", { name: "Confirm it's you" });
  await expect(elevate).toBeVisible();
  await expectNoA11yViolations(page, "the elevation dialog");
  await elevate.getByLabel("Authentication code").fill(consoleServer.secondFactor());
  const requested = page.waitForRequest(
    (request) => request.url().includes("/api/backups/") && request.url().endsWith("/restore") && request.method() === "POST",
  );
  await elevate.getByRole("button", { name: "Confirm" }).click();
  const body = (await requested).postDataJSON() as { target_domain: string | null; restore_env: boolean; verify: boolean };
  expect(body).toEqual({ target_domain: null, restore_env: false, verify: true });
  await expect(elevate).toBeHidden();
  await expect(toasts(page).getByText("Restore queued for shop.example.net", { exact: true })).toBeVisible();
});

test("restoring into a different domain is confirmed by typing that domain", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/backups");
  const drawer = await openBackupsOf(page, "shop.example.net");
  await drawer.getByRole("button", { name: /^Actions for/ }).first().click();
  await page.getByRole("menuitem", { name: "Restore…" }).click();

  const dialog = page.getByRole("alertdialog", { name: "Restore a backup of shop.example.net" });
  const domainField = dialog.getByRole("textbox").nth(0);
  await domainField.fill("shop-staging.example.com");
  const confirmField = dialog.getByRole("textbox").nth(1);
  await confirmField.fill("shop.example.net");
  await expect(dialog.getByRole("button", { name: "Restore" })).toBeDisabled();
  await confirmField.fill("shop-staging.example.com");
  await expect(dialog.getByRole("button", { name: "Restore" })).toBeEnabled();
});

test("checking a backup's integrity updates its row with the result, and a reload keeps it", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/backups");
  const drawer = await openBackupsOf(page, "taller.example.com");
  const row = drawer.getByRole("row").filter({ has: page.getByRole("button", { name: /^Actions for/ }) }).first();
  await expect(row.getByText("Not checked")).toBeVisible();

  await row.getByRole("button", { name: /^Actions for/ }).click();
  const verified = page.waitForResponse(
    (response) => response.url().includes("/api/backups/") && response.url().endsWith("/verify") && response.request().method() === "POST",
  );
  await page.getByRole("menuitem", { name: "Check integrity" }).click();
  expect((await verified).status()).toBe(200);

  await expect(row.getByText("Verified", { exact: true })).toBeVisible();
  // The "Created" column has its own relative time too; the check's is the last <time>.
  await expect(row.locator("time").last()).toHaveText(/ago$|just now/);
  await settle(page);
  await expectNoA11yViolations(page, "a backup row after checking it");

  // The server, not the session, remembers it: the application's address shows the same verdict.
  await page.goto("/backups?domain=taller.example.com");
  const again = page.getByRole("dialog", { name: "Backups of taller.example.com" });
  await expect(again.getByText("Verified", { exact: true }).first()).toBeVisible();
});

test("the header says what the backups take and the room left on their own disk; schedules have their tab", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/backups");
  // The disk the backup directory is on, used and free - not the machine's root disk.
  const storage = (await (await page.request.get("/api/backups/storage")).json()) as {
    path: string;
    filesystem_total: number | null;
    filesystem_free: number | null;
  };
  expect(storage.filesystem_total, "the sandbox's backup directory is on a readable filesystem").toBeGreaterThan(0);
  expect(storage.filesystem_free).not.toBeNull();
  await expect(page.getByText(/^Disk: [\d.]+ (B|KB|MB|GB|TB) free of [\d.]+ (B|KB|MB|GB|TB)$/)).toBeVisible();
  await page.getByRole("button", { name: "About the disk holding the backups" }).click();
  const meter = page.getByRole("meter", { name: "Space used" });
  await expect(meter).toHaveAttribute("aria-valuetext", /^[\d.]+ (B|KB|MB|GB|TB) of [\d.]+ (B|KB|MB|GB|TB)$/);
  await expect(page.getByText(storage.path)).toBeVisible();
  await page.keyboard.press("Escape");
  // Nothing is outside the backup directory on the seeded machine, so there is no notice.
  await expect(page.getByText(/outside the backup directory/)).toHaveCount(0);

  await page.getByRole("link", { name: /^Schedules/ }).click();
  await expect(page).toHaveURL(/\/backups\/schedules$/);
  await expect(page.getByRole("region", { name: "Backup schedules" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the schedules tab");
});

const withMisplacedBackups = test.extend<object, { consoleServer: ConsoleServer }>({
  consoleServer: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const server = await startConsoleServer(["--misplaced-backups"]);
      try {
        await use(server);
      } finally {
        await server.stop();
      }
    },
    { scope: "worker", timeout: 75_000 },
  ],
});

withMisplacedBackups("backups outside the backup directory are named, with the command that imports them", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/backups");
  const storage = (await (await page.request.get("/api/backups/storage")).json()) as {
    misplaced: { directory: string; count: number; command: string }[];
  };
  expect(storage.misplaced).toHaveLength(1);
  const [found] = storage.misplaced;
  if (found === undefined) throw new Error("no misplaced backups seeded");

  const notice = page.locator("[data-tone=warning]").filter({ hasText: `${String(found.count)} backups are outside the backup directory` });
  await expect(notice).toBeVisible();
  await expect(notice.getByText(`${String(found.count)} backups in ${found.directory}`)).toBeVisible();
  expect(found.command).toBe(`noust backup import ${found.directory}`);
  await expect(notice.locator("code").filter({ hasText: found.command })).toBeVisible();
  await expect(notice.getByText(/--dry-run\s*to the command to see what would move first/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the misplaced backups notice");
});
