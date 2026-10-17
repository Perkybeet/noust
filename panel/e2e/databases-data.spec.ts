/**
 * Databases in 3.4: the engine and version a new database is created on, a database reachable
 * from outside (Public access), one backup policy for every database with what it leaves out,
 * and a dump imported from the browser, in chunks that survive a dropped connection (checklist
 * C-02, C-22, H-02, H-18). Against the real backend and its sandboxed machine: the picker reads
 * what the server says it can install, opening a database is the real plan and the real socket
 * unit (the sandbox answers what the kernel would), and an upload goes through the real
 * resumable endpoints.
 */

import type { Page } from "@playwright/test";

import { confirmItsYou, expect, expectNoA11yViolations, settle, test } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

const DATABASE = "/databases/postgresql/example_production";

/** Signs in through the API with the master token and its second factor, then opens `path`. */
async function signIn(page: Page, server: ConsoleServer, path: string): Promise<void> {
  const answer = await page.request.post("/api/auth/login", { data: { token: server.token, totp_code: server.secondFactor() } });
  expect(answer.ok()).toBe(true);
  await page.goto(path);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}

test("a new database for an application: the engine, its version, and an engine that is not installed asks to be installed first", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/apps/docs.example.org/database");
  await page.getByRole("button", { name: "Create database" }).click();
  const dialog = page.getByRole("dialog", { name: "Create a database" });
  await expect(dialog.getByRole("combobox", { name: "Engine" })).toBeVisible();

  // What the server cannot give is said with its own reason, never hidden.
  await expect(dialog.getByText("Not available on this server")).toBeVisible();
  await expect(dialog.getByText(/MariaDB: MySQL is installed, and MariaDB cannot run beside it/)).toBeVisible();

  // An engine that is installed offers the version that runs, and the ones it cannot use are off.
  await dialog.getByRole("combobox", { name: "Engine" }).click();
  await expect(page.getByRole("option", { name: /^PostgreSQL/ })).toContainText("Installed, version 16.4");
  await page.getByRole("option", { name: /^PostgreSQL/ }).click();
  await dialog.getByRole("combobox", { name: "Version" }).click();
  await expect(page.getByRole("option", { name: "The version that runs (16.4)" })).toBeVisible();
  await expect(page.getByRole("option", { name: /^16 / })).toContainText("Runs here");
  await expect(page.getByRole("option", { name: /^14 / })).toHaveAttribute("aria-disabled", "true");
  await page.keyboard.press("Escape");
  await expect(dialog.getByText("What Noust will write")).toBeVisible();
  await expect(dialog.getByText(/postgresql:\/\/.*\*{8}/)).toBeVisible();

  // MongoDB is not installed here: Noust installs it first, and says so before it does.
  await dialog.getByRole("combobox", { name: "Engine" }).click();
  await expect(page.getByRole("option", { name: /^MongoDB/ })).toContainText("Not installed: Noust installs it first");
  await page.getByRole("option", { name: /^MongoDB/ }).click();
  const install = dialog.getByRole("checkbox", { name: "Install MongoDB first" });
  await expect(install).not.toBeChecked();
  await expect(dialog.getByText("MongoDB is installed first.")).toBeVisible();
  const posts: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && /\/api\/apps\/[^/]+\/databases$/.test(new URL(request.url()).pathname)) posts.push(request.url());
  });
  await dialog.getByRole("button", { name: "Create and link" }).click();
  await expect(dialog.getByText("Tick it to install MongoDB, or choose an engine that is installed.")).toBeVisible();
  expect(posts).toHaveLength(0);
  await settle(page);
  await expectNoA11yViolations(page, "the create dialog with an engine to install");
  await install.check();
  await expect(dialog.getByText("Tick it to install MongoDB, or choose an engine that is installed.")).toBeHidden();
});

test("a new database is created on the version the operator chose, and the request says so", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/apps/docs.example.org/database");
  await page.getByRole("button", { name: "Create database" }).click();
  const dialog = page.getByRole("dialog", { name: "Create a database" });
  await dialog.getByRole("combobox", { name: "Engine" }).click();
  await page.getByRole("option", { name: /^PostgreSQL/ }).click();
  await dialog.getByRole("combobox", { name: "Version" }).click();
  await page.getByRole("option", { name: /^16 / }).click();
  const sent = page.waitForRequest((request) => request.method() === "POST" && new URL(request.url()).pathname === "/api/apps/docs.example.org/databases");
  await dialog.getByRole("button", { name: "Create and link" }).click();
  const body = (await sent).postDataJSON() as { engine: string; version: string | null; install: boolean };
  expect(body).toMatchObject({ engine: "postgresql", version: "16", install: false });
  await expect(dialog).toBeHidden();
});

test("public access: opened from one address with TLS, shown once, and made private again", async ({ page, consoleServer, problems }) => {
  problems.expect(/status of 403 .*\/api\/databases\/public(\/postgresql\/example_staging(\/reveal)?)?$/);
  await signIn(page, consoleServer, "/databases/postgresql/example_staging/connect");
  const section = page.getByRole("region", { name: "Public access" });
  await expect(section.getByText("Private", { exact: true })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a database's Connect tab, private");

  await section.getByRole("button", { name: "Make it public…" }).click();
  const dialog = page.getByRole("dialog", { name: "Make example_staging public" });
  // The recommended answers are the ones already chosen.
  await expect(dialog.getByRole("radio", { name: /^Required \(recommended\)/ })).toBeChecked();
  await expect(dialog.getByRole("radio", { name: /^Owner/ })).toBeChecked();
  await dialog.getByLabel("Reachable from").fill("203.0.113.7, not-an-address");
  await dialog.getByRole("button", { name: "Review" }).click();
  await expect(dialog.getByText("Not an address or a network: not-an-address.")).toBeVisible();
  await dialog.getByLabel("Reachable from").fill("203.0.113.7");
  await dialog.getByRole("radio", { name: /^Read only/ }).check();
  await dialog.getByRole("button", { name: "Review" }).click();

  // Everything is said before anything is made, in the server's own words.
  await expect(dialog.getByText("What Noust will do")).toBeVisible();
  await expect(dialog.getByText("What you accept by going ahead")).toBeVisible();
  await expect(dialog.getByText(/becomes reachable on port \d+/)).toBeVisible();
  await expect(dialog.getByText("Required: a connection without TLS is refused")).toBeVisible();
  const submit = dialog.getByRole("button", { name: "Make it public" });
  await submit.click();
  await expect(dialog.getByText("Tick the box to accept what is listed above.")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the public access review");
  await dialog.getByRole("checkbox", { name: "I understand this and want the database public" }).check();
  await submit.click();
  await confirmItsYou(page, consoleServer);

  // The password is shown this once.
  const shown = page.getByRole("dialog", { name: "Connection to example_staging" });
  await expect(shown.getByRole("textbox", { name: "Password" })).not.toHaveValue("");
  await expect(shown.getByRole("textbox", { name: "Connection string" })).toHaveValue(/sslmode=require/);
  await shown.getByRole("button", { name: "Done" }).click();
  await expect(shown).toBeHidden();

  await expect(section.getByText(/^Public on port \d+$/)).toBeVisible();
  await expect(section.getByText("Required: a connection without TLS is refused")).toBeVisible();
  await expect(section.getByText("203.0.113.7/32", { exact: true })).toBeVisible();
  await expect(section.getByText("Read only", { exact: true })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a database's Connect tab, public");

  // Showing it again asks to confirm it's you, and shows the same connection.
  await section.getByRole("button", { name: "Show the connection" }).click();
  const again = page.getByRole("dialog", { name: "Connection to example_staging" });
  await expect(again.getByRole("textbox", { name: "Username" })).toHaveValue(/_remote$/);
  await again.getByRole("button", { name: "Done" }).click();

  await section.getByRole("button", { name: "Make it private" }).click();
  const confirm = page.getByRole("alertdialog", { name: "Make example_staging private" });
  await confirm.getByRole("button", { name: "Make it private" }).click();
  await expect(section.getByText("Private", { exact: true })).toBeVisible();
});

test("a database that is not public stays private when the review is cancelled", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `${DATABASE}/connect`);
  const section = page.getByRole("region", { name: "Public access" });
  await section.getByRole("button", { name: "Make it public…" }).click();
  const dialog = page.getByRole("dialog", { name: "Make example_production public" });
  await dialog.getByRole("radio", { name: /^Optional/ }).check();
  await dialog.getByRole("button", { name: "Review" }).click();
  // Nobody was named, so it says anyone, in words, and not the one the server writes for it.
  await expect(dialog.getByText("Anyone", { exact: true })).toBeVisible();
  // Plain text over the network is a thing the operator accepts in so many words.
  await expect(dialog.getByText("Optional: the password and the data may cross the network in clear")).toBeVisible();
  await dialog.getByRole("button", { name: "Back" }).click();
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  await expect(section.getByText("Private", { exact: true })).toBeVisible();
});

test("one backup policy for every database of an engine, with what it leaves out, and removed again", async ({ page, consoleServer, problems }) => {
  // Saving and removing a policy are behind sudo mode: the first answer is the 403 that asks for it.
  problems.expect(/status of 403 .*\/api\/databases\/backup-policies\/mysql\/\*$/);
  await signIn(page, consoleServer, "/databases/mysql/example_shop/backups");
  const card = page.locator("section").filter({ has: page.getByRole("heading", { level: 2, name: "Every MySQL/MariaDB database" }) });
  await expect(card.getByText("No policy covers every MySQL/MariaDB database.")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the backups tab, no policy for every database");

  await card.getByRole("button", { name: "Back up every database" }).click();
  const drawer = page.getByRole("dialog", { name: "Backups of every MySQL/MariaDB database" });
  await expect(drawer.getByText("Leave out")).toBeVisible();
  await drawer.getByRole("textbox", { name: /^Databases/ }).fill("shop_wp, bad$$name!");
  await drawer.getByRole("button", { name: "Save" }).click();
  await expect(drawer.getByText("Not a valid name: bad$$name!.")).toBeVisible();
  await drawer.getByRole("textbox", { name: /^Databases/ }).fill("shop_wp");
  await drawer.getByRole("textbox", { name: /^Tables/ }).fill("sessions\nlogs_*");
  await settle(page);
  await expectNoA11yViolations(page, "the policy for every database");
  await drawer.getByRole("button", { name: "Save" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(drawer).toBeHidden();

  // What it covers and what it leaves out are on the card, in plain words.
  await expect(card.getByText("Every MySQL/MariaDB database, example_shop included")).toBeVisible();
  await expect(card.getByText("databases shop_wp")).toBeVisible();
  await expect(card.getByText("tables sessions, logs_*")).toBeVisible();

  // The list agrees: the database it covers is backed up, the one it leaves out is not.
  await page.goto("/databases");
  const table = page.getByRole("region", { name: "Databases" });
  await expect(table.getByRole("row", { name: /example_shop/ }).getByText("Backed up")).toBeVisible();
  await expect(table.getByRole("row", { name: /shop_wp/ }).getByText("No backups scheduled")).toBeVisible();

  // Leaving no trace for what runs after this in the same server.
  await page.goto("/databases/mysql/example_shop/backups");
  await card.getByRole("button", { name: "More schedule actions" }).click();
  await page.getByRole("menuitem", { name: "Remove" }).click();
  await page.getByRole("alertdialog", { name: "Remove the policy for every MySQL/MariaDB database" }).getByRole("button", { name: "Remove policy" }).click();
  await expect(card.getByText("No policy covers every MySQL/MariaDB database.")).toBeVisible();
});

/** A plain SQL dump of `bytes` bytes or a little more: the header an engine's own tool writes, then comments. */
function sqlDump(bytes: number): Buffer {
  const header = "-- PostgreSQL database dump\n\nSET statement_timeout = 0;\nCREATE TABLE greetings (id integer, said text);\nINSERT INTO greetings VALUES (1, 'hola');\n";
  const filler = "-- padding that makes the dump long enough to need several chunks\n";
  // pg_dump closes a complete dump with this line, and the server refuses one that does not have it.
  const footer = "\n--\n-- PostgreSQL database dump complete\n--\n";
  return Buffer.concat([Buffer.from(header), Buffer.from(filler.repeat(Math.ceil(Math.max(0, bytes - header.length) / filler.length))), Buffer.from(footer)]);
}

test("import a dump into a new database: uploaded, recognised, loaded as a job whose log is followed", async ({ page, consoleServer, problems }) => {
  problems.expect(/status of 403 .*\/api\/databases\/imports$/);
  await signIn(page, consoleServer, "/databases");
  await page.getByRole("button", { name: "Import a dump…" }).click();
  const dialog = page.getByRole("dialog", { name: "Import a dump" });
  await dialog.getByRole("combobox", { name: "Engine" }).click();
  await page.getByRole("option", { name: "PostgreSQL", exact: true }).click();
  const database = `imported_${String(Date.now()).slice(-6)}`;
  await dialog.getByLabel("Database", { exact: true }).fill(database);

  // The size cap is the server's, and said before a file is chosen.
  await expect(dialog.getByText(/up to 10 GB\.?$/)).toBeVisible();
  await dialog.getByLabel("Dump file").setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("not a dump") });
  await expect(dialog.getByText(/notes\.txt does not look like a database dump/)).toBeVisible();

  await dialog.getByLabel("Dump file").setInputFiles({ name: "shop.sql", mimeType: "application/sql", buffer: sqlDump(2_000) });
  await expect(dialog.getByText("Uploaded and checked")).toBeVisible();
  await expect(dialog.getByText("A SQL script, not compressed")).toBeVisible();
  await expect(dialog.getByText("PostgreSQL can load it.")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the import dialog, a dump uploaded");
  await dialog.getByRole("button", { name: "Continue" }).click();

  await expect(dialog.getByText(`${database} does not exist on PostgreSQL`)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the import options");
  await dialog.getByRole("button", { name: "Import" }).click();
  await confirmItsYou(page, consoleServer);

  // The job's output is followed in the dialog, and its result said.
  await expect(dialog.getByRole("region", { name: `Output of the import into ${database}` })).toBeVisible();
  await expect(dialog.getByText(`Imported into ${database}`)).toBeVisible();
  await expect(dialog.getByText(`Created ${database}`)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the import, finished");
  await dialog.getByRole("link", { name: `Open ${database}` }).click();
  await expect(page).toHaveURL(new RegExp(`/databases/postgresql/${database}$`));
});

test("import over a database that exists asks to type its name before it replaces anything", async ({ page, consoleServer, problems }) => {
  problems.expect(/status of 403 .*\/api\/databases\/imports$/);
  await signIn(page, consoleServer, `${DATABASE}/backups`);
  await page.getByRole("button", { name: "Import a dump…" }).click();
  const dialog = page.getByRole("dialog", { name: "Import a dump into example_production" });
  await dialog.getByLabel("Dump file").setInputFiles({ name: "restore.sql", mimeType: "application/sql", buffer: sqlDump(1_000) });
  await expect(dialog.getByText("Uploaded and checked")).toBeVisible();
  await dialog.getByRole("button", { name: "Continue" }).click();

  await expect(dialog.getByText("example_production already exists on PostgreSQL.")).toBeVisible();
  await expect(dialog.getByRole("checkbox", { name: /^Take a copy of example_production first/ })).toBeChecked();
  await dialog.getByRole("checkbox", { name: /^Keep the owners and privileges the dump names/ }).check();
  await dialog.getByRole("checkbox", { name: /^Drop and recreate it first/ }).check();
  // A copy is always taken when it is dropped, and the name is typed to say the data is meant to go.
  await expect(dialog.getByText("A copy of example_production is taken first, because it is dropped.")).toBeVisible();
  await expect(dialog.getByText("Replaces the existing data")).toBeVisible();
  const importNow = dialog.getByRole("button", { name: "Import" });
  await importNow.click();
  await expect(dialog.getByText("Type the database's name to confirm that its data is replaced.")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the import options, replacing");
  await dialog.getByRole("textbox", { name: /^Confirm/ }).fill("example_production");
  const sent = page.waitForRequest((request) => request.method() === "POST" && new URL(request.url()).pathname === "/api/databases/imports");
  await importNow.click();
  const body = (await sent).postDataJSON() as { engine: string; database: string; replace: boolean; keep_owners: boolean; safety_backup: boolean };
  expect(body).toMatchObject({ engine: "postgresql", database: "example_production", replace: true, keep_owners: true });
  // Not carried to the end: the sandbox's production database keeps what it has, and the elevation prompt is closed.
  await page.getByRole("dialog", { name: "Confirm it's you" }).getByRole("button", { name: "Cancel" }).click();
  await dialog.getByRole("button", { name: "Back" }).click();
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
});

/** Whether a request is one of the upload's chunks (a PUT), and where in the file it lands. */
function chunkOffset(request: { method: () => string; url: () => string }): number | null {
  if (request.method() !== "PUT") return null;
  const url = new URL(request.url());
  return url.pathname.startsWith("/api/databases/imports/uploads/") ? Number(url.searchParams.get("offset")) : null;
}

test("an upload that loses its connection goes on from what the server holds", async ({ page, consoleServer, problems }) => {
  problems.expect(/Failed to load resource: net::ERR_CONNECTION_RESET .*\/api\/databases\/imports\/uploads\//);
  const offsets: number[] = [];
  let down = true;
  // The network comes back at the click on "Try now" and not before. The upload retries by itself on a
  // backoff, so a network restored a moment before the click lets that retry finish the upload first,
  // and the button the test is about to press is gone (it showed under load). The click is seen in the
  // page, in the capture phase, before the console's own handler asks for anything.
  await page.exposeFunction("noustNetworkBack", () => {
    down = false;
  });
  await page.addInitScript(() => {
    document.addEventListener(
      "click",
      (event) => {
        const button = event.target instanceof Element ? event.target.closest("button") : null;
        if (button?.textContent.trim() === "Try now") void (window as unknown as { noustNetworkBack: () => Promise<void> }).noustNetworkBack();
      },
      true,
    );
  });
  // The first chunk gets through; then the network is away until that click.
  await page.route(
    (url) => url.pathname.startsWith("/api/databases/imports/uploads/"),
    async (route) => {
      const offset = chunkOffset(route.request());
      if (offset === null) return route.continue();
      offsets.push(offset);
      return offset > 0 && down ? route.abort("connectionreset") : route.continue();
    },
  );
  await signIn(page, consoleServer, `${DATABASE}/backups`);
  await page.getByRole("button", { name: "Import a dump…" }).click();
  const dialog = page.getByRole("dialog", { name: "Import a dump into example_production" });
  await dialog.getByLabel("Dump file").setInputFiles({ name: "big.sql", mimeType: "application/sql", buffer: sqlDump(17 * 1024 * 1024) });

  // Told what happens and when, and what was sent is not sent again.
  await expect(dialog.getByText(/The connection dropped\. Trying again/)).toBeVisible();
  await expect(dialog.getByText(/^8\.0 MB of 17 MB$/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "an upload waiting for the connection");
  await dialog.getByRole("button", { name: "Try now" }).click();
  await expect(dialog.getByText("Uploaded and checked")).toBeVisible();
  expect(offsets.filter((offset) => offset === 0)).toHaveLength(1);
  expect(Math.max(...offsets)).toBeGreaterThanOrEqual(16 * 1024 * 1024);
  await expect(dialog.getByText("A SQL script, not compressed")).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
});

test("an upload can be cancelled while it goes, and the server throws away what it had", async ({ page, consoleServer }) => {
  let release: () => void = () => undefined;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  // The second chunk stays on its way, so the upload is under way for as long as the test needs.
  await page.route(
    (url) => url.pathname.startsWith("/api/databases/imports/uploads/"),
    async (route) => {
      const offset = chunkOffset(route.request());
      if (offset === null || offset === 0) return route.continue();
      await held;
      return route.continue().catch(() => undefined);
    },
  );
  await signIn(page, consoleServer, `${DATABASE}/backups`);
  await page.getByRole("button", { name: "Import a dump…" }).click();
  const dialog = page.getByRole("dialog", { name: "Import a dump into example_production" });
  const created = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/databases/imports/uploads");
  await dialog.getByLabel("Dump file").setInputFiles({ name: "big.sql", mimeType: "application/sql", buffer: sqlDump(17 * 1024 * 1024) });
  const upload = (await (await created).json()) as { id: string };
  await expect(dialog.getByRole("progressbar")).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel upload" }).click();
  await expect(dialog.getByText("Upload cancelled. The server threw away what it had.")).toBeVisible();
  release();
  const gone = await page.request.get(`/api/databases/imports/uploads/${upload.id}`);
  expect(gone.ok()).toBe(false);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
});
