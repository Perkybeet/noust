/**
 * The four pages redrawn with the content kit (K1, 4.2): Cloud providers, Image registries, Deploy keys and
 * Integrations, against the real backend, in both themes and at 1920, 1440 and 390. Each says what it is by its
 * shapes (a slab with a head for each subject, the first time as a sentence and a button, tags on the line of their
 * name, the long rule behind a "?"), draws no command that repeats a button, and never lets the title's actions
 * reach the window's edge a screen away from the content they govern. A role that cannot change a page reads it and
 * sees one locked action and one line that says why.
 *
 * What the sandbox cannot have (a provider's accounts, a registry's apps) is fixed at the browser's edge, as the
 * other specs do; the seeded machine's own data (the boot scripts, the GitHub App, the pinned hosts) is the real one.
 * The pictures taken here are written under `NOUST_K1_SHOTS` when it is set, for a person to look at.
 */

import type { Page, Route } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";

import { expect, expectNoA11yViolations, settle, signIn, stillness, test } from "./fixtures";

const SHOTS = process.env.NOUST_K1_SHOTS;

const VIEWPORTS = [
  { name: "1440", width: 1440, height: 900 },
  { name: "390", width: 390, height: 844 },
] as const;

/** Writes the page as it is to NOUST_K1_SHOTS, when asked to: the project (the theme) and the width are in the name. */
async function shot(page: Page, name: string, width: string): Promise<void> {
  if (SHOTS === undefined) return;
  mkdirSync(SHOTS, { recursive: true });
  const project = test.info().project.name;
  await page.screenshot({ path: path.join(SHOTS, `${name}-${project}-${width}.png`), fullPage: false });
}

async function answer(page: Page, url: string, body: unknown, method = "GET"): Promise<void> {
  await page.route(url, async (route: Route) => {
    if (route.request().method() !== method) {
      await route.fallback();
      return;
    }
    await route.fulfill({ status: 200, json: body });
  });
}

/** The session of a role that reads and does not write: what the page offers is decided by what it holds. */
async function asReader(page: Page, role = "viewer"): Promise<void> {
  await page.route("**/api/auth/session", async (route) => {
    const response = await route.fetch();
    const session = (await response.json()) as { permissions?: string[]; role?: string | null };
    await route.fulfill({ response, json: { ...session, role, permissions: (session.permissions ?? []).filter((held) => !/\.(manage|host_access)$/.test(held)) } });
  });
}

/** The page does not scroll sideways: a table, a log or a tab strip may, inside itself. */
async function expectNoSidewaysScroll(page: Page): Promise<void> {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, "the page scrolls sideways").toBeLessThanOrEqual(0);
}

const ACCOUNT = {
  name: "hetzner-prod",
  provider: "hetzner",
  token_hint: "...wxyz",
  note: "Production project",
  created_at: "2026-10-01T10:00:00+00:00",
  created_by: "yago",
  verified_at: new Date(Date.now() - 2 * 3_600_000).toISOString(),
  last_used_at: null,
};

const READY = {
  node: "web-03",
  account: "hetzner-prod",
  provider: "hetzner",
  provider_id: "4711",
  address: "203.0.113.9",
  location: "nbg1",
  plan: "cx22",
  image: "ubuntu-24.04",
  state: "ready",
  error: null,
  job_id: null,
  created_at: "2026-10-10T10:00:00+00:00",
  created_by: "yago",
  updated_at: "2026-10-10T10:05:00+00:00",
};

const FAILED = { ...READY, node: "worker-01", provider_id: "4712", address: null, location: "fsn1", plan: "cx32", state: "failed", error: "server limit reached for location fsn1" };

async function cloudWithAccounts(page: Page): Promise<void> {
  await answer(page, "**/api/fleet/cloud/accounts", [ACCOUNT]);
  await answer(page, "**/api/fleet/cloud/servers", [READY, FAILED]);
}

for (const viewport of VIEWPORTS) {
  test.describe(`at ${viewport.name}`, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
    });

    test("Cloud providers, the first time: a sentence and its button, the page's action waiting and saying what for", async ({ page, consoleServer }) => {
      await signIn(page, consoleServer, "/settings/cloud");
      await expect(page.getByRole("heading", { level: 1, name: "Cloud providers" })).toBeVisible();
      const accounts = page.getByRole("region", { name: "Provider accounts", exact: true });
      await expect(accounts.getByText("Add the token of Hetzner Cloud, DigitalOcean or Vultr and you can create servers without leaving here.")).toBeVisible();
      await expect(accounts.getByRole("button", { name: "Add an account" })).toHaveAttribute("data-variant", "primary");
      // Nothing to create a server with yet: the header's action stays, waits, and its "?" says what for.
      await expect(page.getByRole("button", { name: "Create a server" })).toBeDisabled();
      await expect(page.getByRole("button", { name: "Why you cannot do this yet" })).toBeVisible();
      await expect(page.locator("button[data-variant=primary]")).toHaveCount(1);
      // The boot scripts that ship with Noust: "Built in" is a tag on the line of the name, and the rule is behind a "?".
      const scripts = page.getByRole("region", { name: "Boot scripts" });
      await expect(scripts.getByText("swap-2g")).toBeVisible();
      await expect(scripts.getByRole("button", { name: "About Boot scripts" })).toBeVisible();
      await expect(page.getByText(/Noust's own part of the document/)).toHaveCount(0);
      await expect(page.getByText("noust fleet cloud")).toHaveCount(0);
      await settle(page);
      await expectNoSidewaysScroll(page);
      await shot(page, "cloud-first-use", viewport.name);
      await expectNoA11yViolations(page, "Cloud providers, the first time");
    });

    test("Cloud providers with accounts: three slabs, what the provider said, and the failed machine's own way to delete it", async ({ page, consoleServer }) => {
      await cloudWithAccounts(page);
      await signIn(page, consoleServer, "/settings/cloud");
      for (const name of ["Provider accounts", "Servers created here", "Boot scripts"]) {
        await expect(page.getByRole("region", { name, exact: true }).getByRole("heading", { level: 2, name })).toBeVisible();
      }
      await expect(page.getByRole("button", { name: "Create a server" })).toBeEnabled();
      const said = page.getByRole("list", { name: "What the provider said about the servers that did not make it" });
      await expect(said).toContainText("worker-01: the provider said server limit reached for location fsn1");
      await expect(said).toContainText("It is billed until you delete it.");
      await expect(page.getByRole("button", { name: "Delete the machine of worker-01" })).toBeVisible();
      await settle(page);
      await expectNoSidewaysScroll(page);
      await shot(page, "cloud", viewport.name);
      await expectNoA11yViolations(page, "Cloud providers with accounts");
    });

    test("Cloud providers for a role that reads: no add, no row menu, the action locked, one line on top", async ({ page, consoleServer }) => {
      await cloudWithAccounts(page);
      await asReader(page);
      await signIn(page, consoleServer, "/settings/cloud");
      await expect(page.getByRole("region", { name: "Provider accounts", exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: "Add an account" })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "New script" })).toHaveCount(0);
      await expect(page.getByRole("button", { name: /^Actions for / })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Create a server" })).toBeDisabled();
      await expect(page.locator("[data-slot=read-only]")).toContainText("Your role, viewer, can see this page and not change it.");
      await settle(page);
      await shot(page, "cloud-reader", viewport.name);
      await expectNoA11yViolations(page, "Cloud providers, read only");
    });

    test("Image registries: the accounts as a slab with their apps as tags, what a registry answered under the table, the first time as a sentence", async ({ page, consoleServer }) => {
      const login = (name: string, extra: Record<string, unknown>) => ({
        name,
        kind: "ghcr",
        server: "ghcr.io",
        username: "example-bot",
        created_at: "2026-10-01T09:00:00+00:00",
        created_by: "root",
        updated_at: "2026-10-01T09:00:00+00:00",
        last_tested_at: new Date(Date.now() - 2 * 3_600_000).toISOString(),
        last_status: "ok",
        last_error: null,
        apps: [] as string[],
        ...extra,
      });
      await answer(page, "**/api/registries", {
        logins: [
          login("ghcr", { apps: ["shop.example.com", "api.example.com", "queue-worker"] }),
          login("registry", { server: "registry.example.com", kind: "custom", last_status: "failed", last_error: "denied: password expired", apps: ["panel.example.net"] }),
          login("hub", { kind: "dockerhub", server: "docker.io" }),
        ],
        kinds: ["dockerhub", "ghcr", "gitlab", "ecr", "acr", "gar", "custom"],
      });
      await signIn(page, consoleServer, "/settings/registries");
      await expect(page.getByRole("heading", { level: 1, name: "Image registries" })).toBeVisible();
      const slab = page.getByRole("region", { name: "Accounts" });
      await expect(slab.getByRole("button", { name: "About Accounts" })).toBeVisible();
      await expect(page.getByRole("button", { name: "Add an account" })).toHaveAttribute("data-variant", "primary");
      await expect(page.getByRole("list", { name: "What the registries answered" })).toContainText("registry: the registry answered denied: password expired");
      await expect(page.getByText("noust registry list")).toHaveCount(0);
      if (viewport.width >= 640) {
        // The row is one height, with three apps and with none: the tags are on the line of the name.
        const rows = slab.getByRole("row");
        const heights = await rows.evaluateAll((all) => all.slice(1).map((row) => Math.round(row.getBoundingClientRect().height)));
        expect(new Set(heights).size, `rows are one height, got ${heights.join(", ")}`).toBe(1);
        await slab.getByRole("button", { name: /more tags/ }).first().click();
        await expect(page.getByRole("dialog", { name: "Applications that use ghcr" })).toBeVisible();
        await shot(page, "registries-popover", viewport.name);
        await page.keyboard.press("Escape");
      }
      await settle(page);
      await expectNoSidewaysScroll(page);
      await shot(page, "registries", viewport.name);
      await expectNoA11yViolations(page, "Image registries");
    });

    test("Image registries, the first time: one sentence about when an account is needed, and the header's button", async ({ page, consoleServer }) => {
      await signIn(page, consoleServer, "/settings/registries");
      await expect(page.getByText("An account is only needed if an image, or the image it starts from, is private. Public ones are pulled without it.")).toBeVisible();
      await expect(page.getByRole("button", { name: "Add an account" })).toHaveCount(1);
      await settle(page);
      await expectNoSidewaysScroll(page);
      await shot(page, "registries-first-use", viewport.name);
      await expectNoA11yViolations(page, "Image registries, the first time");
    });

    test("Deploy keys: the keys, the code hosts this server trusts with the why behind a ?, and adding one before the first connection", async ({ page, consoleServer }) => {
      await signIn(page, consoleServer, "/settings/deploy-keys");
      await expect(page.getByRole("heading", { level: 1, name: "Deploy keys" })).toBeVisible();
      await expect(page.getByRole("heading", { level: 2, name: "Trusted code hosts" })).toBeVisible();
      await expect(page.getByRole("heading", { level: 2, name: "Add one before the first connection" })).toBeVisible();
      await expect(page.getByText("Pinned host keys")).toHaveCount(0);
      await expect(page.getByText(/pose as GitHub/)).toHaveCount(0);
      await page.getByRole("button", { name: "About Trusted code hosts" }).click();
      await expect(page.getByRole("dialog", { name: "Trusted code hosts" })).toContainText("So nobody can pose as GitHub or your Gitea between this server and the real one.");
      await shot(page, "deploy-keys-help", viewport.name);
      await page.keyboard.press("Escape");
      await settle(page);
      await expectNoSidewaysScroll(page);
      await shot(page, "deploy-keys", viewport.name);
      await expectNoA11yViolations(page, "Deploy keys");
    });

    test("Deploy keys for a role that reads: the keys and the hosts, no form and no forgetting, one line on top", async ({ page, consoleServer }) => {
      await asReader(page);
      await signIn(page, consoleServer, "/settings/deploy-keys");
      await expect(page.getByRole("heading", { level: 2, name: "Trusted code hosts" })).toBeVisible();
      await expect(page.getByRole("heading", { level: 2, name: "Add one before the first connection" })).toHaveCount(0);
      await expect(page.getByRole("button", { name: /^Forget / })).toHaveCount(0);
      await expect(page.locator("[data-slot=read-only]")).toContainText("server.host_access");
      await settle(page);
      await shot(page, "deploy-keys-reader", viewport.name);
      await expectNoA11yViolations(page, "Deploy keys, read only");
    });

    test("Integrations: four slabs, the address as a pair to copy, GitHub as a card beside where it is installed, GitLab as its first time", async ({ page, consoleServer }) => {
      await signIn(page, consoleServer, "/settings/integrations");
      await expect(page.getByRole("heading", { level: 1, name: "Integrations" })).toBeVisible();
      for (const name of ["Address for webhooks", "GitHub", "GitLab", "Gitea, Forgejo and Bitbucket"]) {
        await expect(page.getByRole("heading", { level: 2, name })).toBeVisible();
      }
      const address = page.getByRole("region", { name: "Address for webhooks" });
      await expect(address.getByRole("button", { name: "Copy address" })).toBeVisible();
      const github = page.getByRole("region", { name: "GitHub" });
      await expect(github.getByRole("article", { name: "noust-acme" })).toBeVisible();
      await expect(github.getByText("Connected", { exact: true })).toBeVisible();
      const gitlab = page.getByRole("region", { name: "GitLab" });
      await expect(gitlab.getByRole("button", { name: "Connect GitLab" })).toHaveAttribute("data-variant", "primary");
      await expect(page.getByText("The same, from a terminal")).toHaveCount(0);
      await expect(page.getByText("noust github status")).toHaveCount(0);
      await settle(page);
      await expectNoSidewaysScroll(page);
      await shot(page, "integrations", viewport.name);
      await expectNoA11yViolations(page, "Integrations");
    });

    test("Integrations for a role that reads: the App's menu and the install link are not drawn, GitLab's button is not, one line says why", async ({ page, consoleServer }) => {
      await asReader(page);
      await signIn(page, consoleServer, "/settings/integrations");
      await expect(page.getByRole("article", { name: "noust-acme" })).toBeVisible();
      await expect(page.getByRole("button", { name: "Actions for noust-acme" })).toHaveCount(0);
      await expect(page.getByRole("link", { name: /Install on another account/ })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Connect GitLab" })).toHaveCount(0);
      await expect(page.locator("[data-slot=read-only]")).toContainText("apps.manage");
      await settle(page);
      await shot(page, "integrations-reader", viewport.name);
      await expectNoA11yViolations(page, "Integrations, read only");
    });
  });
}

test("a settings page is as wide as its header: the title's actions end where the content does, at 1920", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1920, height: 1000 });
  await signIn(page, consoleServer, "/settings/integrations");
  await expect(page.getByRole("heading", { level: 1, name: "Integrations" })).toBeVisible();
  await settle(page);
  const edges = await page.evaluate(() => {
    const template = document.querySelector("[data-template=detail][data-measure=settings]");
    const header = template?.querySelector("[data-slot=header]");
    const content = template?.querySelector("[data-slot=content]");
    const main = document.querySelector("main");
    if (!template || !header || !content || !main) return null;
    return { header: header.getBoundingClientRect().right, content: content.getBoundingClientRect().right, main: main.getBoundingClientRect().right };
  });
  expect(edges, "the settings template, its header and its content").not.toBeNull();
  if (edges === null) return;
  // They end on the same edge, and that edge is the content's width and not the window's.
  expect(Math.abs(edges.header - edges.content)).toBeLessThanOrEqual(1);
  expect(edges.content).toBeLessThan(edges.main - 100);
  await shot(page, "integrations-wide", "1920");
});

test("the master switch of Notifications is on the title's line, not at the far end of the header", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1920, height: 1000 });
  await signIn(page, consoleServer, "/settings/notifications");
  const title = page.getByRole("heading", { level: 1 });
  await expect(title).toBeVisible();
  const master = page.getByRole("switch", { name: /Send notifications/ });
  await expect(master).toBeVisible();
  await stillness(page);
  const [titleBox, switchBox, contentBox] = await Promise.all([
    title.boundingBox(),
    master.boundingBox(),
    page.locator("[data-template=detail] > [data-slot=content]").boundingBox(),
  ]);
  if (!titleBox || !switchBox || !contentBox) throw new Error("no boxes");
  // On the line of the title (their middles are within a line of each other), after it, and inside the content's width.
  expect(Math.abs(titleBox.y + titleBox.height / 2 - (switchBox.y + switchBox.height / 2))).toBeLessThan(40);
  expect(switchBox.x).toBeGreaterThan(titleBox.x + titleBox.width - 1);
  expect(switchBox.x + switchBox.width).toBeLessThanOrEqual(contentBox.x + contentBox.width + 1);
  expect(switchBox.x - (titleBox.x + titleBox.width), "the switch is beside the title, not a window away").toBeLessThan(200);
  await shot(page, "notifications-master", "1920");
});
