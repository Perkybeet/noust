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


test("the list says whether each database answers, where it runs and whether it is backed up, and the engines are their own page", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/databases");
  await expect(page.getByRole("heading", { level: 1, name: "Databases" })).toBeVisible();
  const table = page.getByRole("region", { name: "Databases" });
  const production = table.getByRole("row", { name: /example_production/ });
  await expect(production.getByText("Verified")).toBeVisible();
  await expect(production.getByText("Running")).toBeVisible();
  // The widest screens say where it runs too; a laptop's width says the engine and its release, and the whole is where the pointer rests.
  await expect(production.getByText("PostgreSQL 16", { exact: true })).toBeVisible();
  await expect(production.getByRole("link", { name: "example.com" })).toBeVisible();
  const staging = table.getByRole("row", { name: /example_staging/ });
  // A database Noust does not hold is not a kind of database: where its backup would be, its row offers to add it.
  await expect(staging.getByRole("button", { name: "Add to Noust" })).toBeVisible();
  await expect(staging.getByText("No backup")).toHaveCount(0);
  // One Noust holds with nothing scheduled says so in words.
  await expect(table.getByRole("row", { name: /shop_wp/ }).getByText("No backup")).toBeVisible();
  await expect(page.getByRole("radio", { name: /^No schedule \(\d+\)$/ })).toBeVisible();
  // The engine that is stopped keeps its databases in the list, and says it is stopped.
  await expect(table.getByRole("row", { name: /sessions/ }).getByText("Stopped")).toBeVisible();
  // The engines are not a second half of this page.
  await expect(page.getByRole("list", { name: "Engines" })).toHaveCount(0);
  await settle(page);
  await expectNoA11yViolations(page, "the databases list");

  // The column on the side narrows the list by engine.
  const column = page.getByRole("navigation", { name: "Databases" });
  await column.getByRole("link", { name: /MySQL/ }).click();
  await expect(page).toHaveURL(/\/databases\?engine=mysql$/);
  await expect(table.getByRole("row", { name: /example_production/ })).toHaveCount(0);
  await expect(table.getByRole("row", { name: /example_shop/ })).toBeVisible();
});

test("the engines have a page of their own, and an engine's settings are saved and applied", async ({ page, consoleServer, problems }) => {
  // Saving settings is behind sudo mode: the first answer is the 403 that asks for it.
  problems.expect(/status of 403 .*\/api\/databases\/engines\/postgresql\/settings$/);
  await signIn(page, consoleServer, "/databases");
  await page.getByRole("navigation", { name: "Databases" }).getByRole("link", { name: /^Engines/ }).click();
  await expect(page).toHaveURL(/\/databases\/engines$/);
  await expect(page.getByRole("heading", { level: 1, name: "Engines" })).toBeVisible();
  const engines = page.getByRole("list", { name: "Engines" });
  const stack = engines.getByRole("listitem").filter({ hasText: "In the stack of" });
  await expect(stack.getByRole("link", { name: "catalogo.example.org" })).toBeVisible();
  // The one that is stopped is grey, and what it asks is on its card.
  await expect(engines.getByRole("listitem").filter({ hasText: "Redis" }).getByRole("button", { name: "Start" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the engines");

  await engines.getByRole("button", { name: /^Actions for PostgreSQL 16/ }).first().click();
  await page.getByRole("menuitem", { name: "Settings" }).click();
  await expect(page).toHaveURL(/\/databases\/engines\/postgresql\/settings$/);
  await expect(page.getByRole("heading", { level: 1, name: "PostgreSQL settings" })).toBeVisible();
  const slow = page.getByRole("textbox", { name: "log_min_duration_statement", exact: true });
  await expect(slow).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "an engine's settings");
  // A value no earlier run left there, so there is always something to save.
  await slow.fill(String(1000 + (Date.now() % 9000)));
  await page.getByRole("button", { name: "Save" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(page.getByText(/Saved, and PostgreSQL /)).toBeVisible();
  await expect(page.getByText("No unsaved changes")).toBeVisible();
});

test("the old addresses of the engines and of a database's users open their new places", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/databases/servers");
  await expect(page).toHaveURL(/\/databases\/engines$/);
  await expect(page.getByRole("heading", { level: 1, name: "Engines" })).toBeVisible();
  await page.goto(`${DATABASE}/users`);
  await expect(page).toHaveURL(/\/example_production\/access$/);
  await expect(page.getByRole("heading", { level: 1, name: "Access" })).toBeVisible();
});

test("one dialog makes a database, empty or from a dump, on the engine chosen as a card, and opens its page", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/databases");
  await page.getByRole("button", { name: "New database" }).click();
  const dialog = page.getByRole("dialog", { name: "New database" });
  // The default engine is chosen already; the other that can hold a database is one card away.
  await expect(dialog.getByRole("radio", { name: /PostgreSQL 16/ })).toBeChecked();
  await expect(dialog.getByRole("radio", { name: /Another engine or version/ })).toBeVisible();
  await dialog.getByRole("radio", { name: "From a dump" }).click();
  await expect(dialog.getByLabel("Dump file")).toBeVisible();
  await expectNoA11yViolations(page, "the new database dialog, from a dump");
  await dialog.getByRole("radio", { name: "Empty" }).click();
  const name = `acme_${String(Date.now()).slice(-6)}`;
  await dialog.getByLabel("Name").fill(name);
  await expectNoA11yViolations(page, "the new database dialog");
  await dialog.getByRole("button", { name: "Create the database" }).click();
  await expect(page).toHaveURL(new RegExp(`/databases/postgresql/${name}$`));
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
  // The whole flow is in the column: from the new database to how to connect to it is one click.
  await page.getByRole("navigation", { name: "Database sections" }).getByRole("link", { name: "Connect" }).click();
  await expect(page).toHaveURL(new RegExp(`/databases/postgresql/${name}/connect$`));
  await expect(page.getByRole("heading", { level: 2, name: "From your computer" })).toBeVisible();
});

test("upgrading an engine says first that it makes a new instance and moves the databases, from the engine's page and from each old database", async ({ page, consoleServer }) => {
  // The MySQL of this machine is past its support: its databases say so, and the way to a newer one.
  await signIn(page, consoleServer, "/databases/mysql/shop_wp");
  const engine = page.getByRole("region", { name: "Engine" });
  await expect(engine.getByText("MySQL 8.0 (on the system)")).toBeVisible();
  await expect(engine.getByText(/No support since Apr 2026/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a database whose engine is past its support");
  await engine.getByRole("button", { name: "Upgrade version" }).click();
  const dialog = page.getByRole("dialog", { name: "Upgrade MySQL 8.0" });
  await expect(dialog.getByText("It is not upgraded in place: a new instance with the newer version is made, and the databases are moved to it.")).toBeVisible();
  // This machine has no Docker, and the dialog says so as every page that lacks a tool does, before anything is asked of it.
  await expect(dialog.getByText("Docker is missing")).toBeVisible();
  // Said once: the plan is not asked of a machine that cannot make the instance, so its refusal is not a second explanation.
  await expect(dialog.getByText("Could not work out the move")).toHaveCount(0);
  await expectNoA11yViolations(page, "the upgrade dialog");
});

test("a stopped engine is said and started from the database's page, and what it needs is not asked of it", async ({ page, consoleServer, problems }) => {
  // The seeded Redis database is named, and Redis numbers its databases: once the engine runs, the engine's own refusal is what is read.
  problems.expect(/status of (500|400) .*\/api\/databases\/databases\/redis\/sessions\//);
  await signIn(page, consoleServer, "/databases");
  const row = page.getByRole("region", { name: "Databases" }).getByRole("row", { name: /sessions/ });
  await expect(row.getByText("Stopped")).toBeVisible();
  await row.getByRole("button", { name: "sessions", exact: true }).click();
  await expect(page).toHaveURL(/\/databases\/redis\/sessions$/);
  const slab = page.getByLabel("Engine stopped");
  await expect(slab.getByText(/Redis \(on the system\) is stopped/)).toBeVisible();
  // A key-value store has Keys and no Query, stopped or not.
  const column = page.getByRole("navigation", { name: "Database sections" });
  await expect(column.getByRole("link", { name: "Keys" })).toBeVisible();
  await expect(column.getByRole("link", { name: "Query" })).toHaveCount(0);
  await settle(page);
  await expectNoA11yViolations(page, "a database whose engine is stopped");
  await column.getByRole("link", { name: "Keys" }).click();
  await expect(page.getByLabel("Engine stopped")).toBeVisible();
  await page.getByRole("button", { name: "Start the engine" }).click();
  await expect(page.getByLabel("Engine stopped")).toHaveCount(0);
});

test("the query console says what its plan runs: showing it does not, measuring it does and waits for changes to be allowed", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `${DATABASE}/query`);
  await page.getByRole("button", { name: "Explain" }).click();
  await expect(page.getByRole("menuitem", { name: /Show the plan/ })).toBeEnabled();
  const measure = page.getByRole("menuitem", { name: /Measure it by running it/ });
  await expect(measure).toBeDisabled();
  await expect(measure).toContainText("Turn on Allow changes: it runs the statement for real.");
  await page.keyboard.press("Escape");
  await page.getByRole("checkbox", { name: "Allow changes" }).check();
  await page.getByRole("button", { name: "Explain" }).click();
  await expect(page.getByRole("menuitem", { name: /Measure it by running it/ })).toBeEnabled();
  await page.keyboard.press("Escape");
  await settle(page);
  await expectNoA11yViolations(page, "the SQL console");
});

test("a database's overview: its state, figures, engine and charts, and its sections in the column", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, DATABASE);
  await expect(page.getByRole("heading", { level: 1, name: "example_production" })).toBeVisible();
  const column = page.getByRole("navigation", { name: "Database sections" });
  await expect(column.getByRole("link")).toHaveText(["Overview", "Data", "Query", "Metrics", "Access", "Connect", "Backups", "Settings", "Delete"]);
  for (const heading of ["View", "Manage", "Danger"]) await expect(column.getByText(heading, { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Engine" }).getByText("PostgreSQL 16 (on the system)")).toBeVisible();
  await expect(page.getByRole("button", { name: "Back up now" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a database's overview");
});

test("settings and delete: what the database holds and how it is set up, and what deleting it takes", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `${DATABASE}/settings`);
  await expect(page.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible();
  await expect(page.getByRole("region", { name: "This database" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a database's settings");
  await page.getByRole("navigation", { name: "Database sections" }).getByRole("link", { name: "Delete" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Delete the database" })).toBeVisible();
  await expect(page.getByText(/It is taken away from example\.com, which restart without it/)).toBeVisible();
  await expect(page.getByRole("checkbox", { name: "Take a last dump first" })).toBeChecked();
  await settle(page);
  await expectNoA11yViolations(page, "deleting a database");
});

test("extensions: the loaded ones are listed, and loading and unloading one goes through sudo mode", async ({ page, consoleServer, problems }) => {
  // Loading an extension runs its script as the superuser: the first answer is the 403 that asks for sudo mode.
  problems.expect(/status of 403 .*\/api\/databases\/databases\/postgresql\/example_production\/extensions$/);
  await signIn(page, consoleServer, `${DATABASE}/settings`);
  const card = page.locator("section").filter({ has: page.getByRole("heading", { level: 2, name: "Extensions" }) });
  await expect(card.getByText("pg_trgm", { exact: true })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a database's extensions");

  await card.getByRole("button", { name: "Add extension" }).click();
  const dialog = page.getByRole("dialog", { name: "Add an extension to example_production" });
  await dialog.getByRole("combobox", { name: "Extension" }).click();
  await page.getByRole("option", { name: /^postgis_topology/ }).click();
  // One that is not trusted says so, and the one it needs is loaded with it or not at all.
  await expect(dialog.getByText("This extension is not trusted")).toBeVisible();
  // Pressing it without the box says what is missing and loads nothing.
  await dialog.getByRole("button", { name: "Add extension" }).click();
  await expect(dialog.getByText("Tick the box to load what it needs too.")).toBeVisible();
  await dialog.getByRole("checkbox", { name: "Also load what it needs: postgis" }).check();
  await expectNoA11yViolations(page, "adding an extension");
  await dialog.getByRole("button", { name: "Add extension" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(card.getByText("postgis_topology", { exact: true })).toBeVisible();

  await card.getByRole("button", { name: "Unload postgis_topology" }).click();
  const ask = page.getByRole("alertdialog", { name: "Unload postgis_topology from example_production?" });
  await ask.getByRole("button", { name: "Unload extension" }).click();
  await expect(card.getByText("postgis_topology", { exact: true })).toHaveCount(0);
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
  await page.getByRole("menuitem", { name: /^Show the plan/ }).click();
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
  const dialog = page.getByRole("alertdialog", { name: "Restore a dump" });
  await expect(dialog.getByLabel("New database name")).not.toHaveValue("");
  await expectNoA11yViolations(page, "the restore dialog");
  await dialog.getByRole("button", { name: "Restore as new database" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(page.getByText(/^Restor(ing|ed) into /)).toBeVisible();
});

test("accounts: a new user's password is shown once", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `${DATABASE}/access`);
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

test("an application's SQLite files are listed and checked without closing them", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/apps/pedidos.example.org/database");
  await expect(page.getByRole("heading", { level: 2, name: "SQLite files" })).toBeVisible();
  const card = page.getByRole("listitem").filter({ hasText: "storage/orders.db" });
  await expect(card.getByText("Rollback journal")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "an application's SQLite files");
  await card.getByRole("button", { name: "Check", exact: true }).click();
  await expect(card.getByText(/Nothing wrong found in/)).toBeVisible();
  await card.getByRole("button", { name: "Check every index" }).click();
  await expect(card.getByText(/Nothing wrong found in/)).toBeVisible();
  await expectNoA11yViolations(page, "a finished SQLite check");
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

test("adding an engine asks how first, and a container goes on to another version with its support said beside its number", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/databases/engines");
  await page.getByRole("button", { name: "Add an engine" }).click();
  const choose = page.getByRole("dialog", { name: "Add an engine" });
  await expect(choose.getByRole("radio", { name: /On the system/ })).toBeChecked();
  await choose.getByRole("radio", { name: /In a container/ }).click();
  // No Docker on this machine: said here, as every page says a missing tool, with the way to install it.
  await expect(choose.getByText("Docker is missing")).toBeVisible();
  await expectNoA11yViolations(page, "adding an engine");
  await choose.getByRole("button", { name: "Continue" }).click();
  const dialog = page.getByRole("dialog", { name: "Another version" });
  await dialog.getByRole("combobox", { name: "Engine" }).click();
  await page.getByRole("option", { name: "PostgreSQL" }).click();
  await dialog.getByRole("combobox", { name: "Version" }).click();
  await expect(page.getByRole("option", { name: /^12: No support since/ })).toBeVisible();
  await page.keyboard.press("Escape");
  await expectNoA11yViolations(page, "the other version dialog");
});
