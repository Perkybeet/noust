/**
 * The databases area against the real backend and its seeded machine (scripts/console_server.py
 * models example_production's tables, metrics, accounts and dumps): the list and the engines,
 * a database's page tab by tab, the data browser and its row editor, the SQL console, backups
 * and restores as jobs, accounts, how to connect, an application's Database tab and the
 * New-application wizard's Database step. Runs in both themes (playwright.config.ts's two
 * projects), with the CSP and console gates of the `problems` fixture; each page is checked
 * with axe once.
 */

import type { Page } from "@playwright/test";

import { confirmItsYou, expect, expectNoA11yViolations, settle, test } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

const DATABASE = "/databases/postgresql/example_production";

/**
 * Signs in through the API with the master token and its second factor, then opens `path`:
 * these tests are about the databases pages, not the sign-in page, which has its own spec.
 */
async function signIn(page: Page, server: ConsoleServer, path: string): Promise<void> {
  const answer = await page.request.post("/api/auth/login", { data: { token: server.token, totp_code: server.secondFactor() } });
  expect(answer.ok()).toBe(true);
  await page.goto(path);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}

test("the list says which databases are backed up, and the engines have a tab of their own", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/databases");
  await expect(page.getByRole("heading", { level: 1, name: "Databases" })).toBeVisible();
  const table = page.getByRole("region", { name: "Databases" });
  const production = table.getByRole("row", { name: /example_production/ });
  await expect(production.getByText("Backed up")).toBeVisible();
  await expect(production.getByRole("link", { name: "example.com" })).toBeVisible();
  await expect(table.getByRole("row", { name: /example_staging/ }).getByText("No backups scheduled")).toBeVisible();
  // Nothing but filters and one notice sits between the header and the table (T1): what
  // configures the list, the engines, is a subset tab, not a strip above it.
  await expect(page.getByRole("navigation", { name: "Engines" })).toHaveCount(0);
  await expect(page.getByText(/databases? (has|have) no backup schedule/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the databases list");

  await page.getByRole("navigation", { name: "Databases sections" }).getByRole("link", { name: "Engines" }).click();
  await expect(page).toHaveURL(/\/databases\/engines$/);
  const list = page.getByRole("region", { name: "Database engines" });
  await expect(list.getByRole("row", { name: /MongoDB/ })).toBeVisible();
  await expect(list.getByRole("row", { name: /MySQL\/MariaDB/ }).getByText(/Upstream support for version 8\.0 ended/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the engines tab");
});

test("creating a database opens its page", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/databases");
  await page.getByRole("button", { name: "New database" }).click();
  const dialog = page.getByRole("dialog", { name: "New database" });
  await dialog.getByRole("combobox", { name: "Engine" }).click();
  await page.getByRole("option", { name: /PostgreSQL/ }).click();
  const name = `acme_${String(Date.now()).slice(-6)}`;
  await dialog.getByLabel("Name").fill(name);
  await expectNoA11yViolations(page, "the new database dialog");
  await dialog.getByRole("button", { name: "Create database" }).click();
  await expect(page).toHaveURL(new RegExp(`/databases/postgresql/${name}$`));
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
});

test("a database's overview: its state, figures, application, access and schedule", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, DATABASE);
  await expect(page.getByRole("heading", { level: 1, name: "example_production" })).toBeVisible();
  const tabs = page.getByRole("navigation", { name: "Database sections" });
  await expect(tabs.getByRole("link")).toHaveText(["Overview", "Data", "Query", "Backups", "Users", "Connect", "Metrics"]);
  await expect(page.getByRole("heading", { level: 2, name: "Used by" })).toBeVisible();
  await expect(page.getByText("Every day at 02:00")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a database's overview");
});

test("the data browser pages, sorts and filters a table, and edits one row in sudo mode", async ({ page, consoleServer, problems }) => {
  // The first edit asks "Confirm it's you" (403 elevation_required), then is sent again.
  problems.expect(/status of 403 .*\/api\/databases\/databases\/postgresql\/example_production\/rows$/);
  await signIn(page, consoleServer, `${DATABASE}/data?schema=public&table=orders`);
  const grid = page.getByRole("region", { name: "Rows of public.orders" });
  await expect(grid.getByRole("row")).toHaveCount(51);
  await expect(grid.getByText("NULL").first()).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the data browser");

  await grid.getByRole("button", { name: /^total/ }).click();
  await expect(page).toHaveURL(/sort=total%3Aasc|sort=total:asc/);
  await page.getByRole("button", { name: "Add filter" }).click();
  await page.getByRole("combobox", { name: "Column" }).click();
  await page.getByRole("option", { name: /^status/ }).click();
  await page.getByLabel("Value").fill("refunded");
  await page.getByRole("button", { name: "Apply filter" }).click();
  await expect(page.getByRole("button", { name: "Remove the filter on status" })).toBeVisible();
  await expect(grid.getByRole("row").nth(1)).toContainText("refunded");

  await page.getByRole("button", { name: "Count them" }).click();
  await expect(page.getByText(/rows? match/)).toBeVisible();

  await grid.getByRole("button", { name: "Open row 1", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "Row" });
  await drawer.getByRole("button", { name: "Edit" }).click();
  const status = drawer.getByRole("textbox", { name: /^status/ });
  await status.fill("chargeback");
  await drawer.getByRole("button", { name: "Save row" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(drawer.getByText("Row saved")).toBeVisible();
  await expect(drawer.getByText("chargeback")).toBeVisible();
  await drawer.getByRole("button", { name: "Done" }).click();
});

test("the SQL console reads, explains, and keeps a statement under a name", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `${DATABASE}/query`);
  const editor = page.getByRole("textbox", { name: "Statement to run against example_production" });
  await editor.fill("SELECT id, customer_id, total, status, paid, created_at FROM orders ORDER BY created_at DESC LIMIT 20");
  await editor.press("Control+Enter");
  const grid = page.getByRole("region", { name: "Result rows" });
  await expect(grid.getByRole("row")).toHaveCount(21);
  await expect(page.getByText("20 rows").first()).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the SQL console with a result");

  await page.getByRole("button", { name: "Explain" }).click();
  await page.getByRole("menuitem", { name: "Show the plan", exact: true }).click();
  await expect(page.getByRole("list", { name: "The plan, step by step" })).toContainText("Index Scan Backward");

  await page.getByRole("button", { name: "Save", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Save the statement" });
  await dialog.getByLabel("Name").fill("Newest orders");
  await dialog.getByRole("button", { name: "Save statement" }).click();
  await page.getByRole("tab", { name: /Saved/ }).click();
  await expect(page.getByRole("tabpanel").getByText("Newest orders")).toBeVisible();
  await page.getByRole("tab", { name: /History/ }).click();
  await expect(page.getByRole("tabpanel").getByText(/SELECT id, customer_id/).first()).toBeVisible();
});

test("backups: the schedule in plain words, and a restore into a new database followed as a job", async ({ page, consoleServer, problems }) => {
  problems.expect(/status of 403 .*\/api\/databases\/backups\/restore$/);
  await signIn(page, consoleServer, `${DATABASE}/backups`);
  await expect(page.getByText("Every day at 02:00")).toBeVisible();
  const dumps = page.getByRole("region", { name: "Dumps of example_production" });
  await expect(dumps.getByText("Check failed")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a database's backups");

  await page.getByRole("button", { name: "Edit", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "Edit the backup schedule" });
  await expect(drawer.getByText("Every day at 02:00.")).toBeVisible();
  await expectNoA11yViolations(page, "the schedule drawer");
  await drawer.getByRole("button", { name: "Cancel" }).click();

  await dumps.getByRole("button", { name: /^Actions for postgresql-example_production-/ }).first().click();
  await page.getByRole("menuitem", { name: "Restore…" }).click();
  const dialog = page.getByRole("dialog", { name: "Restore a dump" });
  await expect(dialog.getByLabel("New database name")).not.toHaveValue("");
  await expectNoA11yViolations(page, "the restore dialog");
  await dialog.getByRole("button", { name: "Restore as new database" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(page.getByText(/^Restor(ing|ed) into /)).toBeVisible();
});

test("accounts: a new user's password is shown once", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `${DATABASE}/users`);
  const table = page.getByRole("region", { name: "Accounts of example_production" });
  await expect(table.getByRole("row", { name: /analytics_reader/ }).getByText("Read only")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a database's accounts");
  await page.getByRole("button", { name: "New user" }).click();
  const dialog = page.getByRole("dialog", { name: "New user" });
  await dialog.getByLabel("Username").fill(`reports_${String(Date.now()).slice(-5)}`);
  await dialog.getByRole("button", { name: "Create user" }).click();
  const shown = page.getByRole("dialog", { name: /created$/ });
  await expect(shown.getByRole("textbox", { name: "Password" })).not.toHaveValue("");
  await shown.getByRole("button", { name: "Done" }).click();
  await expect(shown).toBeHidden();
});

test("connect: an SSH tunnel to the engine, never an open port", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `${DATABASE}/connect`);
  await page.getByLabel("Server address").fill("db.example.net");
  await expect(page.getByText("ssh -N -L 15432:127.0.0.1:5432 root@db.example.net")).toBeVisible();
  await expect(page.getByText("Only this server can reach the engine.")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a database's Connect tab");
});

test("metrics: readings, charts, the biggest tables and the slowest statements", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `${DATABASE}/metrics`);
  await expect(page.getByRole("heading", { level: 3, name: "Connections" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Biggest tables of example_production" }).getByText("public.orders")).toBeVisible();
  await expect(page.getByText("UPDATE orders SET status = $1, paid = $2 WHERE id = $3")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a database's metrics");
});

test("an application's Database tab shows what it uses and offers to create one", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/apps/example.com/database");
  await expect(page.getByRole("link", { name: "example_production" })).toBeVisible();
  await expect(page.getByText("DATABASE_URL")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "an application's Database tab");
  await page.getByRole("button", { name: "Create database" }).click();
  const dialog = page.getByRole("dialog", { name: "Create a database" });
  await expect(dialog.getByText("What Noust will write")).toBeVisible();
  await expect(dialog.getByText(/postgresql:\/\/.*\*{8}/)).toBeVisible();
  await expectNoA11yViolations(page, "the create-and-link dialog");
});
