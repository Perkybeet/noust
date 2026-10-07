/**
 * Teams, projects and environments in a browser, against the real backend (checklist B-77, I-01,
 * I-02, I-05, I-07 on the console):
 *
 * - a project and an environment are made in two clicks each (Coolify's costs six for an
 *   environment: its single-environment shortcut hides the list the button lives on), and the
 *   lists then say where each application lives and filter by it;
 * - an application moves to another project and environment from its row's menu, taking what
 *   belongs to it, and the project that holds it will not be deleted until it is empty;
 * - a second team brings the switcher, which narrows the lists, survives a reload and says in
 *   words which team the console is on;
 * - someone who only reads sees the organisation and no button to change it.
 *
 * The file has a server of its own: it makes and moves things the other specs count.
 */

import { request as playwrightRequest } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";

import { csrf } from "./apps-cleanup";
import { expect, expectNoA11yViolations, settle, signIn, startConsoleServer, stillness, test, totpCode } from "./fixtures";
import type { ConsoleServer } from "./fixtures";
import { signInSpanish, useSpanish } from "./spanish";

const serial = test.extend<object, { consoleServer: ConsoleServer }>({
  consoleServer: [
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
});
serial.describe.configure({ mode: "serial" });

declare global {
  interface Window {
    __noustClicks?: number;
  }
}

/** Counts every activation the page receives, from this document on. */
async function countClicks(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.__noustClicks = 0;
    document.addEventListener(
      "click",
      (event) => {
        if (event.isTrusted) window.__noustClicks = (window.__noustClicks ?? 0) + 1;
      },
      true,
    );
  });
}

async function startCounting(page: Page): Promise<void> {
  await page.evaluate(() => {
    window.__noustClicks = 0;
  });
}

async function clicks(page: Page): Promise<number> {
  return page.evaluate(() => window.__noustClicks ?? 0);
}

/** Sudo mode without a click, so "Confirm it's you" never joins a count that is not about it. */
async function elevate(page: Page, server: ConsoleServer): Promise<void> {
  const elevated = await page.request.post("/api/auth/elevate", { data: { code: server.secondFactor() }, headers: await csrf(page) });
  expect(elevated.ok(), await elevated.text()).toBe(true);
}

/** The access token's session over the API, in sudo mode. */
async function master(server: ConsoleServer): Promise<APIRequestContext> {
  const api = await playwrightRequest.newContext({ baseURL: server.url });
  expect((await api.post("/api/auth/login", { data: { token: server.token, totp_code: server.secondFactor() } })).ok()).toBe(true);
  const { cookies } = await api.storageState();
  const token = cookies.find((cookie) => cookie.name === "wasm_csrf");
  const headers: Record<string, string> = token ? { "X-WASM-CSRF": token.value } : {};
  expect((await api.post("/api/auth/elevate", { data: { code: server.secondFactor() }, headers })).ok()).toBe(true);
  return api;
}

async function headers(api: APIRequestContext): Promise<Record<string, string>> {
  const { cookies } = await api.storageState();
  const token = cookies.find((cookie) => cookie.name === "wasm_csrf");
  return token ? { "X-WASM-CSRF": token.value } : {};
}

const APP = "shop.example.net";
const OTHER_APP = "tienda.example.org";

/** The list's rows: a table on a desktop, found by the application's link. */
function row(page: Page, domain: string) {
  return page.getByRole("row").filter({ has: page.getByRole("link", { name: domain, exact: true }) });
}

serial("a project and an environment are made in two clicks each, and the lists then say where an application lives", async ({ page, consoleServer }) => {
  test.setTimeout(240_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await countClicks(page);
  await signIn(page, consoleServer, "/projects");
  await expect(page.getByRole("heading", { level: 1, name: "Projects" })).toBeVisible();
  await elevate(page, consoleServer);
  await settle(page);
  await expectNoA11yViolations(page, "the projects list, with one project");

  // A project: "New project", then its name. It opens with production ready.
  await startCounting(page);
  await page.getByRole("button", { name: "New project" }).click();
  const newProject = page.getByRole("dialog", { name: "New project" });
  await newProject.getByLabel("Project name").fill("Mobile app");
  await newProject.getByRole("button", { name: "Create project" }).click();
  await expect(page).toHaveURL(/\/projects\/default\/mobile-app$/);
  expect(await clicks(page), "a project takes two clicks").toBeLessThanOrEqual(2);
  await expect(page.getByRole("heading", { level: 1, name: "Mobile app" })).toBeVisible();
  await expect(page.getByRole("radio", { name: "Production" })).toBeChecked();
  await expect(page.getByText("Production is empty")).toBeVisible();

  // An environment of it: the same two clicks, on the page where the list of them is.
  await startCounting(page);
  await page.getByRole("button", { name: "New environment" }).click();
  const newEnvironment = page.getByRole("dialog", { name: "New environment in Mobile app" });
  await newEnvironment.getByLabel("Environment name").fill("Staging");
  await newEnvironment.getByRole("button", { name: "Create environment" }).click();
  await expect(page.getByRole("radio", { name: "Staging" })).toBeChecked();
  await expect(page).toHaveURL(/env=staging/);
  expect(await clicks(page), "an environment takes two clicks").toBeLessThanOrEqual(2);
  await settle(page);
  await expectNoA11yViolations(page, "a project with two environments");

  // With a second project there is somewhere to tell apart: the lists say where each row lives.
  await page.goto("/apps");
  await expect(row(page, APP)).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Project" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Environment" })).toBeVisible();
  await expect(row(page, APP)).toContainText("Default");
  await settle(page);
  await expectNoA11yViolations(page, "the applications, with a project and an environment column");

  // Moving it, from its row.
  await row(page, APP).getByRole("button", { name: `Actions for ${APP}` }).click();
  await page.getByRole("menuitem", { name: /Move to another project/ }).click();
  const move = page.getByRole("dialog", { name: `Move ${APP}` });
  await expect(move.getByRole("button", { name: "Move application" })).toBeDisabled();
  await move.getByRole("combobox", { name: "Project" }).click();
  await page.getByRole("option", { name: "Mobile app" }).click();
  await move.getByRole("combobox", { name: "Environment" }).click();
  await page.getByRole("option", { name: "Staging" }).click();
  await stillness(page);
  await expectNoA11yViolations(page, "the move dialog");
  await move.getByRole("button", { name: "Move application" }).click();
  await expect(move).toBeHidden();
  await expect(row(page, APP)).toContainText("Mobile app");
  await expect(row(page, APP)).toContainText("Staging");

  // Filtered by it, in the URL.
  await page.getByRole("combobox", { name: "Project" }).click();
  await page.getByRole("option", { name: "Mobile app" }).click();
  await expect(page).toHaveURL(/project=mobile-app/);
  await expect(row(page, APP)).toBeVisible();
  await expect(row(page, OTHER_APP)).toHaveCount(0);

  // The project page lists it under its environment; the environment will not be deleted while it holds it.
  await page.goto("/projects/default/mobile-app?env=staging");
  await expect(page.getByRole("region", { name: "Applications" }).getByRole("link", { name: APP })).toBeVisible();
  await page.getByRole("button", { name: "Actions for the environment Staging" }).click();
  const remove = page.getByRole("menuitem", { name: /Delete environment/ });
  await expect(remove).toHaveAttribute("aria-disabled", "true");
  await expect(remove).toHaveAccessibleDescription(/still holds \d+ resources?/);
  await page.keyboard.press("Escape");

  // Put back, then the environment and the project, which are empty now, are deleted.
  await page.goto("/apps");
  await row(page, APP).getByRole("button", { name: `Actions for ${APP}` }).click();
  await page.getByRole("menuitem", { name: /Move to another project/ }).click();
  const back = page.getByRole("dialog", { name: `Move ${APP}` });
  await back.getByRole("combobox", { name: "Project" }).click();
  await page.getByRole("option", { name: "Default" }).click();
  await back.getByRole("button", { name: "Move application" }).click();
  await expect(back).toBeHidden();
  await expect(row(page, APP)).toContainText("Production");

  await page.goto("/projects/default/mobile-app?env=staging");
  await page.getByRole("button", { name: "Actions for the environment Staging" }).click();
  await page.getByRole("menuitem", { name: /Delete environment/ }).click();
  await page.getByRole("alertdialog", { name: "Delete Staging" }).getByRole("button", { name: "Delete environment" }).click();
  await expect(page.getByRole("radio", { name: "Staging" })).toHaveCount(0);

  await page.goto("/projects");
  await page.getByRole("button", { name: "Actions for Mobile app" }).click();
  await page.getByRole("menuitem", { name: /Delete project/ }).click();
  await page.getByRole("alertdialog", { name: "Delete Mobile app" }).getByRole("button", { name: "Delete project" }).click();
  await expect(page.getByRole("link", { name: "Mobile app" })).toHaveCount(0);
});

serial("a second team brings the switcher, which narrows the lists and is remembered", async ({ page, consoleServer }) => {
  test.setTimeout(240_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await countClicks(page);
  await signIn(page, consoleServer, "/teams");
  await expect(page.getByRole("heading", { level: 1, name: "Teams" })).toBeVisible();
  await elevate(page, consoleServer);
  // One team called default: nothing to switch, and the page says a team is not a machine.
  await expect(page.getByTestId("team-switcher")).toHaveCount(0);
  await expect(page.getByText("A team is not a separate machine")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the teams list, with one team");

  await startCounting(page);
  await page.getByRole("button", { name: "New team" }).click();
  const dialog = page.getByRole("dialog", { name: "New team" });
  await dialog.getByLabel("Team name").fill("Globex");
  await dialog.getByRole("button", { name: "Create team" }).click();
  expect(await clicks(page), "a team takes two clicks").toBeLessThanOrEqual(2);
  // The next thing a new team needs is people.
  await expect(page).toHaveURL(/\/teams\/globex\/members$/);
  await expect(page.getByRole("heading", { level: 1, name: "Globex" })).toBeVisible();
  await expect(page.getByText("No one is in this team yet")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a team's members, empty");

  // The switcher is there now, on every page, and says every team is in view.
  const switcher = page.getByRole("button", { name: "Team: All teams" });
  await expect(switcher).toBeVisible();
  await page.goto("/apps");
  await expect(row(page, APP)).toBeVisible();
  await page.getByRole("button", { name: "Team: All teams" }).click();
  await stillness(page);
  await expectNoA11yViolations(page, "the team switcher, open");
  await page.getByRole("menuitemradio", { name: /Globex/ }).click();
  await expect(page.getByRole("button", { name: "Team: Globex" })).toBeVisible();
  // Globex holds nothing: the list is narrowed to it.
  await expect(row(page, APP)).toHaveCount(0);

  // Remembered across a reload, as a browser remembers it for the access token.
  await page.reload();
  await expect(page.getByRole("button", { name: "Team: Globex" })).toBeVisible();
  await expect(row(page, APP)).toHaveCount(0);

  await page.getByRole("button", { name: "Team: Globex" }).click();
  await page.getByRole("menuitemradio", { name: /All teams/ }).click();
  await expect(page.getByRole("button", { name: "Team: All teams" })).toBeVisible();
  await expect(row(page, APP)).toBeVisible();

  // Its settings: the short name is typed to delete it.
  await page.goto("/teams/globex/settings");
  await page.getByRole("button", { name: "Delete team" }).click();
  const confirm = page.getByRole("alertdialog", { name: "Delete Globex" });
  const button = confirm.getByRole("button", { name: "Delete team" });
  await expect(button).toBeDisabled();
  await confirm.getByRole("textbox").fill("globex");
  await button.click();
  await expect(page).toHaveURL(/\/teams$/);
  await expect(page.getByTestId("team-switcher")).toHaveCount(0);
});

/** An account that signs in with a password and a second factor, as accounts.spec makes them. */
async function createViewer(server: ConsoleServer, admin: APIRequestContext, username: string): Promise<{ password: string; secret: string; codes: string[] }> {
  const password = `quiet meadow viewer ${String(username.length * 7919)} lantern`;
  const created = await admin.post("/api/auth/accounts", { data: { username, role: "viewer", password, person_ref: `${username}@example.com` }, headers: await headers(admin) });
  expect(created.status(), await created.text()).toBe(201);
  const own = await playwrightRequest.newContext({ baseURL: server.url });
  expect((await own.post("/api/auth/login", { data: { username, password } })).ok()).toBe(true);
  const { cookies } = await own.storageState();
  const csrfToken = cookies.find((cookie) => cookie.name === "wasm_csrf");
  const own2fa: Record<string, string> = csrfToken ? { "X-WASM-CSRF": csrfToken.value } : {};
  const enrol = (await (await own.post("/api/auth/2fa/enroll", { headers: own2fa })).json()) as { secret: string };
  const confirmed = await own.post("/api/auth/2fa/confirm", { data: { code: totpCode(enrol.secret) }, headers: own2fa });
  expect(confirmed.ok(), await confirmed.text()).toBe(true);
  const { backup_codes: codes } = (await confirmed.json()) as { backup_codes: string[] };
  await own.dispose();
  return { password, secret: enrol.secret, codes: [...codes] };
}

serial("someone who only reads sees the organisation and no way to change it", async ({ page, consoleServer }) => {
  test.setTimeout(240_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  const admin = await master(consoleServer);
  const vera = await createViewer(consoleServer, admin, "vera");
  // A second project, so there is an organisation to read.
  const made = await admin.post("/api/teams/default/projects", { data: { name: "Web" }, headers: await headers(admin) });
  expect(made.status(), await made.text()).toBe(201);
  await admin.dispose();

  await page.goto("/login");
  await page.getByLabel("Username or email").fill("vera");
  await page.getByLabel("Password").fill(vera.password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  const code = page.getByLabel("Two-factor code");
  await expect(code).toBeFocused();
  await code.fill(vera.codes[0] ?? "");
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page).not.toHaveURL(/\/login/);

  await page.goto("/projects");
  await expect(page.getByRole("heading", { level: 1, name: "Projects" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Web", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "New project" })).toHaveCount(0);
  await page.getByRole("button", { name: "Actions for Web" }).click();
  await expect(page.getByRole("menuitem", { name: "Open" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: /Delete project/ })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await settle(page);
  await expectNoA11yViolations(page, "the projects, as a viewer reads them");

  await page.goto("/projects/default/web");
  await expect(page.getByRole("heading", { level: 1, name: "Web" })).toBeVisible();
  await expect(page.getByRole("link", { name: "New application" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "New environment" })).toHaveCount(0);

  await page.goto("/teams");
  await expect(page.getByRole("heading", { level: 1, name: "Teams" })).toBeVisible();
  await expect(page.getByRole("button", { name: "New team" })).toHaveCount(0);

  await page.goto("/apps");
  await expect(row(page, APP)).toBeVisible();
  await row(page, APP).getByRole("button", { name: `Actions for ${APP}` }).click();
  await expect(page.getByRole("menuitem", { name: "Open" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: /Move to another project/ })).toHaveCount(0);
});

serial("the pages of teams and projects read in Spanish", async ({ page, consoleServer }) => {
  test.setTimeout(240_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await useSpanish(page);
  await signInSpanish(page, consoleServer, "/projects");
  await expect(page.getByRole("heading", { level: 1, name: "Proyectos" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Nuevo proyecto" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "los proyectos");
  await page.goto("/teams");
  await expect(page.getByRole("heading", { level: 1, name: "Equipos" })).toBeVisible();
  await expect(page.getByText("Un equipo no es una máquina aparte")).toBeVisible();
});
