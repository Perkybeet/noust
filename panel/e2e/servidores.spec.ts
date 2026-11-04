/**
 * 4.1: a server's page as sections, the list that opens it and the catalog's honest tiles, against
 * the real backend (4.2: the system's views are sections of the server, and Security and Network are areas of their own).
 * A lone server first (its page is its name and nine sections, the ports say who
 * holds each, the web server says what moving would do, the files say what is kept), then a central
 * with a node: the Servers area opens the list, a row opens that server, and its permissions section
 * shows what the server lets the central do.
 *
 * The pictures of these pages, both themes at 1440 and 390, are the last test (tagged @screens).
 */

import path from "node:path";

import type { Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, signIn, startConsoleServer, test } from "./fixtures";
import type { ConsoleServer, RunningServer } from "./fixtures";
import { confirmItsYou } from "./settings.helpers";

const SECTIONS = ["Status", "This server", "Processes", "Resource monitor", "Updates", "Services", "Web server", "Files", "Terminal"] as const;

function column(page: Page) {
  return page.getByRole("navigation", { name: "Server sections" });
}

test("a lone server's page is its name and nine sections, and every one opens without a violation", async ({ page, consoleServer }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer, "/server");
  const links = await column(page).getByRole("link").allTextContents();
  // A count rides after a name that has one ("System and updates8"): the names are what is held.
  expect(links.map((text) => SECTIONS.find((name) => text.startsWith(name)))).toEqual([...SECTIONS]);
  // The header names the machine: the one page where it does.
  const title = await page.getByRole("heading", { level: 1 }).textContent();
  expect(title).not.toBe("Server");
  expect(await column(page).getByRole("link", { name: "Central's permissions" }).count()).toBe(0);

  for (const name of SECTIONS) {
    await column(page).getByRole("link", { name: new RegExp(`^${name}`) }).click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(title ?? "");
    await expect(column(page).getByRole("link", { name: new RegExp(`^${name}`) })).toHaveAttribute("aria-current", "page");
    await settle(page);
    await expectNoA11yViolations(page, `Server > ${name}`);
  }
});

test("the 4.0 addresses of a server's tabs go to where each thing is now", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/server/system");
  await expect(page).toHaveURL(/\/server\/updates$/);
  await page.goto("/server/storage");
  await expect(page).toHaveURL(/\/server\/files$/);
  await page.goto("/server/security?view=firewall");
  await expect(page).toHaveURL(/\/network\/firewall$/);
  await page.goto("/server/system?view=network");
  await expect(page).toHaveURL(/\/network\/interfaces$/);
  // 4.2: Security and Network are areas of their own, and the views of System are sections of the server.
  await page.goto("/server/security");
  await expect(page).toHaveURL(/\/security$/);
  await page.goto("/server/security?view=bans");
  await expect(page).toHaveURL(/\/security\/blocks$/);
  await page.goto("/server/network?view=interfaces");
  await expect(page).toHaveURL(/\/network\/interfaces$/);
  await page.goto("/server/system?view=processes");
  await expect(page).toHaveURL(/\/server\/processes$/);
  await page.goto("/domains/ports");
  await expect(page).toHaveURL(/\/network$/);
  await page.goto("/domains/tunnels");
  await expect(page).toHaveURL(/\/network\/tunnels$/);
});

test("Network and ports: who uses a port, a free one is offered to open, the firewall is a section of Network, and Published ports are the first of its sections", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/network/listening");
  const claims = (await (await page.request.get("/api/server/ports")).json()) as { claims: { port: number; protocol: string; kind: string; owner: string }[] };
  const application = claims.claims.find((claim) => claim.kind === "app");
  expect(application, "the seeded server has an application holding a port").toBeDefined();

  const search = page.getByRole("searchbox", { name: "Search ports" });
  await search.fill(String(application?.port));
  const table = page.getByRole("table", { name: "Ports and what uses them" });
  await expect(table.getByRole("row").filter({ hasText: application?.owner ?? "" }).first()).toBeVisible();
  // The router writes a number typed in a search box as a JSON string: q="3000".
  await expect(page).toHaveURL(new RegExp(`q=(%22)?${String(application?.port)}`));

  await search.fill("65001");
  await expect(page.getByText("Nothing on this server uses port 65001.")).toBeVisible();
  await page.getByRole("button", { name: "Open port 65001" }).click();
  const dialog = page.getByRole("alertdialog", { name: "Open or close a port" });
  await expect(dialog.getByLabel("Port")).toHaveValue("65001");
  await settle(page);
  await expectNoA11yViolations(page, "the dialog that opens a port");
  await dialog.getByRole("button", { name: "Cancel" }).click();

  const sections = page.getByRole("navigation", { name: "Network sections" });
  await sections.getByRole("link", { name: "Firewall" }).click();
  await expect(page).toHaveURL(/\/network\/firewall$/);
  await expect(page.getByRole("region", { name: "Ports that listen" })).toBeVisible();
  await sections.getByRole("link", { name: "Interfaces" }).click();
  await expect(page.getByRole("table", { name: "Network interfaces" })).toBeVisible();
  // Each side leads to the other with one line: this is where what listens is, Domains is where an application is published.
  await sections.getByRole("link", { name: "Published ports" }).click();
  await expect(page).toHaveURL(/\/network$/);
  await expect(page.getByRole("button", { name: "Publish a port" }).first()).toBeVisible();
  await page.getByRole("main").getByRole("link", { name: "Listening ports" }).click();
  await expect(page).toHaveURL(/\/network\/listening$/);
});

test("Web server: both servers and what is behind each, and the move says what it would do before it asks for anything", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/server/webserver");
  await expect(page.getByRole("heading", { name: "nginx" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Apache" })).toBeVisible();
  await expect(page.getByText("Which web server a new application is put behind. It moves none of the ones already here.")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the web server");

  await page.getByRole("button", { name: /^Move to / }).click();
  const dialog = page.getByRole("dialog", { name: /^Move to / });
  // The plan is read first: what happens to ports 80 and 443, or what stops it. Nothing is changed by looking.
  await expect(dialog.getByText(/ports 80 and 443/).first()).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the plan of moving to the other web server");
  // The dialog's corner button and the footer's say the same: either closes it.
  await dialog.getByRole("button", { name: /^(Close|Cancel)$/ }).last().click();
  await expect(dialog).toBeHidden();
});

test("Files: what each application keeps between deployments is listed beside the disks", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/server/files");
  await expect(page.getByRole("heading", { name: "Kept between deployments" })).toBeVisible();
  await expect(page.getByRole("list", { name: "Filesystems" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the files of a server");
});

test("Security and SSH: the server's SSH key is made from the console and shown whole to copy", async ({ page, consoleServer, problems }) => {
  // Making the key writes the server's SSH identity: it asks for sudo mode first.
  problems.expect(/status of 403 .*\/api\/deploy-keys\/server$/);
  await signIn(page, consoleServer, "/security/ssh");
  const card = page.locator("section", { has: page.getByRole("heading", { name: "The server's SSH key" }) });
  await expect(card).toBeVisible();
  const create = card.getByRole("button", { name: "Create the key" });
  if (await create.isVisible()) {
    await create.click();
    await confirmItsYou(page, consoleServer);
  }
  await expect(card.getByText(/^ssh-ed25519 /)).toBeVisible();
  await expect(card.getByText("Fingerprint")).toBeVisible();
  await expect(card.getByRole("button", { name: "Create the key" })).toHaveCount(0);
  await settle(page);
  await expectNoA11yViolations(page, "the server's SSH key");
});

test("the catalog lists what cannot be installed here with its reason, in a section of its own", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/catalog");
  const section = page.getByRole("region", { name: "Not available here" });
  await expect(section).toBeVisible();
  await expect(section.getByText("Not available in this release").first()).toBeVisible();
  // The reason is a sentence; what the check found is folded under it.
  await expect(section.getByRole("button", { name: "What was checked" }).first()).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the catalog with its unavailable recipes");
});

interface FleetFixtures {
  node: RunningServer;
  central: ConsoleServer;
}

const withFleet = test.extend<object, FleetFixtures>({
  node: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const node = await startConsoleServer([]);
      try {
        await use(node);
      } finally {
        await node.stop();
      }
    },
    { scope: "worker", timeout: 75_000 },
  ],
  central: [
    async ({ node }, use) => {
      const central = await startConsoleServer(["--totp", "--backup-codes", "500", "--fleet-node", `fleet-node=127.0.0.1:${new URL(node.url).port}`]);
      try {
        await use(central);
      } finally {
        await central.stop();
      }
    },
    { scope: "worker", timeout: 90_000 },
  ],
  // The pages are served from the central: the browser's base is its address.
  baseURL: async ({ central }, use) => {
    await use(central.url);
  },
});

withFleet("on a fleet Servers opens the list, a row opens that server, and its permissions are shown with the command that changes them", async ({ page, central }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, central, "/");
  // The first request a central makes to a node opens the tunnel, which takes seconds (more on a busy machine); the pages
  // under test are not about that wait, so it is spent here, once, and not inside the 10 s a page is given to arrive.
  const reached = await page.request.get("/api/nodes/fleet-node/api/system/version", { timeout: 90_000 });
  expect(reached.ok(), await reached.text()).toBe(true);
  // The sidebar's link says how many services failed after its name ("Servers 1 failed") once the counts arrive, which is
  // sometime after the first heading: an exact name found the link only until then. And it leads to the fleet's list only
  // once the console knows it is a central's, so it is pressed when it does.
  const servers = page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: /^Servers\b/ });
  await expect(servers).toHaveAttribute("href", /\/fleet\/servers$/);
  await servers.click();
  await expect(page).toHaveURL(/\/fleet\/servers$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Servers");
  await settle(page);
  await expectNoA11yViolations(page, "the list of servers");

  await page.getByRole("link", { name: "Open fleet-node" }).click();
  await expect(page).toHaveURL(/\/n\/fleet-node\/server$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("fleet-node");
  await column(page).getByRole("link", { name: "Central's permissions" }).click();
  await expect(page.getByText("fleet-node lets the central: Everything.")).toBeVisible();
  await page.getByRole("radio", { name: /Read only/ }).click();
  await expect(page.getByText("noust fleet access --level read --host-access off")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the central's permissions on a server");

  // From the permissions column of the list the same section opens.
  await page.goto("/fleet/servers");
  await page.getByRole("link", { name: "Everything" }).first().click();
  await expect(page).toHaveURL(/\/n\/fleet-node\/server\/central$/);
});

withFleet("adding a server asks for an address and a way in, and suggests the name", async ({ page, central }) => {
  await signIn(page, central, "/fleet/servers");
  await page.getByRole("button", { name: "Add a server" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Add a server" });
  await dialog.getByLabel(/^Address/).fill("web2.example.com");
  await expect(dialog.getByLabel(/^Name/)).toHaveValue("web2");
  // The account, the port and what the central may do are one fold away, not in the way.
  await expect(dialog.getByLabel("Account")).toBeHidden();
  await dialog.getByRole("button", { name: "Account, port and permissions" }).click();
  await expect(dialog.getByLabel("Account")).toHaveValue("root");
  await settle(page);
  await expectNoA11yViolations(page, "the dialog that adds a server");
});

const SCREEN_DIR = process.env.NOUST_SCREENS_DIR ?? path.join(import.meta.dirname, "__screens__");

withFleet("@screens a server's sections, the list of servers and the permissions, at 1440 and 390", async ({ page, central }, testInfo) => {
  test.setTimeout(10 * 60_000);
  const theme = testInfo.project.name;
  await signIn(page, central, "/");
  const pages: readonly { name: string; to: string }[] = [
    { name: "servers", to: "/fleet/servers" },
    { name: "server-status", to: "/n/fleet-node/server" },
    { name: "server-system", to: "/n/fleet-node/server/updates" },
    { name: "server-security", to: "/n/fleet-node/security/ssh" },
    { name: "server-network", to: "/n/fleet-node/network/listening" },
    { name: "network-published-ports", to: "/network" },
    { name: "server-webserver", to: "/n/fleet-node/server/webserver" },
    { name: "server-files", to: "/n/fleet-node/server/files" },
    { name: "server-central", to: "/n/fleet-node/server/central" },
  ];
  for (const size of [
    { width: 1440, height: 900 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(size);
    for (const entry of pages) {
      await page.goto(entry.to);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await settle(page);
      await page.screenshot({ path: path.join(SCREEN_DIR, theme, `o5-servidores-${entry.name}-${String(size.width)}.png`), fullPage: true });
    }
  }
});
