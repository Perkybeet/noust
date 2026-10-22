/**
 * Noust's own backup (checklist H-27): the state a lost server cannot do without, sealed with a
 * passphrase in one file, taken by hand or on a schedule, checked, and restored only from a
 * terminal, which the console shows the command for. Against the real backend and its sandboxed
 * machine, where the bundles are real files sealed by the real cipher.
 */

import type { Page } from "@playwright/test";

import { confirmItsYou, expect, expectNoA11yViolations, settle, test } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

const PASSPHRASE = "correct horse battery staple";

async function signIn(page: Page, server: ConsoleServer, path: string): Promise<void> {
  const answer = await page.request.post("/api/auth/login", { data: { token: server.token, totp_code: server.secondFactor() } });
  expect(answer.ok()).toBe(true);
  await page.goto(path);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}

test("Noust's own backup: a bundle by hand, checked, how it is restored, and a schedule that keeps its passphrase", async ({ page, consoleServer, problems }) => {
  // Storing the passphrase for a timer is behind sudo mode: the first answer is the 403 that asks for it.
  problems.expect(/status of 403 .*\/api\/self-backup\/schedule$/);
  await signIn(page, consoleServer, "/backups/noust");
  await expect(page.getByRole("heading", { name: "Schedule", level: 2 }).locator("xpath=ancestor::section[1]").getByText("Not scheduled")).toBeVisible();
  await expect(page.getByText("No bundles yet")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Noust's own backup, nothing yet");

  // A bundle by hand: the passphrase is typed twice, and kept nowhere.
  await page.getByRole("button", { name: "Back up Noust now" }).click();
  const seal = page.getByRole("dialog", { name: "Seal a bundle" });
  await seal.getByLabel("Passphrase", { exact: true }).fill("too short");
  await seal.getByLabel("Repeat the passphrase").fill("too short");
  await seal.getByRole("button", { name: "Back up now" }).click();
  await expect(seal.getByText("Use at least 12 characters.")).toBeVisible();
  await seal.getByLabel("Passphrase", { exact: true }).fill(PASSPHRASE);
  await seal.getByLabel("Repeat the passphrase").fill(`${PASSPHRASE}!`);
  await seal.getByRole("button", { name: "Back up now" }).click();
  await expect(seal.getByText("The two passphrases are not the same.")).toBeVisible();
  await seal.getByLabel("Repeat the passphrase").fill(PASSPHRASE);
  await settle(page);
  await expectNoA11yViolations(page, "the passphrase dialog");
  await seal.getByRole("button", { name: "Back up now" }).click();
  await expect(seal).toBeHidden();

  const bundles = page.getByRole("region", { name: "Bundles of Noust's own state" });
  const row = bundles.getByRole("row", { name: /noust-self-.*\.tar\.gz\.enc/ });
  await expect(row).toBeVisible();
  await expect(row.getByText("By hand")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Noust's own backup, a bundle");

  // Checking a bundle asks for the passphrase it was sealed with, and says it can be restored.
  await row.getByRole("button", { name: /^Actions for noust-self-/ }).click();
  await page.getByRole("menuitem", { name: "Check it" }).click();
  const check = page.getByRole("dialog", { name: /^Check noust-self-/ });
  await check.getByLabel("Passphrase", { exact: true }).fill(PASSPHRASE);
  await check.getByRole("button", { name: "Check bundle" }).click();
  await expect(page.getByText(/can be restored$/).first()).toBeVisible();

  // A restore replaces the store this console runs on: it is never done from here, and the command is shown.
  await row.getByRole("button", { name: /^Actions for noust-self-/ }).click();
  await page.getByRole("menuitem", { name: "How to restore…" }).click();
  const restore = page.getByRole("dialog", { name: /^Restore noust-self-/ });
  await expect(restore.getByText(/It is run from a terminal on this server/)).toBeVisible();
  await expect(restore.getByText(/noust self-backup restore noust-self-.*\.tar\.gz\.enc/)).toBeVisible();
  await expect(restore.getByText(/noust restore-server --from noust-self-.*\.tar\.gz\.enc/)).toBeVisible();
  await expect(restore.getByRole("button", { name: /^Restore/ })).toHaveCount(0);
  await settle(page);
  await expectNoA11yViolations(page, "how a bundle is restored");
  await restore.getByRole("button", { name: "Close" }).first().click();
  await expect(restore).toBeHidden();

  // A schedule: the passphrase is kept for the timer, and said to be.
  await page.getByRole("button", { name: "Set a schedule" }).click();
  const schedule = page.getByRole("dialog", { name: "Backups of Noust's own state" });
  await expect(schedule.getByText(/Keep a copy outside this server/)).toBeVisible();
  await schedule.getByRole("button", { name: "Save schedule" }).click();
  await expect(schedule.getByText("Type the passphrase bundles are sealed with.")).toBeVisible();
  await schedule.getByLabel("Passphrase", { exact: true }).fill(PASSPHRASE);
  await settle(page);
  await expectNoA11yViolations(page, "the schedule of Noust's own backup");
  await schedule.getByRole("button", { name: "Save schedule" }).click();
  await expect(schedule.getByText("The two passphrases are not the same.")).toBeVisible();
  await schedule.getByLabel("Repeat the passphrase").fill(PASSPHRASE);
  await schedule.getByRole("button", { name: "Save schedule" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(schedule).toBeHidden();
  await expect(page.getByRole("heading", { name: "Schedule", level: 2 }).locator("xpath=ancestor::section[1]").getByText("On", { exact: true })).toBeVisible();
  await expect(page.getByText("Kept on this server, for the timer")).toBeVisible();

  // Running it by hand takes a scheduled bundle, sealed with the kept passphrase.
  await page.getByRole("button", { name: "Run it now" }).click();
  await expect(bundles.getByRole("row", { name: /noust-self-.*\.tar\.gz\.enc/ }).getByText("Scheduled")).toBeVisible();

  // Nothing is left scheduled for what runs after this on the same server.
  await page.getByRole("button", { name: "Remove the schedule" }).click();
  await page.getByRole("alertdialog", { name: "Remove the schedule" }).getByRole("button", { name: "Remove schedule" }).click();
  await expect(page.getByRole("heading", { name: "Schedule", level: 2 }).locator("xpath=ancestor::section[1]").getByText("Not scheduled")).toBeVisible();
});
