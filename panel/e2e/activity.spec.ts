/**
 * Activity against the real backend: the jobs history and the audit log merged into one
 * timeline, filtering, and opening a job's captured log.
 *
 * Signing in is itself audited (`noust.web.auth`'s login endpoint records `auth.login`), so
 * every test that calls `signIn` has already produced one real audit row before it navigates
 * here - this is what the first test asserts, rather than relying only on what
 * `scripts/console_server.py` seeds ahead of time.
 */

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

test("merges the jobs history and the audit log, newest first, including this sign-in", async ({ page, consoleServer }) => {
  // Everything: the page opens on operations, and a sign-in is access.
  await signIn(page, consoleServer, "/activity?kind=all");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Activity");

  const table = page.getByRole("region", { name: /^Activity/ });
  await expect(table.getByRole("row").filter({ hasNot: page.getByRole("columnheader") }).first()).toBeVisible();

  // The sign-in this test just performed, audited by the real login endpoint. The timeline is
  // newest first and the worker's backend may carry other tests' sign-ins too, so this is the
  // most recent one - which, run just before navigating here, is this test's own.
  const signInRow = table.getByRole("row").filter({ has: page.getByText("Sign-in", { exact: true }) }).first();
  await expect(signInRow).toBeVisible();
  await expect(signInRow.getByText("Succeeded")).toBeVisible();

  // A seeded job (the backup of shop.example.net), so the merge is proven with both kinds of row
  // on screen at once.
  await expect(table.getByRole("row").filter({ has: page.getByText("shop.example.net") }).first()).toBeVisible();

  await settle(page);
  await expectNoA11yViolations(page, "the activity timeline");

  // Operations, the page's own view, leaves the sign-ins out and keeps the jobs.
  await page.getByRole("radio", { name: "Operations" }).click();
  await expect(page).toHaveURL(/\/activity$/);
  await expect(table.getByText("Sign-in", { exact: true })).toHaveCount(0);
  await expect(table.getByRole("row").filter({ has: page.getByText("shop.example.net") }).first()).toBeVisible();
});

test("filtering by result narrows the timeline to one source", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/activity");
  const table = page.getByRole("region", { name: /^Activity/ });
  const rows = () => table.getByRole("row").filter({ hasNot: page.getByRole("columnheader") });
  // count() does not wait like toBeVisible() does, and a first row is no proof of a loaded
  // list: while a query is pending the table draws placeholder rows, real <tr>s, marked only
  // by the table's aria-busy. Counted then, the placeholders (or the rows of the previous
  // filter) were read as the result, and a later nth() found nothing.
  const loaded = () => expect(table.getByRole("table")).not.toHaveAttribute("aria-busy", "true");
  await expect(rows().first()).toBeVisible();
  await loaded();
  const before = await rows().count();
  expect(before).toBeGreaterThan(0);

  await page.getByRole("combobox", { name: "Result" }).click();
  await page.getByRole("option", { name: "Job: Failed", exact: true }).click();
  await expect(page).toHaveURL(/\/activity\?result=failed$/);
  // Only the filtered list has no row without "Failed": placeholders and the unfiltered rows do.
  await expect(rows().filter({ hasNot: page.getByText("Failed", { exact: true }) })).toHaveCount(0);
  await expect(rows().first()).toBeVisible();
  await loaded();
  const count = await rows().count();
  expect(count).toBeLessThanOrEqual(before);
  for (let i = 0; i < count; i += 1) {
    await expect(rows().nth(i).getByText("Failed", { exact: true })).toBeVisible();
  }
  // A job-only result excludes the audit log entirely: this sign-in's own row is gone.
  await expect(table.getByText("Sign-in", { exact: true })).toHaveCount(0);

  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page).toHaveURL(/\/activity$/);
});

test("opening a job with a captured log shows it verbatim", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/activity");
  const table = page.getByRole("region", { name: /^Activity/ });
  // The seeded update of example.com that kept its log: the job the master token started. The
  // row names it in the console's words (Update, example.com); the server's English description
  // is not repeated beside them.
  const view = page.getByRole("button", { name: "View log of Update example.com", exact: true });
  const row = table.getByRole("row").filter({ has: view }).filter({ has: page.getByText("master", { exact: true }) });
  await expect(row).toBeVisible();
  await expect(row.getByText("Updating the application at example.com")).toHaveCount(0);
  await row.getByRole("button", { name: /View log of/ }).click();

  const drawer = page.getByRole("dialog", { name: "Update example.com" });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByText(/Updating example\.com/)).toBeVisible();
  await expect(drawer.getByText(/Update of example\.com finished/)).toBeVisible();
  await expectNoA11yViolations(page, "a job's log");
});
