/**
 * Backup destinations against the real backend: the seeded SFTP server and the encrypted
 * S3-compatible bucket (scripts/console_server.py, seed_backup_destinations), adding one,
 * testing it (a reachable one and one whose host no resolver knows), browsing what a
 * destination holds and restoring from it, the encrypted one's key, a schedule that copies to
 * both, and copying a local backup to one. rclone is answered from a directory per remote in
 * the sandbox, so a copy, a listing and a restore move real files and their jobs finish.
 *
 * Tests share one backend per worker, so each one creates what it changes under its own name
 * and reads counts from the API, never from the seed.
 */

import type { Page, TestInfo } from "@playwright/test";

import { confirmItsYou, expect, expectNoA11yViolations, settle, signIn, test, toasts } from "./fixtures";

const SFTP = "offsite-sftp";
const ENCRYPTED = "vault-r2";

function destinations(page: Page) {
  return page.getByRole("region", { name: "Backup destinations" });
}

function destinationRow(page: Page, name: string) {
  return destinations(page).getByRole("row").filter({ has: page.getByRole("cell", { name, exact: true }) });
}

async function openActions(page: Page, name: string): Promise<void> {
  await page.getByRole("button", { name: `Actions for ${name}`, exact: true }).click();
}

/** A destination name no other test (or theme project) uses: lowercase, digits and '-'. */
function uniqueName(prefix: string, testInfo: TestInfo): string {
  return `${prefix}-${testInfo.project.name}-${String(Date.now() % 1_000_000)}`;
}

/** Waits for a queued job to finish, and fails with its error when it does not complete. */
async function expectJobCompletes(page: Page, jobId: string): Promise<void> {
  await expect
    .poll(
      async () => {
        const job = (await (await page.request.get(`/api/jobs/${jobId}`)).json()) as { status: string; error: string | null };
        return job.status === "failed" ? `failed: ${job.error ?? ""}` : job.status;
      },
      { timeout: 30_000 },
    )
    .toBe("completed");
}

test("both seeded destinations are listed, with what is encrypted, and the page passes axe", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/backups/destinations");
  await expect(page.getByRole("link", { name: /^Destinations/ })).toHaveAttribute("data-status", "active");

  const sftp = destinationRow(page, SFTP);
  await expect(sftp.getByRole("cell", { name: "SFTP server" })).toBeVisible();
  await expect(sftp.getByText("Not encrypted")).toBeVisible();
  await expect(sftp.getByRole("cell", { name: "/srv/backups/web-01" })).toBeVisible();
  await expect(sftp.getByText("Not tested")).toBeVisible();

  const encrypted = destinationRow(page, ENCRYPTED);
  await expect(encrypted.getByRole("cell", { name: "S3-compatible storage" })).toBeVisible();
  await expect(encrypted.getByText("Encrypted", { exact: true })).toBeVisible();

  await settle(page);
  await expectNoA11yViolations(page, "the destinations section");
});

test("testing a destination says it is reachable and what is there", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/backups/destinations");
  await openActions(page, SFTP);
  const tested = page.waitForResponse((response) => response.url().endsWith(`/api/backup-destinations/${SFTP}/test`));
  await page.getByRole("menuitem", { name: "Test" }).click();
  expect((await tested).status()).toBe(200);

  await expect(destinationRow(page, SFTP).getByText("Reachable", { exact: true })).toBeVisible();
  const toast = toasts(page).locator(".toast").filter({ hasText: `${SFTP} is reachable` });
  await expect(toast).toBeVisible();
  // The folders rclone listed at the destination's path: one per application backed up there.
  await expect(toast).toContainText("shop-example-net/");
  await settle(page);
  await expectNoA11yViolations(page, "a tested destination");
});

test("adding an SFTP destination lists it; one that cannot be reached says so in rclone's words", async ({ page, consoleServer, problems }, testInfo) => {
  // Creating a destination is sudo mode: the first attempt is refused until the operator confirms.
  problems.expect(/status of 403 .*\/api\/backup-destinations$/);
  // Testing the unreachable one fails on purpose, with rclone's own error: a server-side
  // status (a BackupError answers 500 today; an upstream failure may well become 502).
  problems.expect(/status of [45]\d\d .*\/api\/backup-destinations\/[a-z0-9-]+\/test$/);
  const name = uniqueName("old-nas", testInfo);
  await signIn(page, consoleServer, "/backups/destinations");

  await page.getByRole("button", { name: "Add destination" }).click();
  const dialog = page.getByRole("dialog", { name: "Add backup destination" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Add destination" })).toBeDisabled();

  await dialog.getByLabel("Name", { exact: true }).fill(name);
  await dialog.getByRole("combobox", { name: "Backend" }).click();
  await page.getByRole("option", { name: /^SFTP server/ }).click();
  await dialog.getByLabel("Host", { exact: true }).fill("backup.old.invalid");
  await dialog.getByLabel("User", { exact: true }).fill("wasm");
  await dialog.getByLabel("Remote folder").fill("/volume1/backups");
  await settle(page);
  await expectNoA11yViolations(page, "the add destination dialog");

  const created = page.waitForRequest((request) => request.url().endsWith("/api/backup-destinations") && request.method() === "POST");
  await dialog.getByRole("button", { name: "Add destination" }).click();
  await confirmItsYou(page, consoleServer);
  expect((await created).postDataJSON()).toMatchObject({
    name,
    backend: "sftp",
    // Every field of the form, blank ones included (a blank secret alone is left out: it keeps
    // the stored value on an edit).
    fields: { host: "backup.old.invalid", user: "wasm", port: "", key_file: "", path: "/volume1/backups" },
    encrypted: false,
  });
  await expect(dialog).toBeHidden();
  await expect(toasts(page).getByText(`Backup destination created: ${name}`, { exact: true })).toBeVisible();

  const row = destinationRow(page, name);
  await expect(row.getByRole("cell", { name: "/volume1/backups" })).toBeVisible();

  await openActions(page, name);
  await page.getByRole("menuitem", { name: "Test" }).click();
  await expect(row.getByText("Unreachable", { exact: true })).toBeVisible();
  const toast = toasts(page).locator(".toast").filter({ hasText: `${name} could not be reached` });
  await expect(toast).toBeVisible();
  // rclone's own error, verbatim: the name it could not resolve.
  await expect(toast).toContainText("lookup backup.old.invalid: no such host");
  await settle(page);
  await expectNoA11yViolations(page, "an unreachable destination");
});

test("browsing a destination lists its applications and backups, and restores one from it", async ({ page, consoleServer, problems }) => {
  problems.expect(/status of 403 .*\/api\/backup-destinations\/.*\/restore$/);
  await signIn(page, consoleServer, "/backups/destinations");
  await openActions(page, SFTP);
  await page.getByRole("menuitem", { name: "Browse" }).click();

  const dialog = page.getByRole("dialog", { name: "Browse a destination" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("combobox", { name: "Destination" })).toContainText(SFTP);
  await expect(dialog.getByRole("button", { name: "example-com" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a destination's applications");

  await dialog.getByRole("button", { name: "shop-example-net" }).click();
  const backups = dialog.getByRole("region", { name: `Backups on ${SFTP} for shop-example-net` });
  const rows = backups.getByRole("row").filter({ hasNot: page.getByRole("columnheader") });
  // The two copies of the local backups, and an older one only the destination still has.
  await expect(rows).toHaveCount(3);
  await settle(page);
  await expectNoA11yViolations(page, "a destination's backups of one application");

  // The oldest: the one there is no local copy of, which is why a destination is restored from.
  const oldest = rows.last();
  const backupId = (await oldest.getByRole("cell").first().innerText()).trim();
  expect(backupId).toMatch(/^shop-example-net_\d{8}_\d{6}$/);
  await oldest.getByRole("button", { name: "Restore" }).click();

  const confirm = page.getByRole("dialog", { name: `Restore from ${SFTP}` });
  await expect(confirm).toBeVisible();
  await expect(confirm.getByText(backupId)).toBeVisible();
  const restore = confirm.getByRole("button", { name: "Restore" });
  await expect(restore).toBeDisabled();
  // The folder is named after the application; the target offered is its domain.
  await expect(confirm.getByLabel("Restore into")).toHaveValue("shop.example.net");
  await confirm.getByLabel(/to confirm$/).fill("shop.example.net");
  await expect(restore).toBeEnabled();
  await settle(page);
  await expectNoA11yViolations(page, "restoring from a destination");

  const requested = page.waitForRequest(
    (request) => request.url().endsWith(`/api/backup-destinations/${SFTP}/backups/${backupId}/restore`) && request.method() === "POST",
  );
  const accepted = page.waitForResponse(
    (response) => response.url().endsWith(`/backups/${backupId}/restore`) && response.status() === 202,
  );
  await restore.click();
  await confirmItsYou(page, consoleServer);
  expect((await requested).postDataJSON()).toMatchObject({ app_name: "shop-example-net", target_domain: null, restore_env: true });
  await expect(toasts(page).getByText(`Restore from ${SFTP} queued for ${backupId}`, { exact: true })).toBeVisible();
  const { job_id: jobId } = (await (await accepted).json()) as { job_id: string };
  await expectJobCompletes(page, jobId);
});

test("the encrypted destination's key is shown after confirming it's you, and kept until saved", async ({ page, consoleServer, problems }) => {
  problems.expect(/status of 403 .*\/api\/backup-destinations\/vault-r2\/show-key$/);
  await signIn(page, consoleServer, "/backups/destinations");
  await openActions(page, SFTP);
  // Only an encrypted destination has a key to show.
  await expect(page.getByRole("menuitem", { name: "Show encryption key" })).toHaveCount(0);
  await page.keyboard.press("Escape");

  await openActions(page, ENCRYPTED);
  await page.getByRole("menuitem", { name: "Show encryption key" }).click();
  await confirmItsYou(page, consoleServer);

  const dialog = page.getByRole("dialog", { name: `Encryption key for ${ENCRYPTED}` });
  await expect(dialog).toBeVisible();
  // Two generated passphrases (token_urlsafe(32)), shown whole.
  await expect(dialog.locator("dd")).toHaveCount(2);
  for (const value of await dialog.locator("dd").allInnerTexts()) expect(value.trim()).toMatch(/^[A-Za-z0-9_-]{40,}$/);
  const done = dialog.getByRole("button", { name: "Done" });
  await expect(done).toBeDisabled();

  // Leaving without saying it was saved is refused, with the reason.
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  await expect(dialog.locator("[data-tone=warning]")).toContainText("Losing it makes every backup on this destination unrecoverable");
  await settle(page);
  await expectNoA11yViolations(page, "the encryption key dialog");

  await dialog.getByRole("checkbox", { name: "I have saved this key somewhere safe" }).click();
  await done.click();
  await expect(dialog).toBeHidden();
});

test("a schedule copies to both destinations, each with its own retention", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/backups/schedules");
  const schedules = page.getByRole("region", { name: "Backup schedules" });
  const row = schedules.getByRole("row").filter({ has: page.getByRole("cell", { name: "shop.example.net", exact: true }) });
  await expect(row.getByText("The last 7 backups")).toBeVisible();
  await expect(row.getByText(`${SFTP}, ${ENCRYPTED}`, { exact: true })).toBeVisible();
  // The schedule in words, over its calendar expression as written.
  await expect(row.getByText("Every day at 02:00", { exact: true })).toBeVisible();
  await expect(row.getByText("*-*-* 02:00:00", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Actions for the schedule on shop.example.net" }).click();
  await page.getByRole("menuitem", { name: "Edit" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit the schedule for shop.example.net" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel(`Keep on ${SFTP}`)).toHaveValue("14");
  await expect(dialog.getByLabel(`Delete after (days) on ${SFTP}`)).toHaveValue("90");
  await expect(dialog.getByLabel(`Keep on ${ENCRYPTED}`)).toHaveValue("30");
  await expect(dialog.getByLabel(`Delete after (days) on ${ENCRYPTED}`)).toHaveValue("");
  await settle(page);
  await expectNoA11yViolations(page, "a schedule with destinations");
});

test("copying a local backup to a destination queues a job that finishes", async ({ page, consoleServer, problems }) => {
  problems.expect(/status of 403 .*\/api\/backups\/.*\/push$/);
  // A backup's own actions are in its application's drawer.
  await signIn(page, consoleServer, "/backups?domain=example.com");

  const listed = (await (await page.request.get("/api/backups")).json()) as { backups: { backup_id: string; domain: string }[] };
  const backup = listed.backups.find((entry) => entry.domain === "example.com");
  if (backup === undefined) throw new Error("the seed has no backup of example.com");

  await page.getByRole("button", { name: `Actions for ${backup.backup_id}`, exact: true }).click();
  await page.getByRole("menuitem", { name: "Copy to destination…" }).click();
  const dialog = page.getByRole("dialog", { name: `Copy ${backup.backup_id}` });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Copy" })).toBeDisabled();
  await dialog.getByRole("combobox", { name: "Destination" }).click();
  await page.getByRole("option", { name: ENCRYPTED, exact: true }).click();
  await settle(page);
  await expectNoA11yViolations(page, "the copy to destination dialog");

  const requested = page.waitForRequest((request) => request.url().endsWith(`/api/backups/${backup.backup_id}/push`) && request.method() === "POST");
  const accepted = page.waitForResponse((response) => response.url().endsWith(`/api/backups/${backup.backup_id}/push`) && response.status() === 202);
  await dialog.getByRole("button", { name: "Copy" }).click();
  await confirmItsYou(page, consoleServer);
  expect((await requested).postDataJSON()).toMatchObject({ destination: ENCRYPTED });
  await expect(dialog).toBeHidden();
  await expect(toasts(page).getByText(`Upload of ${backup.backup_id} to ${ENCRYPTED} queued`, { exact: true })).toBeVisible();
  const { job_id: jobId } = (await (await accepted).json()) as { job_id: string };
  await expectJobCompletes(page, jobId);

  // What the destination holds now includes it, as Browse lists it.
  const remote = (await (await page.request.get(`/api/backup-destinations/${ENCRYPTED}/backups?app=example-com`)).json()) as {
    backups: { backup_id: string }[];
  };
  expect(remote.backups.map((entry) => entry.backup_id)).toContain(backup.backup_id);
});
