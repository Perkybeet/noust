/**
 * What the last lanes of the server left to say, against the real backend in both themes with axe, the CSP and the console gates: a copy that
 * was made and has not reached a destination (item 201), a server whose access level does not let its central collect copies, a command read
 * whole on a phone, and a token's dialog that leaves out what no token holds. What the sandbox has no way to produce (a copy that is owed, a
 * central that collects) is the real answer with the field the server adds, put in by the route, so the console draws what it would draw.
 *
 * With `NOUST_CLOSING_SCREENS` set to a folder it also writes the screens for review, at 1440x900 and 390 wide, into it.
 */

import type { Page } from "@playwright/test";
import path from "node:path";

import { expect, expectNoA11yViolations, settle, signIn, startConsoleServer, test } from "./fixtures";
import type { RunningServer } from "./fixtures";

/** The copies a central collects are a hub's, and a hub with no server says it has none: that test has a hub with one node of its own. */
const hubTest = test.extend<object, { hub: RunningServer }>({
  hub: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const node = await startConsoleServer([]);
      // A central that manages a server refuses to while its own sign-in has no second factor.
      const hub = await startConsoleServer(["--totp", "--backup-codes", "300", "--central-role", "hub", "--fleet-node", `web-2=127.0.0.1:${new URL(node.url).port}`]);
      try {
        await use(hub);
      } finally {
        await Promise.all([hub.stop(), node.stop()]);
      }
    },
    { scope: "worker", timeout: 180_000 },
  ],
  baseURL: async ({ hub }, use) => {
    await use(hub.url);
  },
});

const OUT = process.env.NOUST_CLOSING_SCREENS ?? "";
const DESKTOP = { width: 1440, height: 900 };
const PHONE = { width: 390, height: 844 };

/** Takes the screen of the page as it is, at both widths, when the run is the one that writes them. */
async function shoot(page: Page, name: string, project: string): Promise<void> {
  if (process.env.NOUST_CLOSING_SCREENS === undefined) return;
  await page.setViewportSize(DESKTOP);
  await settle(page);
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(OUT, project, `${name}-1440.png`) });
  await page.setViewportSize(PHONE);
  await settle(page);
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(OUT, project, `${name}-390.png`) });
  await page.setViewportSize(DESKTOP);
}

const OWED = (destination: string, hours: number) => ({
  destination,
  reason: "ssh: connect to host backup.example.net port 22: Connection timed out",
  attempts: 2,
  since: new Date(Date.now() - 7_200_000).toISOString(),
  next_attempt_at: new Date(Date.now() + hours * 3_600_000).toISOString(),
  permanent: false,
});

test.describe("a copy waiting to be sent to a destination", () => {
  test("an application's backup says which destination it waits for, when it is tried again and what the destination said", async ({ page, consoleServer }, testInfo) => {
    await page.setViewportSize(DESKTOP);
    // The real list, with one backup of the seeded application owed to its destination.
    await page.route(/\/api\/backups(\?.*)?$/, async (route) => {
      const response = await route.fetch();
      const body = (await response.json()) as { backups: { domain: string; pending_sends?: unknown[] }[] };
      const first = body.backups.find((backup) => backup.domain === "shop.example.net");
      if (first !== undefined) first.pending_sends = [OWED("offsite-sftp", 1)];
      await route.fulfill({ response, json: body });
    });
    await signIn(page, consoleServer, "/backups?domain=shop.example.net");
    const drawer = page.getByRole("dialog", { name: "Backups of shop.example.net" });
    await expect(drawer).toBeVisible();
    await expect(drawer.getByText("Pending to offsite-sftp")).toBeVisible();
    // The backup that is everywhere says so with a dash and its reason, not with a label and nothing after it.
    await expect(drawer.getByText("Nothing waits to be sent").first()).toBeAttached();
    await expect(drawer.getByText(/Tries again in/)).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "a backup waiting to be sent");
    await drawer.getByRole("button", { name: "Why it has not been sent yet" }).click();
    await expect(page.getByRole("dialog", { name: "Waiting to be sent" }).getByText(/Connection timed out/)).toBeVisible();
    await shoot(page, "copia-pendiente", testInfo.project.name);
  });
});

hubTest.describe("a server that does not let its central collect its copies", () => {
  hubTest("is a warning in its own row, with its fix, and the others go on being collected", async ({ page, hub }, testInfo) => {
    await page.setViewportSize(DESKTOP);
    const node = (name: string, extra: Record<string, unknown> = {}) => ({
      node: name,
      follow: null,
      following: true,
      last_pass_at: new Date(Date.now() - 600_000).toISOString(),
      last_ok_at: new Date(Date.now() - 600_000).toISOString(),
      last_error: null,
      last_error_code: null,
      failures: 0,
      support: { mode: "full" },
      held: { count: 2, bytes: 4_096 },
      newest: new Date(Date.now() - 3_600_000).toISOString(),
      moving: null,
      ...extra,
    });
    await page.route(/\/api\/central-backups\/status$/, async (route) => {
      await route.fulfill({
        json: {
          following: { following: 2, of: 2, exceptions: [] },
          nodes: [node("web-2"), node("db-1", { last_error: "db-1 does not let this central collect its copies", last_error_code: "ceiling", failures: 3, held: { count: 0, bytes: 0 } })],
          settings: { enabled: true, kinds: ["app", "database", "volume", "noust"], rate_limit_kbps: null, poll_seconds: 60, pass_hours: 6, retention_count: 30, retention_days: null, retention_gb: null, min_free_gb: 1 },
          space: { total: 500e9, free: 120e9, used: 30e9, min_free: 1e9, available: 119e9, low: false },
          directory: "/data/backups/.collected",
        },
      });
    });
    await page.route(/\/api\/central-backups(\?.*)?$/, async (route) => {
      await route.fulfill({ json: { items: [], total: 0 } });
    });
    await signIn(page, hub);
    await page.goto("/fleet/central-backups");
    const servers = page.getByRole("region", { name: "How collecting is going with each server" });
    const refusing = servers.getByRole("row").filter({ hasText: "db-1" });
    await expect(refusing.getByText("Its access level does not allow it")).toBeVisible();
    await expect(refusing.getByText(/noust fleet access/)).toBeVisible();
    await expect(refusing.locator("[data-state='warning']")).toHaveCount(1);
    await expect(refusing.locator("[data-state='failed']")).toHaveCount(0);
    await expect(servers.getByRole("row").filter({ hasText: "web-2" }).getByText("Up to date")).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "a server whose access level does not let its central collect");
    await shoot(page, "recoger-techo", testInfo.project.name);
  });
});

test.describe("the sign-in of a central that had no installer", () => {
  test("reads the command that issues a token whole on a phone, and says no installer", async ({ page }, testInfo) => {
    await page.route(/\/api\/auth\/session$/, async (route) => {
      const response = await route.fetch();
      const body = (await response.json()) as Record<string, unknown>;
      await route.fulfill({ response, json: { ...body, accounts_exist: false, first_start: { token_shown_by: "container_log" } } });
    });
    await page.setViewportSize(PHONE);
    await page.goto("/login?with=token");
    const command = page.getByText("docker exec -it <container> noust web token --new");
    await expect(command).toBeVisible();
    // Read whole: the line breaks inside the box and nothing of it is cut off by the edge of the screen.
    const box = await command.boundingBox();
    expect(box).not.toBeNull();
    expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(PHONE.width);
    expect(await command.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await settle(page);
    await expectNoA11yViolations(page, "the long command on a phone");
    if (process.env.NOUST_CLOSING_SCREENS !== undefined) await page.screenshot({ path: path.join(OUT, testInfo.project.name, "orden-larga-390.png") });
  });

  test("does not name where the token came from once there are accounts: it is this server's, with the command to issue another", async ({ page }) => {
    // The sandbox has no account until a test makes one: what a server with accounts says to a caller who is not signed in is put in by the route.
    await page.route(/\/api\/auth\/session$/, async (route) => {
      const response = await route.fetch();
      const body = (await response.json()) as Record<string, unknown>;
      await route.fulfill({ response, json: { ...body, accounts_exist: true, first_start: null } });
    });
    await page.goto("/login");
    await expect(page.getByText(/No account yet\? Ask whoever runs this server for one\. If you run it and cannot get in, sign in with its access token/)).toBeVisible();
    await expect(page.getByText(/create your own account/i)).toHaveCount(0);
    await expect(page.getByText(/installer printed/)).toHaveCount(0);
    await expect(page.getByText("noust web token --new")).toBeVisible();
  });
});

test.describe("a token never holds what is for people", () => {
  test("the dialog that makes one leaves it out of what the token will be able to do, as the server says", async ({ page, consoleServer }, testInfo) => {
    await page.setViewportSize(DESKTOP);
    await signIn(page, consoleServer, "/settings/tokens");
    const roles = (await (await page.request.get("/api/auth/roles")).json()) as { token_never?: string[]; roles: { admin: string[] } };
    // The real server says it, and a role holds it all the same: the administrator uses the assistant, the token an administrator makes does not.
    expect(roles.token_never).toContain("assistant.use");
    expect(roles.roles.admin).toContain("assistant.use");
    await page.getByRole("button", { name: "Create token" }).first().click();
    const dialog = page.getByRole("dialog", { name: /token/i }).first();
    await expect(dialog).toBeVisible();
    await dialog.getByRole("radio", { name: /^Admin/ }).click();
    await expect(dialog.getByText("apps.manage").first()).toBeVisible();
    await expect(dialog.getByText("assistant.use", { exact: true })).toHaveCount(0);
    await expect(dialog.getByText(/a token never holds assistant\.use/)).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "the token dialog without what no token holds");
    await shoot(page, "token-sin-asistente", testInfo.project.name);
  });
});
