/**
 * Volume backups (checklist H-23): an application's persistent folders and Docker volumes backed
 * up on their own. The policy is made from what the application declares, the schedule and the
 * retention are the ones of every backup policy, a backup runs by hand as a job, and a restore
 * is a decision the operator confirms. Against the real backend and its sandboxed machine, which
 * really writes the archives and the timer units.
 */

import type { Page } from "@playwright/test";

import { confirmItsYou, expect, expectNoA11yViolations, settle, test } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

/** The application whose data the sandbox declares: its `uploads` folder, kept across releases. */
const APP = "tienda.example.org";

async function signIn(page: Page, server: ConsoleServer, path: string): Promise<void> {
  const answer = await page.request.post("/api/auth/login", { data: { token: server.token, totp_code: server.secondFactor() } });
  expect(answer.ok()).toBe(true);
  await page.goto(path);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}

test("volume backups: a policy from what the application declares, run by hand, checked and restored on confirmation", async ({ page, consoleServer, problems }) => {
  // The policy, the restore and the removal are behind sudo mode: the first answer is the 403 that asks for it.
  problems.expect(/status of 403 .*\/api\/volume-backups\//);
  await signIn(page, consoleServer, "/backups/volumes");
  await expect(page.getByRole("tab", { name: "Volumes" }).or(page.getByRole("link", { name: "Volumes" })).first()).toBeVisible();
  await expect(page.getByText("No data backups yet")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the volumes tab, no policy yet");

  // The policy: the pieces are the ones the application declares, picked, not typed.
  await page.getByRole("button", { name: "Set a schedule" }).click();
  const drawer = page.getByRole("dialog", { name: "New data schedule" });
  await drawer.getByRole("combobox", { name: "Application" }).click();
  await page.getByRole("option", { name: APP }).click();
  await drawer.getByRole("radio", { name: /^Only the pieces I choose/ }).check();
  await expect(drawer.getByText("Declared by persistent path")).toBeVisible();
  await drawer.getByRole("button", { name: "Save schedule" }).click();
  await expect(drawer.getByText("Choose at least one piece, or back up everything it declares.")).toBeVisible();
  await drawer.getByRole("checkbox", { name: /^uploads/ }).check();
  await drawer.getByRole("textbox", { name: /^More folders/ }).fill("../outside");
  await drawer.getByRole("button", { name: "Save schedule" }).click();
  await expect(drawer.getByText(/A folder is a relative path inside the application's data, without \.\. or a leading slash: \.\.\/outside\./)).toBeVisible();
  await drawer.getByRole("textbox", { name: /^More folders/ }).fill("media/cache");
  await drawer.getByRole("radio", { name: /^Pause it/ }).check();
  // Where else the backups go: the destinations are the ones of every other backup.
  await drawer.getByRole("checkbox", { name: "offsite-sftp" }).check();
  await settle(page);
  await expectNoA11yViolations(page, "the new data policy");
  await drawer.getByRole("button", { name: "Save schedule" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(drawer).toBeHidden();

  const table = page.getByRole("region", { name: "Application data and its backups" });
  const row = table.getByRole("row", { name: new RegExp(APP.replace(/\./g, "\\.")) });
  await expect(row.getByText("Scheduled")).toBeVisible();
  await expect(row.getByText("2 pieces of data")).toBeVisible();

  // Run it now: a job that is followed, and a backup that appears.
  await row.getByRole("button", { name: `Actions for ${APP}` }).click();
  await page.getByRole("menuitem", { name: "Back up now" }).click();
  await expect(row.getByText("Backed up")).toBeVisible();

  await row.getByRole("button", { name: `Actions for ${APP}` }).click();
  await page.getByRole("menuitem", { name: "Open its backups" }).click();
  const detail = page.getByRole("dialog", { name: APP });
  await expect(detail.getByText("While it copies")).toBeVisible();
  await expect(detail.getByText("Pause it")).toBeVisible();
  await expect(detail.getByText("The last 7 backups, for up to 30 days").first()).toBeVisible();
  await expect(detail.locator("dl").getByText("offsite-sftp")).toBeVisible();
  const backups = detail.getByRole("table", { name: `Data backups of ${APP}` });
  const first = backups.getByRole("row").nth(1);
  await expect(first.getByText("Scheduled")).toBeVisible();
  // It was sent where the policy says, and the row says where it is.
  await expect(first.getByText("offsite-sftp")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "an application's data backups");

  // Checking it is a job of its own.
  await first.getByRole("button", { name: /^Actions for / }).click();
  await page.getByRole("menuitem", { name: "Check it" }).click();
  await expect(first.getByText("Checked")).toBeVisible();

  // Restoring replaces what is there, and says so before it does.
  await first.getByRole("button", { name: /^Actions for / }).click();
  await page.getByRole("menuitem", { name: "Restore…" }).click();
  const restore = page.getByRole("alertdialog", { name: `Restore the data of ${APP}` });
  await expect(restore.getByText(/replaces what is there: the data is not merged/)).toBeVisible();
  await expect(restore.getByRole("checkbox", { name: /^Take a copy of what is there first/ })).toBeChecked();
  await settle(page);
  await expectNoA11yViolations(page, "the volume restore confirmation");
  // The application's name is typed to say the data it holds is meant to be replaced.
  await expect(restore.getByRole("button", { name: "Restore data" })).toBeDisabled();
  await restore.getByRole("textbox").fill(APP);
  // Sudo mode confirmed for the policy a moment ago still holds, so nothing is asked again.
  await restore.getByRole("button", { name: "Restore data" }).click();
  await expect(restore).toBeHidden();
  await expect(page.getByText(`Put the data of ${APP} back`).first()).toBeVisible();

  // Nothing is left scheduled for what runs after this on the same server.
  await detail.getByRole("button", { name: `Actions for ${APP}` }).click();
  await page.getByRole("menuitem", { name: "Remove the schedule" }).click();
  await page.getByRole("alertdialog", { name: `Remove the data schedule of ${APP}` }).getByRole("button", { name: "Remove schedule" }).click();
  await expect(detail.getByText(/Nothing backs this data up on a schedule/)).toBeVisible();
});
