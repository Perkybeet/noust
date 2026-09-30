/**
 * The fleet against two real backends: a central and a node, connected without ssh (see
 * scripts/console_server.py's `--fleet-node`, `join_fleet_node` and
 * noust.fleet.tunnels.set_loopback_for_testing). Every request the browser causes still goes
 * through the real proxy, the real fleet token and the node's own real API - only the ssh
 * dial itself is replaced by a loopback port, so what these tests exercise is the same code a
 * production central runs against a real server.
 *
 * - the selector switches to the node, and the node's own, differently-seeded application
 *   shows where the central's never do;
 * - a deep link to a node's page survives a reload;
 * - the selector's three contexts: a server, All servers, This central, and the way back;
 * - a node's own settings stay on the node, and the central's say whose they are;
 * - the Fleet's views list this server and the node together, each row naming its server;
 * - a bulk action is planned, run as a job and followed server by server;
 * - the Add server dialog catches a console token pasted as a join code, and a code the
 *   central refused is not sent twice;
 * - a fleet with a node that is down and one on an older Noust answers partially, never blank;
 * - a destructive, elevated action made while on the node lands on the node, not the central;
 * - a node that stops answering says so on its own pages and in the Fleet's attention;
 * - Settings > Servers lists the node and opens the add flow's first step;
 * - a central with sealed secrets shows the lock screen and opens with the right passphrase;
 * - a hub sends its own pages to the Fleet instead.
 */

import type { Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, signIn, startConsoleServer, test, toasts } from "./fixtures";
import type { ConsoleServer, RunningServer } from "./fixtures";
import { confirmItsYou } from "./settings.helpers";

/**
 * Signs in to a server this file started itself, never the worker's default one `baseURL`
 * points at: the address bar is given the full origin up front (`signIn`'s own navigation is
 * relative, resolved against `baseURL`, which is the wrong server for these), with `next`
 * already in the query string so the post-login landing page - and any redirect it triggers,
 * such as a hub's - is exactly what a plain `signIn(page, server, next)` would have reached.
 */
async function signInAt(page: Page, server: ConsoleServer, next?: string): Promise<void> {
  const query = next === undefined ? "" : `?next=${encodeURIComponent(next)}`;
  await page.goto(`${server.url}/login${query}`);
  await signIn(page, server);
}

/** The node's name on the central: 1-32 lower-case letters, digits and dashes, like any other. */
const NODE_NAME = "fleet-node";

/** Seeded only on the node (see NOUST_E2E_FLEET_APP), so seeing it proves the page read the node. */
const NODE_APP = "node-only.example.net";

/** `NODE_APP`, escaped for use inside a `RegExp`. */
const NODE_APP_RE = NODE_APP.replace(/\./g, "\\.");

/** The node's own local port, as `--fleet-node` wants it. */
function portOf(server: ConsoleServer): string {
  return new URL(server.url).port;
}

interface FleetWorkerFixtures {
  node: RunningServer;
  consoleServer: ConsoleServer;
}

/**
 * A central and a node sharing one worker: every test below reads them, and the one
 * destructive test only ever creates and removes its own, disposable destination on the node, so
 * the pair stays good for every test scheduled after it on the same worker. The node going
 * down is its own, separate pair (below), never this shared one.
 */
const withFleet = test.extend<object, FleetWorkerFixtures>({
  node: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const node = await startConsoleServer([], { env: { NOUST_E2E_FLEET_APP: NODE_APP } });
      try {
        await use(node);
      } finally {
        await node.stop();
      }
    },
    { scope: "worker", timeout: 75_000 },
  ],
  consoleServer: [
    async ({ node }, use) => {
      const central = await startConsoleServer([
        "--totp",
        "--backup-codes",
        "500",
        "--fleet-node",
        `${NODE_NAME}=127.0.0.1:${portOf(node)}`,
      ]);
      try {
        await use(central);
      } finally {
        await central.stop();
      }
    },
    { scope: "worker", timeout: 90_000 },
  ],
});

withFleet("the selector switches to the node, and the apps list shows its own application", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/apps");
  await expect(page.getByRole("link", { name: NODE_APP, exact: true })).toHaveCount(0);

  await page.getByRole("button", { name: /^Server:/ }).click();
  await page.getByRole("menuitemradio", { name: new RegExp(`^${NODE_NAME}`) }).click();
  await expect(page).toHaveURL(new RegExp(`/n/${NODE_NAME}/apps$`));
  await expect(page.getByRole("link", { name: NODE_APP, exact: true })).toBeVisible();

  await settle(page);
  await expectNoA11yViolations(page, "the apps list, switched to the node");
});

withFleet("a deep link to a node's application works on reload", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/n/${NODE_NAME}/apps/${NODE_APP}`);
  await expect(page).toHaveURL(new RegExp(`/n/${NODE_NAME}/apps/${NODE_APP_RE}$`));
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(NODE_APP);

  await page.reload();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(NODE_APP);
  await settle(page);
  await expectNoA11yViolations(page, "a node's application, reloaded from a deep link");
});

withFleet("the Fleet's views show this server and the node, each row naming its server", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/fleet");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Fleet");
  await expect(page.getByRole("button", { name: "Server: all servers" })).toBeVisible();

  const table = page.getByRole("region", { name: "The fleet's servers at a glance" });
  await expect(table.getByText("This central")).toBeVisible();
  await expect(table.getByRole("link", { name: `Open ${NODE_NAME}` })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the Fleet's summary");

  const tabs = page.getByRole("navigation", { name: "Fleet views" });
  await tabs.getByRole("link", { name: "Applications" }).click();
  const apps = page.getByRole("region", { name: "Applications on every server" });
  await expect(apps.getByRole("link", { name: NODE_APP, exact: true })).toHaveAttribute("href", `/n/${NODE_NAME}/apps/${NODE_APP}`);
  await settle(page);
  await expectNoA11yViolations(page, "the Fleet's applications");

  for (const [tab, caption] of [
    ["Servers", "Servers of this fleet"],
    ["Certificates", "Certificates on every server"],
    ["Backups", "Backups of every application"],
    ["Updates", "Updates of every server"],
    ["Activity", "What happened lately on every server"],
  ] as const) {
    await tabs.getByRole("link", { name: tab }).click();
    await expect(page.getByRole("region", { name: caption })).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, `the Fleet's ${tab.toLowerCase()}`);
  }
});

withFleet("the selector says which of the three contexts the console is in, and leads back to the server", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/n/${NODE_NAME}/apps`);
  await page.getByRole("button", { name: `Server: ${NODE_NAME}` }).click();
  await page.getByRole("menuitemradio", { name: /^All servers/ }).click();
  await expect(page).toHaveURL(/\/fleet$/);
  await expect(page.getByRole("button", { name: "Server: all servers" })).toBeVisible();

  // Never a silent jump: the way back to the node is there, in words.
  const back = page.getByRole("link", { name: `Back to ${NODE_NAME}, the server you were on` });
  await expect(back).toBeVisible();
  // The sidebar's server destinations lead to the node, under its name.
  await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Applications" })).toHaveAttribute("href", `/n/${NODE_NAME}/apps`);
  await back.click();
  await expect(page).toHaveURL(new RegExp(`/n/${NODE_NAME}$`));
  await expect(page.getByRole("button", { name: `Server: ${NODE_NAME}` })).toBeVisible();
});

withFleet("a node's own settings stay on the node; the central's say whose they are and how to manage the node's", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/n/${NODE_NAME}/settings/notifications`);
  await expect(page).toHaveURL(new RegExp(`/n/${NODE_NAME}/settings/notifications$`));
  await expect(page.getByRole("button", { name: `Server: ${NODE_NAME}` })).toBeVisible();
  const sections = page.getByRole("navigation", { name: "Settings sections" }).first();
  await expect(sections.getByRole("link", { name: "General" })).toHaveAttribute("href", `/n/${NODE_NAME}/settings`);
  await settle(page);
  await expectNoA11yViolations(page, "a node's own settings");

  await sections.getByRole("link", { name: "API tokens" }).click();
  await expect(page).toHaveURL(/\/settings\/tokens$/);
  await expect(page.getByRole("button", { name: /^Server: this central/ })).toBeVisible();
  await expect(page.getByText(`Sign-in, two-factor and API tokens of ${NODE_NAME} are managed on ${NODE_NAME}`)).toBeVisible();
  await expect(sections.getByRole("link", { name: "General" })).toHaveAttribute("href", `/n/${NODE_NAME}/settings`);
  await settle(page);
  await expectNoA11yViolations(page, "the central's settings, from a node");
});

withFleet("a bulk action is planned, run as a job of the central and followed server by server", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/fleet/servers");
  const servers = page.getByRole("region", { name: "Servers of this fleet" });
  await servers.getByRole("checkbox", { name: `Select ${NODE_NAME}` }).click();
  await page.getByRole("button", { name: "Run an action on 1 server" }).click();

  const dialog = page.getByRole("dialog", { name: "Run an action on several servers" });
  await dialog.getByRole("radio", { name: "Renew certificates" }).click();
  await dialog.getByRole("button", { name: "Continue" }).click();
  await expect(dialog.getByRole("checkbox", { name: NODE_NAME })).toBeChecked();
  await dialog.getByRole("button", { name: "Show the plan" }).click();
  await expect(dialog.getByText("“Renew certificates” will run on 1 server.")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a bulk action's plan");
  await dialog.getByRole("button", { name: "Run on 1 server" }).click();
  // Sudo mode, when the node's schema marks the action as needing it: asked once for the job.
  const confirm = page.getByRole("dialog", { name: "Confirm it's you" });
  if (await confirm.isVisible().catch(() => false)) await confirmItsYou(page, consoleServer);

  await expect(page).toHaveURL(/\/fleet\/jobs\/[\w-]+$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Renew certificates");
  const job = page.getByRole("region", { name: "Every server of this action" });
  await expect(job.getByRole("link", { name: `Activity of ${NODE_NAME}` })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a bulk action's job");
});

withFleet("Add a server: a console token pasted as a join code is caught, and a refused code is never sent twice", async ({ page, consoleServer, problems }) => {
  problems.expect(/status of 400 .*\/api\/nodes$/);
  problems.expect(/status of 403 .*\/api\/nodes$/);
  const posts: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/nodes") posts.push(request.url());
  });
  await signIn(page, consoleServer, "/settings/servers");
  await page.getByRole("button", { name: "Add a server" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a server" });
  await expect(dialog.getByText("Run it on the other server: the one you are adding")).toBeVisible();
  await dialog.getByLabel("Name", { exact: true }).fill("temp-join-check");
  await dialog.getByRole("button", { name: "Show the command" }).click();
  await expect(dialog.getByTestId("authorize-command")).toContainText("noust fleet authorize");
  await dialog.getByRole("button", { name: "I ran it: continue" }).click();

  // The console's own access token, pasted by mistake: said, and nothing is sent.
  await dialog.getByLabel("Join code").fill(consoleServer.token);
  await expect(dialog.getByText(/That looks like a console access token/)).toBeVisible();
  await dialog.getByLabel("SSH address").fill("127.0.0.1");
  await dialog.getByRole("button", { name: "Add server" }).click();
  await expect(dialog.getByLabel("Join code")).toBeFocused();
  expect(posts).toHaveLength(0);
  await settle(page);
  await expectNoA11yViolations(page, "the add server dialog, a console token pasted");

  // A well-formed code made for another central's key: the central refuses it.
  await dialog.getByLabel("Join code").fill(foreignJoinCode());
  await expect(dialog.getByText("Join code read")).toBeVisible();
  await dialog.getByRole("button", { name: "Add server" }).click();
  const confirm = page.getByRole("dialog", { name: "Confirm it's you" });
  await confirm.waitFor({ state: "visible", timeout: 5_000 }).catch(() => undefined);
  if (await confirm.isVisible()) await confirmItsYou(page, consoleServer);
  await expect(dialog.getByText("Could not add temp-join-check")).toBeVisible();
  const sent = posts.length;
  await dialog.getByRole("button", { name: "Back to the join code" }).click();
  await expect(dialog.getByLabel("Join code")).toBeVisible();
  // The 3.0 bug: this click landed on the join step's submit button and sent the code again.
  await page.waitForTimeout(500);
  expect(posts).toHaveLength(sent);
});

withFleet(
  "a destructive action on the node asks to confirm it's you, then succeeds there and only there",
  async ({ page, consoleServer, problems }) => {
    // A backup destination: within the node's default ceiling (admin, without host access), where
    // a raw unit is not - that one is root-equivalent, and the node refuses it to any central.
    const name = `e2e-fleet-${String(Date.now())}`;
    problems.expect(new RegExp(`status of 403 .*\\/api\\/nodes\\/${NODE_NAME}\\/api\\/backup-destinations$`));
    const rows = () => page.getByRole("region", { name: "Backup destinations" }).getByRole("row").filter({ has: page.getByRole("cell", { name, exact: true }) });

    await signIn(page, consoleServer, `/n/${NODE_NAME}/backups/destinations`);
    await page.getByRole("button", { name: "Add destination" }).click();
    const create = page.getByRole("dialog", { name: "Add backup destination" });
    await create.getByLabel("Name", { exact: true }).fill(name);
    await create.getByRole("combobox", { name: "Backend" }).click();
    await page.getByRole("option", { name: /^SFTP server/ }).click();
    await create.getByLabel("Host", { exact: true }).fill("backup.fleet.invalid");
    await create.getByLabel("User", { exact: true }).fill("noust");
    await create.getByLabel("Remote folder").fill("/volume1/backups");
    await create.getByRole("button", { name: "Add destination" }).click();
    await confirmItsYou(page, consoleServer);
    await expect(create).toBeHidden();
    await expect(toasts(page).getByText(`Backup destination created: ${name}`, { exact: true })).toBeVisible();
    await expect(rows()).toHaveCount(1);

    // It landed on the node's own configuration, not the central's: the central's own
    // destinations never saw it, which only the proxy actually reaching the node could produce.
    await page.goto("/backups/destinations");
    await expect(page.getByRole("region", { name: "Backup destinations" })).toBeVisible();
    await expect(rows()).toHaveCount(0);

    await page.goto(`/n/${NODE_NAME}/backups/destinations`);
    await page.getByRole("button", { name: `Actions for ${name}`, exact: true }).click();
    await page.getByRole("menuitem", { name: "Remove" }).click();
    const remove = page.getByRole("alertdialog", { name: `Remove ${name}` });
    await remove.getByRole("button", { name: "Remove destination" }).click();
    await expect(remove).toBeHidden();
    await expect(rows()).toHaveCount(0);
  },
);

withFleet("Settings > Servers shows the node and the add flow's first step", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/settings/servers");
  // Settings shares one heading across every section (SettingsLayout's own PageHeader); the
  // "Servers" table below is what actually says which tab this is.
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Settings");

  const table = page.getByRole("region", { name: "Servers this central manages" });
  const row = table.getByRole("row").filter({ hasText: NODE_NAME });
  await expect(row).toBeVisible();
  await expect(row.getByText("Reachable")).toBeVisible();

  await page.getByRole("button", { name: "Add a server" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a server" });
  await dialog.getByLabel("Name", { exact: true }).fill("temp-add-check");
  await dialog.getByRole("button", { name: "Show the command" }).click();
  // The command names this central (`--name <central>`), not the server being added: it is
  // what the new server will call this central back, not the node's own name.
  const command = dialog.getByTestId("authorize-command");
  await expect(command).toBeVisible();
  await expect(command).toContainText("noust fleet authorize");
  await expect(command).toContainText("--central-key");

  await settle(page);
  await expectNoA11yViolations(page, "the add server dialog, step 1");
});

// ---------------------------------------------------------------------------------------
// The node going down would break every test above sharing the worker's pair, so this one
// starts and tears down its own, and is free to stop the node mid-test.

test("a node that stops answering says so, on its own pages and the Fleet page's attention", async ({ page, problems }) => {
  const node = await startConsoleServer([], { env: { NOUST_E2E_FLEET_APP: NODE_APP } });
  const central = await startConsoleServer([
    "--totp",
    "--backup-codes",
    "500",
    "--fleet-node",
    `${NODE_NAME}=127.0.0.1:${portOf(node)}`,
  ]);
  try {
    problems.expect(new RegExp(`status of 50\\d .*\\/api\\/nodes\\/${NODE_NAME}\\/`));
    await signInAt(page, central, `/n/${NODE_NAME}/apps`);
    await expect(page.getByRole("link", { name: NODE_APP, exact: true })).toBeVisible();

    await node.stop();

    await page.reload();
    await expect(page.getByText(`${NODE_NAME} is not answering`)).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "a node that is not answering");

    await page.goto(`${central.url}/fleet`);
    // Partial, never blank: the node that is down is named, with the central's own words.
    await expect(page.getByText("1 of 2 servers did not answer fully")).toBeVisible();
    await expect(page.getByText("Not answering").first()).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "the Fleet's summary, with a server down");
  } finally {
    // Closed before either server stops: an authenticated page keeps an /events stream and
    // polls its session, and stopping the backend under it would fail those as console
    // errors the "problems" fixture is still watching for, wrongly, this test's own teardown.
    await page.close();
    await Promise.all([node.stop(), central.stop()]);
  }
});

test("a sealed central shows the lock screen, and opens with the right passphrase", async ({ page, problems }) => {
  const passphrase = "correct horse battery staple";
  const central = await startConsoleServer(["--seal"], { stdin: `${passphrase}\n` });
  try {
    problems.expect(/status of 403 .*\/api\/central\/unlock$/);
    await signInAt(page, central, "/");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("This console is locked");
    await settle(page);
    await expectNoA11yViolations(page, "the locked central's screen");

    await page.getByLabel("Passphrase").fill(passphrase);
    // Exact: "Continue without unlocking" contains "unlocking", a substring match of "Unlock".
    await page.getByRole("button", { name: "Unlock", exact: true }).click();
    await confirmItsYou(page, central);
    await expect(toasts(page).getByText("Unlocked: the servers are within reach again")).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Overview");
  } finally {
    await page.close();
    await central.stop();
  }
});

test("a hub sends its own pages to the Fleet instead", async ({ page }) => {
  const central = await startConsoleServer(["--central-role", "hub"]);
  try {
    await signInAt(page, central, "/apps");
    await expect(page).toHaveURL(/\/fleet(\?|$)/);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Fleet");
    await expect(page.getByText("This central deploys nothing itself")).toBeVisible();

    await page.goto(`${central.url}/`);
    await expect(page).toHaveURL(/\/fleet(\?|$)/);
    await settle(page);
    await expectNoA11yViolations(page, "the Fleet page, opened for a hub");
  } finally {
    await page.close();
    await central.stop();
  }
});

/** A join code that reads as one but was made for another central's key: the central refuses it. */
function foreignJoinCode(): string {
  const field = (text: string) => {
    const bytes = Buffer.from(text);
    const length = Buffer.alloc(4);
    length.writeUInt32BE(bytes.length);
    return Buffer.concat([length, bytes]);
  };
  const blob = Buffer.concat([field("ssh-ed25519"), (() => {
    const key = Buffer.alloc(32, 7);
    const length = Buffer.alloc(4);
    length.writeUInt32BE(32);
    return Buffer.concat([length, key]);
  })()]);
  const document = {
    ssh_host_key_line: `ssh-ed25519 ${blob.toString("base64")}`,
    ssh_user: "noust-tunnel",
    ssh_port: 22,
    console_port: 8080,
    token: `noust_tok_${"a".repeat(43)}`,
    noust_version: "3.1.0",
    central_key_fp: `SHA256:${"A".repeat(43)}`,
    node_name: "temp-join-check",
  };
  return `noust-join:v1:${Buffer.from(JSON.stringify(document)).toString("base64url")}`;
}

// ---------------------------------------------------------------------------------------
// A fleet of three: one node answers, one is down, one runs an older Noust. The central's
// views answer for every server, partially, never blank.

test("a node that is down and one on an older Noust: the views answer partially, in the servers' own words", async ({ page, problems }) => {
  const [up, down, older] = await Promise.all([
    startConsoleServer([], { env: { NOUST_E2E_FLEET_APP: NODE_APP } }),
    startConsoleServer([]),
    startConsoleServer([], { env: { NOUST_E2E_OLDER_NODE: "3.0.0" } }),
  ]);
  const central = await startConsoleServer([
    "--totp",
    "--backup-codes",
    "500",
    "--fleet-node",
    `web-2=127.0.0.1:${portOf(up)}`,
    "--fleet-node",
    `db-1=127.0.0.1:${portOf(down)}`,
    "--fleet-node",
    `old-1=127.0.0.1:${portOf(older)}`,
  ]);
  try {
    problems.expect(/status of 50\d .*\/api\/nodes\/db-1\//);
    problems.expect(/status of 404 .*\/api\/nodes\/old-1\//);
    await down.stop();
    await signInAt(page, central, "/fleet");
    await expect(page.getByText("2 of 4 servers did not answer fully")).toBeVisible({ timeout: 20_000 });
    const notice = page.getByRole("list", { name: "Servers that did not answer fully" });
    await expect(notice.getByText("db-1", { exact: true })).toBeVisible();
    await expect(notice.getByText("old-1", { exact: true })).toBeVisible();
    await expect(notice.getByText("Older Noust", { exact: true })).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "a partial fleet's summary");

    await page.getByRole("navigation", { name: "Fleet views" }).getByRole("link", { name: "Applications" }).click();
    // web-2 answered: its application is there, even though db-1 did not. Four servers' worth
    // is more than one page of the list, so it is looked for as an operator would.
    await page.getByRole("searchbox", { name: "Search applications" }).fill(NODE_APP);
    await expect(page.getByRole("region", { name: "Applications on every server" }).getByRole("link", { name: NODE_APP, exact: true })).toBeVisible();

    await page.getByRole("navigation", { name: "Fleet views" }).getByRole("link", { name: "Updates" }).click();
    const updates = page.getByRole("region", { name: "Updates of every server" });
    await expect(updates.getByRole("row").filter({ hasText: "old-1" })).toContainText("3.0.0");
    await settle(page);
    await expectNoA11yViolations(page, "a partial fleet's updates");
  } finally {
    await page.close();
    await Promise.all([up.stop(), down.stop(), older.stop(), central.stop()]);
  }
});
