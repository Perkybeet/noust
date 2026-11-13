/**
 * The screens of the assistant, the approval policy and what a credential did, photographed at 1440 by 900 and at 390 px, in both
 * themes, for a person to look at (the lane's own review, beside the approved mockup). Tagged @screens: it is slow and writes files,
 * so the default run leaves it out; `NOUST_SHOTS` names the folder.
 *
 * One server with `--assistant on` and two accounts, the same way the panel's spec starts.
 */

import { request as playwrightRequest } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";

import { csrf } from "./apps-cleanup";
import { expect, settle, startConsoleServer, test, totpCode } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

const OUT = process.env.NOUST_SHOTS ?? path.join(import.meta.dirname, "..", "test-results", "assistant-screens");
const APP = "tienda.example.org";

/** The anti-forgery header of a request context's own session. */
async function csrfOf(context: APIRequestContext): Promise<Record<string, string>> {
  const { cookies } = await context.storageState();
  const token = cookies.find((cookie) => cookie.name === "noust_csrf");
  return token ? { "X-Noust-CSRF": token.value } : {};
}

const serial = test.extend<object, { consoleServer: ConsoleServer }>({
  consoleServer: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const server = await startConsoleServer(["--totp", "--backup-codes", "400", "--assistant", "on"]);
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

const SIZES = [
  { name: "1440", width: 1440, height: 900 },
  { name: "390", width: 390, height: 844 },
] as const;

/** The panel stands beside the page from 1792 px, goes over it below and rises from the foot on a phone: one picture of each. */
const PANEL_SIZES = [
  { name: "1920", width: 1920, height: 1080 },
  { name: "1440", width: 1440, height: 900 },
  { name: "390", width: 390, height: 844 },
] as const;

interface Person {
  username: string;
  password: string;
  codes: string[];
}
let marta: Person | null = null;

async function master(server: ConsoleServer): Promise<APIRequestContext> {
  const api = await playwrightRequest.newContext({ baseURL: server.url });
  expect((await api.post("/api/auth/login", { data: { token: server.token, totp_code: server.secondFactor() } })).ok()).toBe(true);
  expect((await api.post("/api/auth/elevate", { data: { code: server.secondFactor() }, headers: await csrfOf(api) })).ok()).toBe(true);
  return api;
}

async function createMarta(server: ConsoleServer): Promise<Person> {
  const admin = await master(server);
  const password = "quiet meadow admin 31676 lantern";
  expect((await admin.post("/api/auth/accounts", { data: { username: "marta", role: "admin", password }, headers: await csrfOf(admin) })).status()).toBe(201);
  await admin.dispose();
  const own = await playwrightRequest.newContext({ baseURL: server.url });
  expect((await own.post("/api/auth/login", { data: { username: "marta", password } })).ok()).toBe(true);
  const enrol = (await (await own.post("/api/auth/2fa/enroll", { headers: await csrfOf(own) })).json()) as { secret: string };
  const confirmed = await own.post("/api/auth/2fa/confirm", { data: { code: totpCode(enrol.secret) }, headers: await csrfOf(own) });
  const { backup_codes: codes } = (await confirmed.json()) as { backup_codes: string[] };
  await own.dispose();
  return { username: "marta", password, codes: [...codes] };
}

async function signInAs(page: Page, person: Person): Promise<void> {
  const first = await page.request.post("/api/auth/login", { data: { username: person.username, password: person.password } });
  const step = ((await first.json()) as { second_factor?: { challenge: string } }).second_factor;
  const code = person.codes.shift();
  if (step !== undefined && code !== undefined) await page.request.post("/api/auth/login/second-factor", { data: { challenge: step.challenge, code }, headers: await csrf(page) });
}

async function elevate(page: Page, person: Person): Promise<void> {
  const code = person.codes.shift();
  expect((await page.request.post("/api/auth/elevate", { data: { code }, headers: await csrf(page) })).ok()).toBe(true);
}

async function script(page: Page, server: ConsoleServer, replies: object[]): Promise<void> {
  const address = String(server.extras.assistant_model);
  expect((await page.request.post(`${address}/__script`, { data: { replies } })).ok()).toBe(true);
}

/** One picture of the page as it is, at both sizes, named after the screen and the theme of the project. */
async function shoot(page: Page, name: string, theme: string, sizes: readonly { name: string; width: number; height: number }[] = SIZES): Promise<void> {
  mkdirSync(OUT, { recursive: true });
  const before = page.viewportSize();
  for (const size of sizes) {
    await page.setViewportSize({ width: size.width, height: size.height });
    await settle(page);
    await page.screenshot({ path: path.join(OUT, `${name}-${size.name}-${theme}.png`) });
  }
  // The test goes on where it was: a phone's bar has no button for what the next step presses.
  if (before !== null) await page.setViewportSize(before);
}

serial.describe("@screens the assistant, the policy and the credentials", () => {
  serial("makes the person the pictures are taken as", async ({ consoleServer }) => {
    marta = await createMarta(consoleServer);
  });

  serial("Settings: the assistant on, what leaves, the policy and the tokens", async ({ page }, info) => {
    if (marta === null) throw new Error("no person");
    const theme = info.project.name;
    await signInAs(page, marta);
    await elevate(page, marta);
    for (const [name, route] of [
      ["50-encendido", "/settings/assistant"],
      ["53-datos", "/settings/assistant/data"],
      ["60-politica", "/settings/approval-policy"],
      ["61-politica-cambiadas", "/settings/approval-policy?view=changed"],
      ["66-aprobaciones", "/settings/approvals"],
      ["70-tokens", "/settings/tokens"],
    ] as const) {
      await page.goto(route);
      await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
      await shoot(page, name, theme);
    }
  });

  serial("the policy: a change, and what saving does", async ({ page }, info) => {
    if (marta === null) throw new Error("no person");
    const theme = info.project.name;
    await signInAs(page, marta);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/settings/approval-policy");
    await page.getByRole("button", { name: /Start, stop and restart.*The AI assistant, Other environments/ }).click();
    await page.getByRole("menuitem", { name: /Does it/ }).click();
    await shoot(page, "62-politica-celda", theme, [SIZES[0]]);
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page.getByRole("alertdialog", { name: "Save the approval policy" })).toBeVisible();
    await shoot(page, "63-politica-guardar", theme, [SIZES[0]]);
  });

  serial("the panel: explaining a failed deployment, and a proposal", async ({ page, consoleServer }, info) => {
    if (marta === null) throw new Error("no person");
    const theme = info.project.name;
    await signInAs(page, marta);
    await page.setViewportSize({ width: 1920, height: 1080 });
    const list = await page.request.get(`/api/deployments?domain=${APP}&limit=50`);
    const failed = ((await list.json()) as { items: { id: number; status: string; error: string | null }[] }).items.find((row) => row.status === "failed" && (row.error ?? "").includes("Type error"));
    if (failed === undefined) throw new Error("no failed deployment");
    await page.goto(`/apps/${APP}/deployments/${String(failed.id)}`);
    await shoot(page, "01-desp-fallido", theme, PANEL_SIZES);
    await script(page, consoleServer, [{ text: "The build failed because of **a type error**: `total` is not a field of `Order`.\n\n1. Open the file that uses it.\n2. Fix the name.\n3. Update again." }]);
    await page.getByRole("button", { name: "Explain this" }).click();
    await expect(page.getByRole("complementary", { name: "AI assistant" }).getByText("a type error")).toBeVisible();
    await shoot(page, "03-desp-explica-fin", theme, PANEL_SIZES);

    await page.goto(`/apps/${APP}`);
    await page.getByRole("button", { name: "AI assistant", exact: true }).click();
    const panel = page.getByRole("complementary", { name: "AI assistant" });
    await panel.getByRole("button", { name: "New conversation" }).click();
    await script(page, consoleServer, [{ calls: [{ name: "restart_application", arguments: { domain: APP } }] }, { text: "I propose to restart it." }]);
    await panel.getByLabel("Ask the assistant").fill(`Restart ${APP}`);
    await panel.getByRole("button", { name: "Send the question" }).click();
    await expect(panel.getByRole("article").first()).toBeVisible();
    await shoot(page, "20-prop-varias", theme, PANEL_SIZES);
  });
});
