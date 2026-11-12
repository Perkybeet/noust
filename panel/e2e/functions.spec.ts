/**
 * Three small functions against the real backend (item 164): a task that runs once and retires itself, the origin of a
 * firewall rule changed in one step, and the audit log exported as a CSV from the console with the filter the page has.
 * The sandbox answers systemd, so a task is judged by what the console sends and what the server writes, and the
 * sandbox's firewall is one fixed answer, so a change is undone at once to leave the worker's server as it was seeded.
 */

import { readFile } from "node:fs/promises";

import { elevate } from "./clicks";
import { confirmItsYou, expect, expectNoA11yViolations, settle, signIn, stillness, test, toasts } from "./fixtures";

test("a task that runs once is listed by its moment, and what ran once says how each ended and what it printed", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/cron");
  const table = page.getByRole("region", { name: /^Scheduled tasks/ });
  const once = table.getByRole("row").filter({ has: page.getByText("reindex-search", { exact: true }) });
  await expect(once.getByText(/^Once, /)).toBeVisible();
  // A task that repeats is still said by its words.
  await expect(table.getByRole("row").filter({ has: page.getByText("sitemap", { exact: true }) }).getByText("Every day at 02:00")).toBeVisible();

  const ran = page.getByRole("region", { name: "Ran once" });
  await expect(ran.getByText("migrate-legacy-orders")).toBeVisible();
  const late = ran.getByRole("row").filter({ has: page.getByText("purge-old-exports") });
  await expect(late.getByText("Late")).toBeVisible();
  await expect(late.getByText("exit-code")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the scheduled tasks with what ran once");

  // The units are gone: what the task printed is read from the record.
  await late.getByText("purge-old-exports").click();
  const drawer = page.getByRole("dialog", { name: "What purge-old-exports printed" });
  await expect(drawer.getByText("Permission denied")).toBeVisible();
  await page.keyboard.press("Escape");
});

test("a task is made to run once: the date and the time are the server's, a moment that has passed is refused where it is chosen", async ({ page, consoleServer, problems }) => {
  // The moment that has passed is refused by the manager, and a task is a root-equivalent change that asks for sudo mode.
  problems.expect(/status of 400 .*\/api\/cron\/preview$/);
  problems.expect(/status of 403 .* \/api\/cron$/);
  await signIn(page, consoleServer, "/cron");
  await page.getByRole("button", { name: "New task" }).first().click();
  const dialog = page.getByRole("dialog", { name: "New scheduled task" });
  await dialog.getByLabel("Name", { exact: true }).fill("e2e-once");
  await dialog.getByLabel("Command", { exact: true }).fill("/usr/bin/true");
  await dialog.getByRole("radio", { name: "Once" }).check();

  // Tomorrow is what the form starts on: the preview says it is once, and on whose clock.
  await expect(dialog.getByText("Runs once, then retires itself")).toBeVisible();
  await expect(dialog.getByText(/On the server's clock \(UTC/)).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "the new task dialog, to run once");

  await dialog.getByLabel("Date").fill("2020-01-01");
  await expect(dialog.getByText(/has already passed on this server/)).toBeVisible();
  const date = new Date(Date.now() + 5 * 86_400_000).toISOString().slice(0, 10);
  await dialog.getByLabel("Date").fill(date);
  await expect(dialog.getByText("Runs once, then retires itself")).toBeVisible();

  const created = page.waitForResponse((response) => response.url().endsWith("/api/cron") && response.request().method() === "POST" && response.status() !== 403);
  await dialog.getByRole("button", { name: "Create task" }).click();
  await confirmItsYou(page, consoleServer);
  const response = await created;
  expect(response.status()).toBe(201);
  const body = (await response.json()) as { job: { once: boolean; once_at: string; schedule: string } };
  expect(body.job).toMatchObject({ once: true, schedule: "once" });
  expect(body.job.once_at.startsWith(date)).toBe(true);

  await expect(toasts(page).getByText("Created e2e-once")).toBeVisible();
  await expect(page.getByRole("row").filter({ has: page.getByText("e2e-once", { exact: true }) }).getByText(/^Once, /)).toBeVisible();
});

test("the origin of a firewall rule is changed in one dialog, as one change that waits once", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/network/firewall");
  const rules = page.getByRole("table", { name: /rules/i }).first();
  const row = rules.getByRole("row").filter({ hasText: "temporary: migration from the old host" });
  await expect(row).toContainText("5432");
  await row.getByRole("button", { name: /^Actions for/ }).click();
  await page.getByRole("menuitem", { name: "Change where it admits from" }).click();

  const dialog = page.getByRole("alertdialog", { name: "Change where 5432/tcp admits from" });
  await expect(dialog.getByText(/as one change that you confirm once/)).toBeVisible();
  await expect(dialog.getByText("Anywhere")).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "the dialog that changes where a rule admits from");

  // What is not an address is said where it was typed, and nothing is asked of the server.
  await dialog.getByRole("textbox", { name: "Address or network" }).fill("office");
  await expect(dialog.getByText("That is not an address or a network.")).toBeVisible();
  await dialog.getByRole("textbox", { name: "Address or network" }).fill("203.0.113.0/24");

  await elevate(page, consoleServer);
  const asked = page.waitForRequest((request) => /\/api\/server\/security\/firewall\/rules\/[^/]+\/source$/.test(request.url()) && request.method() === "POST");
  await dialog.getByRole("button", { name: "Change the origin" }).click();
  expect((await asked).postDataJSON()).toEqual({ source: "203.0.113.0/24" });
  await expect(dialog).toBeHidden();

  // One change, kept once or undone once: the page says so and the operator takes it back at once.
  await expect(page.getByText(/A firewall change undoes itself in/)).toBeVisible();
  await page.getByRole("button", { name: "Undo now" }).click();
  await page.getByRole("alertdialog", { name: "Undo the change now?" }).getByRole("button", { name: "Undo the change" }).click();
  await expect(page.getByText(/A firewall change undoes itself in/)).toBeHidden();
});

test("the audit log is exported as a CSV from the page, with the filter that is on, and the export is recorded", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/settings/audit?result=ok");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await settle(page);

  await page.getByRole("button", { name: "Export" }).click();
  await expect(page.getByText("The events these filters and this search keep, with what each one recorded.")).toBeVisible();
  await expectNoA11yViolations(page, "the Audit page with its export menu open");
  const download = page.waitForEvent("download");
  await page.getByRole("menuitem", { name: /CSV, for a spreadsheet/ }).click();
  const file = await download;

  expect(file.suggestedFilename()).toBe("noust-audit.csv");
  const text = await readFile(await file.path(), "utf8");
  // RFC 4180: a CSV ends its lines with CRLF, which is what a spreadsheet expects.
  const [header = "", ...rows] = text.trim().split("\r\n");
  expect(header).toBe("seq,ts,actor,action,resource,result,ip,corr,mac,detail");
  expect(rows.length).toBeGreaterThan(0);

  // The export is itself an event of the trail: who asked, the format and the filter.
  const events = await page.request.get("/api/audit?action=audit.export");
  const recorded = ((await events.json()) as { items: { details?: { format?: string; filter?: Record<string, string> } }[] }).items[0];
  expect(recorded?.details?.format).toBe("csv");
  expect(recorded?.details?.filter).toEqual({ result: "ok" });
});
