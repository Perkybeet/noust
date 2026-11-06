/**
 * Server > Software and "X is missing" (4.2, item 118), against the real backend and the machine modelled in
 * scripts/console_server/software_host.py: the list by group, the three ways removing can go (a question, the server's
 * refusal, and a piece that was not Noust's), installing, the notice that installs what a page lacks, and the software of
 * a fleet.
 *
 * What these tests install and remove changes the modelled machine, which other specs read (the Ports page says the
 * stream module is missing, Tunnels that cloudflared is), so they run on a console server of their own, not on the
 * worker's shared one.
 */

import { confirmItsYou, expect, expectNoA11yViolations, settle, signIn, startConsoleServer, test as base } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

const test = base.extend<object, { own: ConsoleServer }>({
  own: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const server = await startConsoleServer(["--totp", "--backup-codes", "200"]);
      try {
        await use(server);
      } finally {
        await server.stop();
      }
    },
    { scope: "worker", timeout: 75_000 },
  ],
  baseURL: async ({ own }, use) => {
    await use(own.url);
  },
});

/** The row of a piece in the table of its group, by the name the page gives it. */
function row(page: import("@playwright/test").Page, name: string) {
  return page.getByRole("row").filter({ has: page.getByText(name, { exact: true }) }).first();
}

test("the software of the server by group, with who put it there and what depends on it", async ({ page, own }) => {
  await signIn(page, own, "/server/software");
  // The machine's own seven pieces, which Noust never removes, are one line until asked for.
  await expect(page.getByRole("table", { name: "Software: The machine" })).toHaveCount(0);
  await page.getByRole("button", { name: /^Noust, systemd, SSH server/ }).click();
  for (const group of ["The machine", "Sites and certificates", "Applications", "Databases", "Network", "Protection and upkeep"]) {
    await expect(page.getByRole("table", { name: `Software: ${group}` })).toBeVisible();
  }
  const nginx = row(page, "nginx");
  await expect(nginx.getByText(/^From Noust/)).toBeVisible();
  await expect(nginx.getByText("17 applications")).toBeVisible();
  await expect(nginx.getByText("When nothing uses it")).toBeVisible();
  const systemd = row(page, "systemd");
  await expect(systemd.getByText("Not from Noust")).toBeVisible();
  await expect(systemd.getByText("Never")).toBeVisible();
  // A piece that is not there says so, and offers to install it.
  await expect(row(page, "MongoDB").getByText("Not installed").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Install MongoDB" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Server > Software");

  // The filter keeps the groups that have something to show.
  await page.getByRole("radio", { name: /^Not installed/ }).click();
  await expect(page.getByRole("table", { name: "Software: The machine" })).toHaveCount(0);
  await expect(page.getByRole("table", { name: "Software: Databases" })).toBeVisible();
});

test("the server refuses to remove what is in use, says why and what, and leads to it", async ({ page, own }) => {
  await signIn(page, own, "/server/software");
  await page.getByRole("button", { name: "Remove nginx" }).click();
  const dialog = page.getByRole("dialog", { name: "nginx cannot be removed now" });
  await expect(dialog.getByText("nginx cannot be removed while something depends on it: 17 applications")).toBeVisible();
  await expect(dialog.getByText("Stop using it first (remove or move what is listed), then remove it.")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the refusal to remove nginx");

  await dialog.getByRole("link", { name: "See the applications" }).click();
  await expect(page).toHaveURL(/\/apps$/);
  // Nothing was queued: a refusal is the manager's answer before any job exists.
  const active = (await (await page.request.get("/api/jobs/active")).json()) as { jobs: { name: string }[] };
  expect(active.jobs.filter((job) => job.name.startsWith("Remove "))).toEqual([]);
});

test("removes what Noust installed after one question, in sudo mode, and the list says it is gone", async ({ page, own, problems }) => {
  problems.expect(/status of 403 .*\/api\/server\/software\/fail2ban\/remove$/);
  await signIn(page, own, "/server/software");
  await page.getByRole("button", { name: "Remove fail2ban" }).click();
  const question = page.getByRole("alertdialog", { name: "Remove fail2ban" });
  await expect(question.getByText("python3-pyinotify")).toBeVisible();
  await expect(question.getByText("Nothing: no application, site or backup uses it.")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the question before removing fail2ban");

  await question.getByRole("button", { name: "Remove fail2ban" }).click();
  await confirmItsYou(page, own);
  await expect(question).toBeHidden();
  // The task is followed where it was started, and the row changes when it ends.
  await expect(page.getByRole("button", { name: "Install fail2ban" })).toBeVisible({ timeout: 30_000 });
  await expect(row(page, "fail2ban").getByText("Not installed").first()).toBeVisible();
});

test("asks for more than a click for what Noust did not install: a yes and the name", async ({ page, own, problems }) => {
  problems.expect(/status of 403 .*\/api\/server\/software\/chrony\/remove$/);
  await signIn(page, own, "/server/software");
  await page.getByRole("button", { name: "Remove chrony" }).click();
  const question = page.getByRole("alertdialog", { name: "Remove chrony" });
  await expect(question.getByText("Noust did not install it: other things on the system may use it without Noust knowing.")).toBeVisible();
  const remove = question.getByRole("button", { name: "Remove chrony" });
  await expect(remove).toBeDisabled();
  await settle(page);
  await expectNoA11yViolations(page, "the stronger question before removing what Noust did not install");

  await question.getByRole("checkbox", { name: /I know Noust did not install it/ }).click();
  await question.getByRole("textbox").fill("chrony");
  await expect(remove).toBeEnabled();
  await remove.click();
  await confirmItsYou(page, own);
  await expect(page.getByRole("button", { name: "Install chrony" })).toBeVisible({ timeout: 30_000 });
});

test("installs a piece from its row as a task, and the list says it is there", async ({ page, own, problems }) => {
  problems.expect(/status of 403 .*\/api\/server\/software\/apache\/install$/);
  await signIn(page, own, "/server/software");
  await expect(page.getByRole("button", { name: "Remove Apache" })).toHaveCount(0);
  await page.getByRole("button", { name: "Install Apache" }).click();
  await confirmItsYou(page, own);
  await expect(page.getByRole("button", { name: "Remove Apache" })).toBeVisible({ timeout: 45_000 });
  await expect(row(page, "Apache").getByText(/^From Noust/)).toBeVisible();
});

test("a page that lacks a piece says it is missing, installs it as a task, and the page changes when it is there", async ({ page, own, problems }) => {
  problems.expect(/status of 403 .*\/api\/server\/software\/nginx_stream_module\/install$/);
  await signIn(page, own, "/network");
  await expect(page.getByText("nginx stream module is missing")).toBeVisible();
  await expect(page.getByText(/Without it nginx cannot publish a TCP or UDP port that is not web\./)).toBeVisible();
  // Opening a port is a firewall rule when the program listens outside: only a port that nginx must carry needs the module, so the button stays.
  await expect(page.getByRole("button", { name: "Open a port" }).first()).toBeEnabled();
  await settle(page);
  await expectNoA11yViolations(page, "Published ports with the stream module missing");

  await page.getByRole("button", { name: "Install" }).click();
  await confirmItsYou(page, own);
  // Done: nginx can publish a port, and the notice that said it could not is gone.
  await expect(page.getByText("nginx stream module is missing")).toHaveCount(0, { timeout: 45_000 });
  await expect(page.getByRole("button", { name: "Open a port" }).first()).toBeEnabled();
  // The install was a task. The modelled machine may finish it before its running state is drawn, so the task centre is
  // asked once it is over: it keeps the task, ended, with its page a press away.
  await page.getByRole("button", { name: /^Tasks/ }).click();
  await expect(page.getByRole("link", { name: /Install nginx stream module/ }).first()).toBeVisible();
});

test("when the install fails the page says so with the tool's own words, and offers to try again", async ({ page, own, problems }) => {
  problems.expect(/status of 403 .*\/api\/server\/software\/cloudflared\/install$/);
  await signIn(page, own, "/network/tunnels");
  await expect(page.getByText("cloudflared is missing")).toBeVisible();
  await page.getByRole("button", { name: "Install" }).click();
  await confirmItsYou(page, own);
  // The modelled machine cannot reach Cloudflare's repository, which is what a server behind a proxy says too.
  await expect(page.getByText("cloudflared was not installed")).toBeVisible({ timeout: 45_000 });
  await expect(page.getByText(/The signing key served for pkg\.cloudflare\.com is not the one Noust trusts/).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Tunnels after an install that failed");
});

/** A central and a node, both of them this build, for what the fleet says of every server's software. */
const fleet = base.extend<object, { node: ConsoleServer; central: ConsoleServer }>({
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
      const central = await startConsoleServer(["--totp", "--backup-codes", "200", "--fleet-node", `fleet-node=127.0.0.1:${new URL(node.url).port}`]);
      try {
        await use(central);
      } finally {
        await central.stop();
      }
    },
    { scope: "worker", timeout: 90_000 },
  ],
  baseURL: async ({ central }, use) => {
    await use(central.url);
  },
});

fleet("the fleet says what is installed on each server, and a server opens its own software", async ({ page, central }) => {
  test.setTimeout(150_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, central, "/");
  // The first request a central makes to a node opens the tunnel, which takes seconds: it is spent here, once.
  const reached = await page.request.get("/api/nodes/fleet-node/api/system/version", { timeout: 90_000 });
  expect(reached.ok(), await reached.text()).toBe(true);

  await page.goto("/fleet/software");
  const table = page.getByRole("table", { name: "Software on each server" });
  const node = table.getByRole("row", { name: /fleet-node/ });
  await expect(node.getByRole("link", { name: "Open the software of fleet-node" })).toBeVisible({ timeout: 60_000 });
  // 18 installed of the 25 the registry has, in the modelled machine both of them are.
  await expect(node.getByText("18")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the software of the fleet");

  await node.getByRole("link", { name: "Open the software of fleet-node" }).click();
  await expect(page).toHaveURL(/\/n\/fleet-node\/server\/software$/);
  // A node's own page, with the machine's seven pieces folded into their line as on any server.
  await expect(page.getByRole("button", { name: /^Noust, systemd/ })).toBeVisible();
});
