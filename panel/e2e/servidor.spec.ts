/**
 * The Server and Security areas of 4.2, task by task, against the real backend and its modelled VPS, in both themes
 * (the light and dark projects): the tasks of the walk of the mockups that belong to them (why is it slow, install the
 * security updates, unblock a client's address, who has entered), each with axe and the CSP gate, and the pages that
 * had no page before (the web firewall, access and sessions). The pictures of every page, at 1440, 1920 and 390, are
 * the last test (tagged @screens): `NOUST_SCREENS=1 NOUST_SCREENS_DIR=/tmp/servidor-screens`.
 */

import path from "node:path";

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";
import { confirmItsYou } from "./settings.helpers";

test("why is it slow: Status says what uses the machine and whose it is, and the way to every process", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer, "/server");
  const { hostname } = (await (await page.request.get("/api/auth/session")).json()) as { hostname: string };
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(hostname);

  // The four readings, then the heaviest and what is not well, in the first screen.
  for (const name of ["CPU", "Memory", "Disk", "Powered on"]) await expect(page.getByRole("heading", { level: 2, name, exact: true })).toBeVisible();
  const heaviest = page.getByRole("region", { name: "What uses the most now" });
  await expect(heaviest).toBeVisible();
  await expect(heaviest.getByRole("row")).not.toHaveCount(1);
  await expect(page.getByRole("heading", { level: 2, name: "What is not well" })).toBeInViewport();
  await settle(page);
  await expectNoA11yViolations(page, "Status");

  // How many ports answer from outside is said in the header, and leads to where they are looked at.
  await expect(page.getByRole("link", { name: /ports? open to the outside$/ })).toHaveAttribute("href", /\/network\/listening/);

  await heaviest.getByRole("link", { name: "All processes" }).click();
  await expect(page).toHaveURL(/\/server\/processes$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Processes");
  await expect(page.getByRole("link", { name: "Listening ports" })).toHaveAttribute("href", /\/network\/listening/);
  await settle(page);
  await expectNoA11yViolations(page, "Processes");

  // A search looks at the name and at whose it is.
  const search = page.getByRole("searchbox", { name: "Search processes" });
  await search.fill("zz-nothing-has-this-name");
  await expect(page.getByText("No process matches.")).toBeVisible();
  await search.fill("");
  await expect(page.getByRole("table", { name: "Processes" }).getByRole("row").nth(1)).toBeVisible();
});

test("the machine's sections are titled by themselves and say which machine they are about", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/server");
  const { hostname } = (await (await page.request.get("/api/auth/session")).json()) as { hostname: string };
  for (const [route, title] of [
    ["/server/machine", "Machine"],
    ["/server/processes", "Processes"],
    ["/server/monitor", "Resource monitor"],
    ["/server/updates", "Updates"],
    ["/server/services", "Services"],
    ["/server/files", "Files"],
  ] as const) {
    await page.goto(route);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(title);
    if (route !== "/server/services") await expect(page.locator("[data-slot=header]").getByText(hostname, { exact: true })).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, title);
  }
});

test("Machine: its name and time zone are one press from their value, the zone says what depends on it, and the power is a slab of its own", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/server/machine");
  const section = page.getByRole("region", { name: "This machine" });
  await expect(section.getByText("Europe/Madrid")).toBeVisible();
  await expect(section.getByRole("link", { name: "scheduled tasks" })).toHaveAttribute("href", /\/cron$/);
  await expect(section.getByRole("link", { name: "backups" })).toHaveAttribute("href", /\/backups$/);
  await expect(section.getByRole("button", { name: "Change time zone" })).toBeVisible();
  await expect(section.getByRole("button", { name: "Rename" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Hardware" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Reboot and shutdown" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the machine");
});

test("install the security updates: one page for Noust and the system, security first, the plan read before anything runs", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/server/updates");
  await expect(page.getByRole("heading", { level: 2, name: "Noust", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: "Automatic updates" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: "From the system" })).toBeVisible();
  // The switch is beside the name of what it turns on.
  await expect(page.getByRole("switch", { name: "Automatic security updates" })).toBeChecked();
  const first = page.getByRole("region", { name: "Pending updates" }).getByRole("row").nth(1);
  await expect(first).toContainText("Security");
  await settle(page);
  await expectNoA11yViolations(page, "Updates");

  // The page's own actions are at the right of its title.
  const header = page.locator("[data-slot=header]");
  await header.getByRole("button", { name: /^Install security updates/ }).click();
  const dialog = page.getByRole("dialog", { name: "Install security updates" });
  await expect(dialog.getByText(/install --only-upgrade --no-remove libssl3t64 openssl/)).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel" }).click();
});

test("Services: those that fail come first, whose each is is said, and a new unit written by hand is checked before it is created", async ({ page, consoleServer, problems }) => {
  // A hand-written unit is created behind sudo mode: the first answer is the 403 that asks for it.
  problems.expect(/status of 403 .*\/api\/services$/);
  // Then systemd's refusal, as a validation error that carries its own words.
  problems.expect(/status of 400 .*\/api\/services$/);
  await signIn(page, consoleServer, "/server/services");
  await expect(page.getByText("The system's services on this machine; the ones that fail come first.")).toBeVisible();
  const table = page.getByRole("table", { name: /^Services/ });
  await expect(table.getByRole("columnheader", { name: "Belongs to" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Services");

  await page.locator("[data-slot=header]").getByRole("button", { name: "New service" }).click();
  const dialog = page.getByRole("dialog", { name: "New service" });
  await dialog.getByLabel(/^Name/).fill("hand-written");
  await dialog.getByRole("radio", { name: "Advanced" }).click();
  // The same check the unit file page makes: systemd's own words, verbatim, with nothing written.
  await dialog.getByRole("button", { name: "Check the unit file" }).click();
  await expect(dialog.getByText(/Nothing was written: systemd-analyze rejected the service file/)).toBeVisible();
  await expect(dialog.getByText(/Service has no ExecStart= setting/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the advanced form of a new service");
  await dialog.getByRole("button", { name: "Create service" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(dialog.getByText(/Service has no ExecStart= setting/).first()).toBeVisible();
  await expect(page.getByRole("link", { name: "hand-written" })).toHaveCount(0);
});

test("unblock a client's address: what is blocked now comes first, found by its address, lifted in one press", async ({ page, consoleServer, problems }) => {
  // Lifting a ban is behind sudo mode: the first answer is the 403 that asks for it.
  problems.expect(/status of 403 .*\/api\/server\/security\/fail2ban\/unban$/);
  await signIn(page, consoleServer, "/security/blocks");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Blocks");
  const blocked = page.getByRole("region", { name: "Blocked now" });
  await expect(blocked.getByText("185.220.101.4")).toBeVisible();
  await expect(blocked.getByText("45.148.10.182")).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: "When it blocks" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Blocks");

  const search = blocked.getByRole("searchbox", { name: "Search an address" });
  await search.fill("185.220");
  await expect(blocked.getByText("185.220.101.4")).toBeVisible();
  await expect(blocked.getByText("45.148.10.182")).toHaveCount(0);
  await search.fill("10.255");
  await expect(page.getByText("No blocked address matches.")).toBeVisible();
  await search.fill("185.220");

  const unblocked = page.waitForResponse((response) => response.url().endsWith("/api/server/security/fail2ban/unban") && response.status() < 300);
  await blocked.getByRole("button", { name: "Unblock 185.220.101.4" }).click();
  await confirmItsYou(page, consoleServer);
  expect((await unblocked).status()).toBeLessThan(300);
});

test("SSH: who may enter, and how: a way in is taken away only after the host's name is typed", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/security/ssh");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("SSH");
  await expect(page.getByRole("region", { name: "Authorized SSH keys" }).getByText("yago@laptop")).toBeVisible();
  const passwords = page.getByRole("switch", { name: "Allow signing in with a password" });
  await expect(passwords).toBeChecked();
  await expect(page.getByRole("switch", { name: "Allow root to sign in directly" })).toBeChecked();
  await settle(page);
  await expectNoA11yViolations(page, "SSH");

  await passwords.click();
  const dialog = page.getByRole("alertdialog", { name: "Turn off password logins" });
  await expect(dialog.getByText(/undoes itself/)).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Apply" })).toBeDisabled();
  await settle(page);
  await expectNoA11yViolations(page, "the question before passwords are turned off");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(passwords).toBeChecked();

  await page.locator("[data-slot=header]").getByRole("button", { name: "Add a key" }).click();
  const adding = page.getByRole("alertdialog", { name: "Add an SSH key" });
  await expect(adding).toBeVisible();
  await adding.getByRole("button", { name: "Cancel" }).click();
});

test("Checks: what is not right comes first with the one action that deals with it, and what is well says where it is kept", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/security");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Checks");
  const look = page.getByRole("region", { name: "Worth a look" });
  const findings = look.getByRole("list", { name: "Open findings" });
  await expect(findings.getByText("SSH accepts passwords", { exact: true })).toBeVisible();
  // Why it was found is one press away, in a "?"; the system's own words are in it.
  await findings.getByRole("listitem").filter({ hasText: "SSH accepts passwords" }).getByRole("button", { name: /^Why/ }).click();
  await expect(page.getByRole("dialog").or(page.getByRole("tooltip")).first()).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("heading", { level: 2, name: "Well" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Compliance" })).toHaveAttribute("href", /\/settings\/compliance$/);
  await settle(page);
  await expectNoA11yViolations(page, "Checks");
  await page.locator("[data-slot=header]").getByRole("button", { name: "Run the checks again" }).click();
});

test("the web firewall has a page: whether the server can run it, and the level of each site", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/security/waf");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Web firewall (WAF)");
  const sites = page.getByRole("region", { name: "By site" });
  await expect(sites).toBeVisible();
  const row = sites.getByRole("row").nth(1);
  await expect(row).toBeVisible();
  // Each site's level is its own protection setting: the row says where to change it.
  await expect(row.getByRole("link", { name: /^(Change|Turn it on)/ })).toHaveAttribute("href", /\/apps\/.+\/settings\/protection/);
  await settle(page);
  await expectNoA11yViolations(page, "the web firewall");
});

test("access and sessions: who has entered by SSH, who is there now, and the terminal's recordings need audit.read", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/security/access");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Access and sessions");
  const table = page.getByRole("table", { name: "Accesses to this machine, newest first" });
  await expect(table.getByRole("row").nth(1)).toBeVisible();
  await expect(table.getByText("203.0.113.7").first()).toBeVisible();
  await expect(table.getByText("Now").first()).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Access and sessions");
});

test("the 4.1 names are still found: the old addresses of Security and the old names of its sections", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/security");
  const sections = page.getByRole("navigation", { name: "Security sections" });
  for (const name of ["Checks", "SSH", "Blocks", "Web firewall (WAF)", "Access and sessions"]) await expect(sections.getByRole("link", { name })).toBeVisible();
  await page.goto("/server/security?view=bans");
  await expect(page).toHaveURL(/\/security\/blocks$/);
});

const SCREENS = process.env.NOUST_SCREENS_DIR ?? path.join(import.meta.dirname, "__screens__");

test("@screens the pages of the Server and Security areas, at 1440, 1920 and 390", async ({ page, consoleServer }, testInfo) => {
  test.setTimeout(10 * 60_000);
  const theme = testInfo.project.name;
  await signIn(page, consoleServer, "/");
  const pages: readonly { name: string; to: string }[] = [
    { name: "estado", to: "/server" },
    { name: "maquina", to: "/server/machine" },
    { name: "procesos", to: "/server/processes" },
    { name: "monitor", to: "/server/monitor" },
    { name: "actualizaciones", to: "/server/updates" },
    { name: "servicios", to: "/server/services" },
    { name: "archivos", to: "/server/files" },
    { name: "comprobaciones", to: "/security" },
    { name: "ssh", to: "/security/ssh" },
    { name: "bloqueos", to: "/security/blocks" },
    { name: "waf", to: "/security/waf" },
    { name: "accesos", to: "/security/access" },
  ];
  for (const size of [
    { width: 1440, height: 900 },
    { width: 1920, height: 1080 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(size);
    for (const entry of pages) {
      await page.goto(entry.to);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await settle(page);
      await page.screenshot({ path: path.join(SCREENS, theme, `${entry.name}-${String(size.width)}.png`), fullPage: false });
    }
  }
});
