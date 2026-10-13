/**
 * Roles, single sign-on, tokens bound to a project, MCP, the manifest preview and the API
 * reference against the real backend (checklist I-03, I-07, I-23, I-34, J-02, J-15, J-19).
 *
 * One server of this file's own, because the flows need accounts and the other specs count them:
 * two accounts are made over the API, then the pages are walked in order. Sudo mode is confirmed
 * once per test over the API, so the pages themselves are exercised without the confirmation
 * dialog (confirmItsYou has its own specs).
 */

import type { Page } from "@playwright/test";

import { csrf } from "./apps-cleanup";
import { expect, expectNoA11yViolations, settle, signIn, startConsoleServer, test } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

const serial = test.extend<object, { consoleServer: ConsoleServer }>({
  consoleServer: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const server = await startConsoleServer(["--totp", "--backup-codes", "120"]);
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

const PASSWORD = "correct-horse-battery-staple-1";

/** Confirms sudo mode for this browser over the API, with a second factor nothing has spent. */
async function elevate(page: Page, server: ConsoleServer): Promise<void> {
  const answer = await page.request.post("/api/auth/elevate", { data: { code: server.secondFactor() }, headers: await csrf(page) });
  expect(answer.ok()).toBe(true);
}

serial("two accounts to hold roles", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer);
  await elevate(page, consoleServer);
  for (const [username, role] of [
    ["ana", "admin"],
    ["bruno", "operator"],
  ] as const) {
    const made = await page.request.post("/api/auth/accounts", { data: { username, role, password: PASSWORD }, headers: await csrf(page) });
    expect(made.ok(), await made.text()).toBe(true);
  }
});

serial("a role is defined from the permissions that exist, given on a project, taken back and deleted", async ({ page, consoleServer, problems }) => {
  // Deleting a role someone still holds is refused on purpose.
  problems.expect(/status of 409 .*\/api\/access\/roles\/release-manager$/);
  await signIn(page, consoleServer, "/settings/access");
  await elevate(page, consoleServer);
  await expect(page.getByRole("heading", { name: "Who holds what" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "No role is granted yet" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Settings, Roles and access, empty");

  // A role of your own, from the permissions the server lists, each with its own sentence.
  await page.getByRole("button", { name: "Define a role" }).click();
  const drawer = page.getByRole("dialog", { name: "Define a role" });
  await drawer.getByLabel("Name", { exact: true }).fill("release-manager");
  await drawer.getByLabel("Label").fill("Release manager");
  await drawer.getByRole("checkbox", { name: /apps\.read/ }).check();
  await drawer.getByRole("checkbox", { name: /apps\.deploy/ }).check();
  await expect(drawer.getByText(/^It can be held on: Environment, Project, Team, Server$/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the role drawer");
  await drawer.getByRole("button", { name: "Define role" }).click();
  const roles = page.getByRole("region", { name: "Role definitions" });
  await expect(roles.getByText("Release manager")).toBeVisible();

  // Given where it is needed: the production environment of the default project.
  await page.getByRole("button", { name: "Grant a role" }).click();
  const dialog = page.getByRole("dialog", { name: "Grant a role" });
  await dialog.getByRole("combobox", { name: "Account" }).click();
  await page.getByRole("option", { name: "bruno" }).click();
  await dialog.getByRole("combobox", { name: "Where it applies" }).click();
  await page.getByRole("option", { name: "An environment" }).click();
  await dialog.getByRole("combobox", { name: "Team" }).click();
  await page.getByRole("option", { name: "Default", exact: true }).click();
  await dialog.getByRole("combobox", { name: "Project" }).click();
  await page.getByRole("option", { name: "Default", exact: true }).click();
  await dialog.getByRole("combobox", { name: "Environment" }).click();
  await page.getByRole("option", { name: "Production", exact: true }).click();
  await dialog.getByRole("combobox", { name: "Role" }).click();
  await page.getByRole("option", { name: "Release manager" }).click();
  await dialog.getByRole("button", { name: "Grant role" }).click();
  const grants = page.getByRole("region", { name: "Roles granted to accounts" });
  await expect(grants.getByText("bruno")).toBeVisible();
  await expect(grants.getByText("Release manager")).toBeVisible();
  await expect(grants.getByText("Environment", { exact: true })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Settings, Roles and access, with a grant");

  // A role someone holds cannot be deleted, and the server says so; taken back, it can.
  await page.getByRole("button", { name: "Delete Release manager" }).click();
  const refusal = page.getByRole("alertdialog", { name: "Delete Release manager" });
  await refusal.getByRole("button", { name: "Delete role" }).click();
  await expect(refusal.getByText(/is held by 1 binding/)).toBeVisible();
  await refusal.getByRole("button", { name: "Cancel" }).click();
  await page.getByRole("button", { name: "Revoke Release manager from bruno" }).click();
  await page.getByRole("alertdialog", { name: /^Revoke Release manager from bruno/ }).getByRole("button", { name: "Revoke role" }).click();
  await expect(page.getByRole("heading", { name: "No role is granted yet" })).toBeVisible();
  await page.getByRole("button", { name: "Delete Release manager" }).click();
  await page.getByRole("alertdialog", { name: "Delete Release manager" }).getByRole("button", { name: "Delete role" }).click();
  await expect(roles.getByText("Release manager")).toBeHidden();
});

serial("a token can be bound to a project, and the access token, which belongs to nobody, cannot issue one", async ({ page, consoleServer, problems }) => {
  // A bound token belongs to an account; the break-glass token is no account's, and is refused.
  problems.expect(/status of 400 .*\/api\/auth\/tokens$/);
  await signIn(page, consoleServer, "/settings/tokens");
  await elevate(page, consoleServer);
  await page.getByRole("button", { name: "Create token" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Create an API token" });
  await dialog.getByLabel("Name", { exact: true }).fill("project-ci");
  await dialog.getByRole("radio", { name: /One project/ }).check();
  // Until it names a project, the token cannot be made.
  await expect(dialog.getByRole("button", { name: "Create token" })).toBeDisabled();
  await dialog.getByRole("combobox", { name: "Team" }).click();
  await page.getByRole("option", { name: "Default", exact: true }).click();
  await dialog.getByRole("combobox", { name: "Project" }).click();
  await page.getByRole("option", { name: "Default", exact: true }).click();
  await expect(dialog.getByText(/sees that and nothing else of the server/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the token dialog, bound to a project");
  await dialog.getByRole("button", { name: "Create token" }).click();
  await expect(dialog.getByText(/belongs to an account/)).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel" }).click();
});

serial("passwords are turned off for single sign-on, keeping the way back, and on again", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/settings/sso");
  await elevate(page, consoleServer);
  await expect(page.getByRole("heading", { name: "No identity provider yet" })).toBeVisible();
  // With no provider, turning passwords off would lock everybody out: it is not offered.
  await expect(page.getByRole("button", { name: "Turn off…" })).toBeDisabled();
  await settle(page);
  await expectNoA11yViolations(page, "Settings, Single sign-on, empty");

  await page.getByRole("button", { name: "Add a provider" }).first().click();
  const drawer = page.getByRole("dialog", { name: "Add an identity provider" });
  await drawer.getByLabel("Name", { exact: true }).fill("corp");
  await drawer.getByLabel("Button label").fill("Acme SSO");
  await drawer.getByLabel("Issuer").fill("https://sso.acme.test/realms/acme");
  await drawer.getByLabel("Client ID").fill("noust");
  await drawer.getByLabel("Client secret").fill("a-secret-that-is-never-shown-again");
  await settle(page);
  await expectNoA11yViolations(page, "the provider drawer");
  await drawer.getByRole("button", { name: "Add provider" }).click();
  const providers = page.getByRole("region", { name: "Configured providers" });
  await expect(providers.getByText("Acme SSO")).toBeVisible();
  // The secret is kept on the server and never comes back.
  await expect(page.getByText("a-secret-that-is-never-shown-again")).toBeHidden();

  await page.getByRole("button", { name: "Turn off…" }).click();
  const off = page.getByRole("dialog", { name: "Turn off passwords and passkeys" });
  await off.getByRole("checkbox", { name: /ana/ }).check();
  await off.getByRole("button", { name: "Turn off passwords and passkeys" }).click();
  await expect(page.getByText("Only the identity provider signs accounts in").first()).toBeVisible();
  const keepers = page.getByRole("group", { name: "Accounts that keep their password" });
  await expect(keepers.getByText("ana")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Settings, Single sign-on, passwords off");

  // The sign-in page leads with the provider and keeps the account form behind a link.
  const anonymous = await page.context().browser()?.newContext({ baseURL: consoleServer.url });
  if (anonymous === undefined) throw new Error("no browser");
  const visitor = await anonymous.newPage();
  await visitor.goto("/login");
  await expect(visitor.getByRole("link", { name: "Sign in with Acme SSO" })).toBeVisible();
  await expect(visitor.getByLabel("Username or email")).toBeHidden();
  await visitor.getByRole("button", { name: "Sign in with a password or a passkey" }).click();
  await expect(visitor.getByLabel("Username or email")).toBeVisible();
  await expect(visitor.getByRole("button", { name: "Emergency access" })).toBeVisible();
  await anonymous.close();

  await page.getByRole("button", { name: "Turn on" }).click();
  await expect(page.getByText("Passwords and passkeys sign accounts in").first()).toBeVisible();
  await providers.getByRole("button", { name: "Remove corp" }).click();
  await page.getByRole("alertdialog", { name: "Remove corp" }).getByRole("button", { name: "Remove provider" }).click();
  await expect(page.getByRole("heading", { name: "No identity provider yet" })).toBeVisible();
});

serial("the MCP endpoint is turned on, a group of tools switched off, and the tools listed by what they do", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/settings/mcp");
  await elevate(page, consoleServer);
  await expect(page.getByText("The MCP endpoint is off").first()).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Settings, MCP, off");
  await page.getByRole("button", { name: "Turn on" }).click();
  await expect(page.getByText("The MCP endpoint answers", { exact: true })).toBeVisible();
  await page.getByRole("switch", { name: "Control" }).click();
  const tools = page.getByRole("region", { name: "MCP tools" });
  await expect(tools.getByText("list_applications")).toBeVisible();
  await expect(tools.getByText("restart_application")).toBeHidden();
  await expect(tools.getByText("Asks twice").first()).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Settings, MCP, on");
  await page.getByRole("button", { name: "Turn off" }).click();
  await expect(page.getByText("The MCP endpoint is off").first()).toBeVisible();
  await page.getByRole("switch", { name: "Control" }).click();
});

serial("a manifest is previewed against the server and nothing is changed", async ({ page, consoleServer, problems }) => {
  // A document with an unknown key is refused on purpose.
  problems.expect(/status of 400 .*\/api\/manifest\/plan$/);
  await signIn(page, consoleServer, "/settings/manifest");
  await elevate(page, consoleServer);
  const before = (await (await page.request.get("/api/apps")).json()) as unknown;
  await page.getByLabel("Manifest", { exact: true }).fill("version: 1\napplications:\n  - domain: manifest-preview.example.org\n    source: https://github.com/acme/shop\n    branch: main\n");
  await page.getByRole("button", { name: "Preview changes" }).click();
  const table = page.getByRole("region", { name: "Changes a manifest would make" });
  await expect(table.getByText("Create application")).toBeVisible();
  await expect(table.getByText("manifest-preview.example.org", { exact: true })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Settings, Manifest, a preview");
  // A document with a key the schema does not know is refused with the server's own words.
  await page.getByLabel("Manifest", { exact: true }).fill("version: 1\napplications:\n  - domain: manifest-preview.example.org\n    memory_mb: 512\n");
  await page.getByRole("button", { name: "Preview changes" }).click();
  await expect(page.getByText(/memory_mb/).first()).toBeVisible();
  expect(await (await page.request.get("/api/apps")).json()).toEqual(before);
});

serial("every route of the API can be browsed, and a read tried with the session", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/settings/api");
  await expect(page.getByRole("region", { name: "API routes" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Settings, API reference");
  await page.getByRole("searchbox", { name: "Search routes" }).fill("/api/apps/{domain}/restart");
  await expect(page).toHaveURL(/q=/);
  await page.getByText("/api/apps/{domain}/restart").first().click();
  const drawer = page.getByRole("dialog", { name: "POST /api/apps/{domain}/restart" });
  await expect(drawer.getByText("apps.operate")).toBeVisible();
  await expect(drawer.getByText(/^curl -sS -X POST/)).toContainText("$NOUST_TOKEN");
  await expect(drawer.getByRole("button", { name: "Send request" })).toBeHidden();
  await settle(page);
  await expectNoA11yViolations(page, "an API route, a change");
  await page.keyboard.press("Escape");

  await page.getByRole("searchbox", { name: "Search routes" }).fill("/api/apps");
  await page.getByRole("cell", { name: "/api/apps", exact: true }).first().click();
  const read = page.getByRole("dialog", { name: "GET /api/apps" });
  await read.getByRole("button", { name: "Send request" }).click();
  await expect(read.getByText("Status 200")).toBeVisible();
  await expect(read.getByRole("region", { name: "What the server answered" }).or(read.getByText(/"domain"/).first())).toBeVisible();
});
