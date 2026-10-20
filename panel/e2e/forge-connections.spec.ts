/**
 * How code and images reach the server, against the real backend: container registry accounts
 * (Settings, Integrations, and an application's Deploys), GitLab connections and the project
 * picker, applications read from Coolify or Dokploy, the workspaces of a monorepo, the build
 * cache and process types of Build commands, and the declaration file of Export. Runs in both
 * themes, with the CSP and console gates of the `problems` fixture.
 *
 * The registry accounts and the declaration are the real backend's. GitLab, Coolify, Dokploy and
 * a monorepo's repository are other people's servers and a network this sandbox does not have,
 * so their answers are fixed at the browser's edge (page.route): what the console sends them is
 * asserted, and nothing leaves the machine.
 */

import type { Page, Route } from "@playwright/test";

import { csrf } from "./apps-cleanup";
import { expect, expectNoA11yViolations, settle, signIn, stillness, test } from "./fixtures";
import type { ConsoleServer } from "./fixtures";
import { typedSource } from "./wizard-sources";

const DECLARED_APP = "lanzamiento.example.org";
const BUILD_APP = "shop.example.net";

/** A card of a settings page, by its title. */
function card(page: Page, title: string) {
  return page.locator("section").filter({ has: page.getByRole("heading", { name: title, exact: true }) }).last();
}

/** Sudo mode, through the API, so a dialog that writes a secret opens straight away. */
async function elevate(page: Page, server: ConsoleServer): Promise<void> {
  const response = await page.request.post("/api/auth/elevate", { data: { code: server.secondFactor() }, headers: await csrf(page) });
  expect(response.ok(), await response.text()).toBe(true);
}

/** Answers a call at the browser's edge with fixed JSON, recording what the console sent. */
async function answer(page: Page, url: string, method: string, body: unknown, sent: unknown[] = [], status = 200): Promise<void> {
  await page.route(url, async (route: Route) => {
    if (route.request().method() !== method) {
      await route.fallback();
      return;
    }
    sent.push(route.request().postDataJSON());
    await route.fulfill({ status, json: body });
  });
}

const GITLAB_CONNECTION = {
  name: "work",
  host: "gitlab.com",
  kind: "token",
  username: "yago",
  created_at: "2026-10-01T09:00:00+00:00",
  created_by: "root",
  updated_at: "2026-10-01T09:00:00+00:00",
  last_checked_at: "2026-10-02T09:00:00+00:00",
  last_status: "ok",
  last_error: null,
};

test("registry accounts are added, edited, listed and removed from Integrations", async ({ page, consoleServer }, testInfo) => {
  // One account per theme project: both may run against the same server.
  const name = `e2e-hub-${testInfo.project.name}`;
  await signIn(page, consoleServer, "/settings/integrations");
  await elevate(page, consoleServer);
  const section = page.getByRole("region", { name: "Container registries" });
  await expect(section).toBeVisible();
  await section.scrollIntoViewIfNeeded();
  try {
    await section.getByRole("button", { name: "Add an account" }).click();
    const dialog = page.getByRole("dialog", { name: "Add a registry account" });
    await dialog.getByLabel(/^Name/).fill(name);
    await dialog.getByLabel(/^Username/).fill("ci-bot");
    await dialog.getByLabel(/^Password or token/).fill("not-a-real-token");
    await stillness(page);
    await expectNoA11yViolations(page, "the registry account dialog");
    await dialog.getByRole("button", { name: "Add the account" }).click();

    const table = section.getByRole("table", { name: "Registry accounts" });
    const row = table.getByRole("row").filter({ hasText: name });
    await expect(row).toContainText("Docker Hub");
    await expect(row).toContainText("docker.io");
    await expect(row).toContainText("ci-bot");
    await expect(row).toContainText("Not tested");
    // The password is never read back, in the page or in the API.
    await expect(page.getByText("not-a-real-token")).toHaveCount(0);
    const listed = await (await page.request.get("/api/registries")).text();
    expect(listed).not.toContain("not-a-real-token");
    await settle(page);
    await expectNoA11yViolations(page, "the registry accounts");

    await row.getByRole("button", { name: `Actions for ${name}` }).click();
    await page.getByRole("menuitem", { name: "Edit" }).click();
    const edit = page.getByRole("dialog", { name: `Edit ${name}` });
    await edit.getByLabel(/^Username/).fill("ci-bot-2");
    await edit.getByRole("button", { name: "Save the changes" }).click();
    await expect(table.getByRole("row").filter({ hasText: name })).toContainText("ci-bot-2");

    await table.getByRole("row").filter({ hasText: name }).getByRole("button", { name: `Actions for ${name}` }).click();
    await page.getByRole("menuitem", { name: "Remove" }).click();
    const confirm = page.getByRole("alertdialog");
    await confirm.getByRole("button", { name: "Remove the account" }).click();
    await expect(table.getByRole("row").filter({ hasText: name })).toHaveCount(0);
  } finally {
    await page.request.delete(`/api/registries/${name}`, { headers: await csrf(page) });
  }
});

test("GitLab: connections with the host's own words, a token connection, and a sign-in that needs an application", async ({ page, consoleServer }) => {
  let connections: unknown[] = [GITLAB_CONNECTION, { ...GITLAB_CONNECTION, name: "old", host: "git.example.com", kind: "oauth", last_status: "failed", last_error: "401 Unauthorized: the token expired" }];
  const status = () => ({
    connections,
    oauth_apps: [{ host: "git.example.com", client_id: "abc123", created_at: "2026-10-01T09:00:00+00:00", created_by: "root" }],
    redirect_uri: "https://console.example.com/integrations/gitlab/callback",
  });
  await page.route("**/api/integrations/gitlab", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    await route.fulfill({ json: status() });
  });
  const connected: unknown[] = [];
  await page.route("**/api/integrations/gitlab/connections", async (route) => {
    const body = route.request().postDataJSON() as { name: string; host: string };
    connected.push(body);
    const made = { ...GITLAB_CONNECTION, name: body.name, host: body.host, username: "ci", last_checked_at: null, last_status: null };
    connections = [...connections, made];
    await route.fulfill({ status: 201, json: made });
  });

  await signIn(page, consoleServer, "/settings/integrations");
  await elevate(page, consoleServer);
  const section = page.getByRole("region", { name: "GitLab", exact: true });
  await expect(section).toBeVisible();
  await section.scrollIntoViewIfNeeded();
  const table = section.getByRole("table", { name: "GitLab connections" });
  await expect(table.getByRole("row").filter({ hasText: "work" })).toContainText("Works");
  await expect(table.getByRole("row").filter({ hasText: "old" })).toContainText("401 Unauthorized: the token expired");
  await settle(page);
  await expectNoA11yViolations(page, "the GitLab connections");

  await section.getByRole("button", { name: "Connect GitLab" }).click();
  const dialog = page.getByRole("dialog", { name: "Connect GitLab" });
  await dialog.getByLabel(/^Name/).fill("ci");
  await dialog.getByLabel(/^Access token/).fill("glpat-not-real");
  await stillness(page);
  await expectNoA11yViolations(page, "the GitLab token dialog");
  await dialog.getByRole("button", { name: "Connect" }).click();
  await expect(table.getByRole("row").filter({ hasText: "ci" }).first()).toBeVisible();
  expect(connected).toEqual([{ name: "ci", host: "gitlab.com", token: "glpat-not-real" }]);

  // A host with no OAuth application asks for it, and says where GitLab sends the browser back.
  await section.getByRole("button", { name: "Connect GitLab" }).click();
  const signIn2 = page.getByRole("dialog", { name: "Connect GitLab" });
  await signIn2.getByRole("radio", { name: "Sign in with GitLab" }).click();
  await expect(signIn2.getByText("https://console.example.com/integrations/gitlab/callback")).toBeVisible();
  await expect(signIn2.getByLabel(/^Application ID/)).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "the GitLab sign-in dialog");
});

test("an application's Export settings write its declaration file and check a pasted one before applying it", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${DECLARED_APP}/settings/export`);
  await elevate(page, consoleServer);
  const declaration = card(page, "Declaration file");
  await declaration.scrollIntoViewIfNeeded();
  await settle(page);
  await expectNoA11yViolations(page, "the declaration card");

  const download = page.waitForEvent("download");
  await declaration.getByRole("button", { name: "Download the declaration" }).click();
  const file = await download;
  expect(file.suggestedFilename()).toBe("noust.yaml");

  await declaration.getByRole("button", { name: "Apply a declaration…" }).click();
  const dialog = page.getByRole("dialog", { name: `Apply a declaration to ${DECLARED_APP}` });
  await expect(dialog.getByRole("button", { name: "Apply it" })).toBeDisabled();
  await dialog.getByLabel("Declaration (YAML)").fill("env:\n  E2E_NOTE: hello\n");
  await dialog.getByRole("button", { name: "Check what it changes" }).click();
  await expect(dialog.getByRole("heading", { name: "What it changes" })).toBeVisible();
  // Checking changes nothing: the variable is not there afterwards.
  const environment = await (await page.request.get(`/api/apps/${DECLARED_APP}/env`)).text();
  expect(environment).not.toContain("E2E_NOTE");
  await stillness(page);
  await expectNoA11yViolations(page, "the declaration dialog with a plan");
});

test("Build commands show the build cache, and process types only where the repository declares them", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${BUILD_APP}/settings/build`);
  const cache = card(page, "Build cache");
  await expect(cache).toBeVisible();
  await cache.scrollIntoViewIfNeeded();
  await expect(cache.getByText("Kept in")).toBeVisible();
  // An application that declares no process types has nothing to show under that title.
  await expect(card(page, "Process types")).toHaveCount(0);
  await settle(page);
  await expectNoA11yViolations(page, "the build cache");
});

test("the wizard creates an application from a GitLab project and its branch", async ({ page, consoleServer }) => {
  await page.route("**/api/integrations/gitlab", async (route) => {
    await route.fulfill({ json: { connections: [GITLAB_CONNECTION], oauth_apps: [], redirect_uri: null } });
  });
  await page.route("**/api/integrations/gitlab/connections/work/groups", async (route) => {
    await route.fulfill({ json: { items: [{ id: 1, full_path: "acme", name: "Acme" }], total: 1 } });
  });
  await page.route("**/api/integrations/gitlab/connections/work/projects", async (route) => {
    await route.fulfill({
      json: { items: [{ id: 12, path: "acme/storefront", default_branch: "production", clone_url: "https://gitlab.com/acme/storefront.git", web_url: "https://gitlab.com/acme/storefront" }], total: 1 },
    });
  });
  await page.route("**/api/integrations/gitlab/connections/work/projects/12/branches", async (route) => {
    await route.fulfill({
      json: { items: [{ name: "production", default: true, commit: "4f2a9c1" }, { name: "staging", default: false, commit: "9b1e22d" }], total: 2 },
    });
  });
  await signIn(page, consoleServer, "/apps/new");
  await page.getByRole("radio", { name: "From GitLab" }).click();
  await expect(page.getByRole("list", { name: "Projects" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the GitLab project list");

  await page.getByRole("button", { name: /acme\/storefront/ }).click();
  await expect(page.getByRole("button", { name: "Change project" })).toBeFocused();
  await expect(page.getByRole("combobox", { name: "Branch" })).toContainText("production");
  await stillness(page);
  await expectNoA11yViolations(page, "the GitLab project chosen");
});

test("the wizard reads an application from Coolify into the import review", async ({ page, consoleServer }) => {
  const listed: unknown[] = [];
  await answer(
    page,
    "**/api/import/applications",
    "POST",
    { platform: "coolify", applications: [{ ref: "u1", name: "storefront", build: "nixpacks", source: "https://github.com/acme/storefront", domains: ["shop.example.com"], project: "acme" }] },
    listed,
  );
  await answer(page, "**/api/import/proposal", "POST", {
    proposal: { platform: "coolify", files: [], warnings: ["Preview deployments have no equivalent here: use Previews in Noust."], domains: ["shop.example.com"], app_type: "nextjs" },
    document: { format: "noust-app", version: 1, secrets_included: false, app: { domain: "shop.example.com", app_type: "nextjs" }, env: {} },
    deployable: true,
  });
  await signIn(page, consoleServer, "/apps/new?start=import");
  await elevate(page, consoleServer);
  await page.getByRole("button", { name: "Read it from Coolify or Dokploy" }).click();
  await page.getByLabel(/^Address of the server/).fill("https://coolify.example.com");
  await page.getByLabel(/^API token/).fill("ro-token");
  await page.getByRole("button", { name: "List the applications" }).click();
  await expect(page.getByText(/1 found on https:\/\/coolify\.example\.com/)).toBeVisible();
  expect(listed).toEqual([{ platform: "coolify", url: "https://coolify.example.com", token: "ro-token" }]);
  await settle(page);
  await expectNoA11yViolations(page, "reading an application from Coolify");

  await page.getByRole("button", { name: "Read it", exact: true }).click();
  await expect(page.getByText("storefront was read")).toBeVisible();
  await expect(page.getByText("Preview deployments have no equivalent here: use Previews in Noust.")).toBeVisible();
  await expect(page.getByText(/An export of shop\.example\.com/)).toBeVisible();
});

test("the wizard deploys the workspaces of a monorepo, each on its own domain", async ({ page, consoleServer }) => {
  await answer(page, "**/api/workspaces/inspect", "POST", {
    tool: "turborepo",
    package_manager: "pnpm",
    workspaces: [
      { name: "web", directory: "apps/web", app_type: "nextjs", deployable: true, reason: "", depends_on: ["ui"], watch_paths: ["apps/web", "packages/ui"], build: null },
      { name: "ui", directory: "packages/ui", app_type: null, deployable: false, reason: "It has no start script, so it is a library", depends_on: [], watch_paths: [], build: null },
    ],
    problems: [],
  });
  await signIn(page, consoleServer, "/apps/new");
  const field = await typedSource(page);
  await field.fill("https://github.com/acme/monorepo.git");
  await page.getByRole("button", { name: "Deploy the workspaces of a monorepo" }).click();
  const dialog = page.getByRole("dialog", { name: "Deploy the workspaces of a monorepo" });
  await dialog.getByRole("button", { name: "Read the workspaces" }).click();
  await expect(dialog.getByText("2 workspaces, managed with turborepo and pnpm.")).toBeVisible();
  await expect(dialog.getByText(/It has no start script, so it is a library/)).toBeVisible();
  await dialog.getByRole("checkbox", { name: /^Deploy web/ }).check();
  await expect(dialog.getByLabel(/^Domain for web/)).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "the monorepo workspaces");
});
