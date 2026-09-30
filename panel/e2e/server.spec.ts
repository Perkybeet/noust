/**
 * The Server area against the real backend, over console_server.py's modelled VPS (Ubuntu
 * 24.04 with security updates pending, a reboot due, passwords on in SSH, PostgreSQL open to
 * the internet through a forgotten rule, fail2ban banning two addresses): every tab, its
 * state and actions, axe on each, and the flows that change nothing irreversible.
 */

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";
import { confirmItsYou } from "./settings.helpers";

test("the overview puts what needs attention first, and fits the first screen", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer, "/server");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Server");
  const attention = page.getByRole("list", { name: "What needs attention" });
  await expect(attention.getByText(/security updates are pending/)).toBeVisible();
  await expect(attention.getByText(/A reboot is needed to finish updating/)).toBeVisible();
  await expect(attention.getByText("A database or internal service is reachable from the internet")).toBeVisible();
  // The readings are the last thing of the tab: on a 1440x900 screen, in view without scrolling.
  await expect(page.getByText("Fullest disk")).toBeInViewport();

  await settle(page);
  await expectNoA11yViolations(page, "the server overview");
});

test("schedules a reboot with its pre-checks in view, shows it on every tab and cancels it", async ({ page, consoleServer, problems }) => {
  problems.expect(/status of 403 .*\/api\/server\/power\/reboot$/);
  await signIn(page, consoleServer, "/server");
  await page.getByRole("button", { name: "Reboot", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Reboot the server" });
  const checks = dialog.getByRole("list", { name: "Checks before rebooting" });
  await expect(checks.getByText("Nothing is running", { exact: true })).toBeVisible();
  await expectNoA11yViolations(page, "the reboot dialog");
  await dialog.getByRole("radio", { name: "Later" }).click();
  await dialog.getByRole("button", { name: /^Reboot/ }).click();
  await confirmItsYou(page, consoleServer);
  await expect(dialog).toBeHidden();

  await expect(page.getByText(/^Reboot scheduled for/).first()).toBeVisible();
  await page.getByRole("navigation", { name: "Server sections" }).getByRole("link", { name: "Storage" }).click();
  const banner = page.getByText(/^Reboot scheduled for/).first();
  await expect(banner).toBeVisible();
  const cancelled = page.waitForResponse((response) => response.url().endsWith("/api/server/power/scheduled"));
  await page.getByRole("button", { name: "Cancel reboot" }).first().click();
  expect((await cancelled).status()).toBe(200);
  await expect(page.getByText(/^Reboot scheduled for/)).toHaveCount(0);
});

test("the updates tab lists security updates first and shows the plan before installing", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/server/updates");
  const table = page.getByRole("region", { name: "Pending updates" });
  const first = table.getByRole("row").nth(1);
  await expect(first).toContainText("linux-image-6.8.0-47-generic");
  await expect(first).toContainText("Security");
  await expect(page.getByText("A reboot has been needed since", { exact: false })).toBeVisible();
  await expect(page.getByRole("switch", { name: "Automatic security updates" })).toBeChecked();
  await settle(page);
  await expectNoA11yViolations(page, "the updates tab");

  await page.getByRole("button", { name: /^Install security updates/ }).click();
  const dialog = page.getByRole("dialog", { name: "Install security updates" });
  await expect(dialog.getByText(/install --only-upgrade --no-remove libssl3t64 openssl/)).toBeVisible();
  await expectNoA11yViolations(page, "the install dialog");
  await dialog.getByRole("button", { name: "Cancel" }).click();

  // A full upgrade lists what it removes, and goes on only once that is ticked.
  await page.getByRole("button", { name: /^Install all/ }).click();
  const all = page.getByRole("dialog", { name: "Install all updates" });
  await all.getByRole("checkbox", { name: "Full upgrade" }).click();
  await expect(all.getByText("This upgrade removes 1 package")).toBeVisible();
  await expect(all.getByText("linux-image-6.8.0-31-generic")).toBeVisible();
  await all.getByRole("button", { name: /^Install \d+ updates$/ }).click();
  await expect(all.getByText(/Tick Remove these packages to go on/)).toBeVisible();
  await all.getByRole("button", { name: "Cancel" }).click();
});

test("the security tab shows its findings, SSH as it runs, the firewall against what listens and the bans", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/server/security");
  const findings = page.getByRole("list", { name: "Open findings" });
  await expect(findings.getByText("SSH accepts passwords", { exact: true })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the security checks");

  // Fixing asks for the host name, and says the change undoes itself unless confirmed.
  const row = findings.getByRole("listitem").filter({ hasText: "SSH accepts passwords" });
  await row.getByRole("button", { name: "Fix…" }).click();
  const fix = page.getByRole("dialog", { name: "Fix: SSH accepts passwords" });
  await expect(fix.getByText("Undone unless you confirm it")).toBeVisible();
  await expect(fix.getByRole("button", { name: "Apply fix" })).toBeDisabled();
  await expectNoA11yViolations(page, "the fix dialog");
  await fix.getByRole("button", { name: "Cancel" }).click();

  await page.getByRole("radio", { name: "SSH" }).click();
  await expect(page).toHaveURL(/view=ssh/);
  await expect(page.getByRole("heading", { name: "Effective settings" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Authorized SSH keys" }).getByText("yago@laptop")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the SSH view");

  await page.getByRole("radio", { name: "Firewall and ports" }).click();
  const ports = page.getByRole("region", { name: "Ports that listen" });
  await expect(ports.getByRole("row").filter({ hasText: "5432/tcp" }).getByText("Exposed")).toBeVisible();
  await expect(page.getByText("1 internal service is reachable from the internet")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the firewall view");

  await page.getByRole("radio", { name: "Brute force" }).click();
  await expect(page.getByRole("list", { name: "Addresses banned by sshd" }).getByText("185.220.101.4")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the bans view");
});

test("the storage, logs and system tabs show the machine", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/server/storage");
  await expect(page.getByRole("list", { name: "Filesystems" }).getByText("/srv")).toBeVisible();
  await expect(page.getByRole("region", { name: "What takes space" }).getByText("System journal")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the storage tab");

  await page.goto("/server/logs?priority=err");
  await expect(page.getByText(/nginx\.service\[1021\]: .*connect\(\) failed/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the logs tab");

  await page.goto("/server/system");
  await expect(page.getByText("Europe/Madrid")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Reboot and shutdown" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the system tab");
});

test("the resource monitor shows its unit and open findings under System", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/server/system?view=monitor");
  const monitor = page.locator("section", { has: page.getByRole("heading", { name: "Resource monitor" }) });
  await expect(monitor).toBeVisible();
  await expect(monitor.getByText("Running")).toBeVisible();
  await expect(monitor.getByText("xmrig")).toBeVisible();
});

test("acknowledging an open finding of the resource monitor removes it", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/server/system?view=monitor");
  const monitor = page.locator("section", { has: page.getByRole("heading", { name: "Resource monitor" }) });
  await expect(monitor.getByText("xmrig")).toBeVisible();

  const acknowledged = page.waitForResponse((response) => /\/api\/monitor\/observations\/\d+\/acknowledge$/.test(response.url()));
  await monitor.getByRole("button", { name: "Acknowledge finding about xmrig" }).click();
  expect((await acknowledged).status()).toBe(200);
  await expect(monitor.getByText("xmrig")).toBeHidden();
});
