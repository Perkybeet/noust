/**
 * The assistant's panel against the real backend and a scripted model (items 171, 183, 184 and 185): "Explain this" opens it on the page
 * that has the error, an answer is drawn without HTML whatever it says, a proposal is a card of the exact call that the person approves
 * with their own session, and with the assistant off or for a person who may not use it nothing of it is drawn. No provider is asked:
 * the model answers what each test dictates (tests/support/scripted_model.py).
 *
 * One server of this file's own, started with `--assistant on`. The assistant acts for a person, so the tests sign in as an account.
 */

import { request as playwrightRequest } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";

import { csrf } from "./apps-cleanup";
import { expect, expectNoA11yViolations, settle, startConsoleServer, test, totpCode } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

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
      const server = await startConsoleServer(["--totp", "--backup-codes", "200", "--assistant", "on"]);
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

const APP = "tienda.example.org";

interface Person {
  username: string;
  password: string;
  secret: string;
  codes: string[];
}

const people = new Map<string, Person>();

/** The access token's session over the API, in sudo mode. */
async function master(server: ConsoleServer): Promise<APIRequestContext> {
  const api = await playwrightRequest.newContext({ baseURL: server.url });
  expect((await api.post("/api/auth/login", { data: { token: server.token, totp_code: server.secondFactor() } })).ok()).toBe(true);
  expect((await api.post("/api/auth/elevate", { data: { code: server.secondFactor() }, headers: await csrfOf(api) })).ok()).toBe(true);
  return api;
}

/** Creates an account through the API and enrols its authenticator, as the person would. */
async function createPerson(server: ConsoleServer, username: string, role: string): Promise<Person> {
  const admin = await master(server);
  const password = `quiet meadow ${role} ${String(username.length * 7919)} lantern`;
  const created = await admin.post("/api/auth/accounts", { data: { username, role, password }, headers: await csrfOf(admin) });
  expect(created.status(), await created.text()).toBe(201);
  await admin.dispose();
  const own = await playwrightRequest.newContext({ baseURL: server.url });
  expect((await own.post("/api/auth/login", { data: { username, password } })).ok()).toBe(true);
  const enrol = (await (await own.post("/api/auth/2fa/enroll", { headers: await csrfOf(own) })).json()) as { secret: string };
  const confirmed = await own.post("/api/auth/2fa/confirm", { data: { code: totpCode(enrol.secret) }, headers: await csrfOf(own) });
  expect(confirmed.ok(), await confirmed.text()).toBe(true);
  const { backup_codes: codes } = (await confirmed.json()) as { backup_codes: string[] };
  await own.dispose();
  const person = { username, password, secret: enrol.secret, codes: [...codes] };
  people.set(username, person);
  return person;
}

function spare(person: Person): string {
  const code = person.codes.shift();
  if (code === undefined) throw new Error(`${person.username} has no backup code left`);
  return code;
}

/** Signs a person in over the API in this browser's own session, so the tests start where a person is already working. */
async function signInAs(page: Page, person: Person): Promise<void> {
  const first = await page.request.post("/api/auth/login", { data: { username: person.username, password: person.password } });
  expect(first.ok(), await first.text()).toBe(true);
  const step = ((await first.json()) as { second_factor?: { challenge: string } }).second_factor;
  if (step !== undefined) {
    const second = await page.request.post("/api/auth/login/second-factor", { data: { challenge: step.challenge, code: spare(person) }, headers: await csrf(page) });
    expect(second.ok(), await second.text()).toBe(true);
  }
}

/** What the scripted model answers to the next requests, dictated by the test. */
async function script(page: Page, server: ConsoleServer, replies: object[]): Promise<void> {
  const address = server.extras.assistant_model;
  if (typeof address !== "string") throw new Error("the server was not started with --assistant");
  const answer = await page.request.post(`${address}/__script`, { data: { replies } });
  expect(answer.ok()).toBe(true);
}

async function failedBuild(page: Page): Promise<number> {
  const response = await page.request.get(`/api/deployments?domain=${APP}&limit=50`);
  const row = ((await response.json()) as { items: { id: number; status: string; error: string | null }[] }).items.find((item) => item.status === "failed" && (item.error ?? "").includes("Type error"));
  if (!row) throw new Error(`no seeded failed build of ${APP}`);
  return row.id;
}

const HOSTILE = [
  "The build failed because of **a type error**.",
  "",
  'Try <img src=x onerror="alert(1)"> and <script>steal()</script>.',
  "",
  "![tracking pixel](https://tracker.example.net/p.gif)",
  "",
  "[https://noust.example/docs](https://evil.example.net/login) and [run me](javascript:alert(1)).",
].join("\n");

serial.describe("The assistant's panel", () => {
  // Wide enough for the panel to stand beside the page (from 1792 px); below it, it goes over the page.
  serial.use({ viewport: { width: 1920, height: 1080 } });

  serial("makes the people the tests sign in as", async ({ consoleServer }) => {
    await createPerson(consoleServer, "marta", "admin");
    await createPerson(consoleServer, "lucas", "viewer");
  });

  serial("is opened by 'Explain this' on a failed deployment and draws a hostile answer as text", async ({ page, consoleServer }) => {
    const marta = people.get("marta");
    if (marta === undefined) throw new Error("marta was not made");
    await signInAs(page, marta);
    await page.goto(`/apps/${APP}`);
    const id = await failedBuild(page);
    await page.goto(`/apps/${APP}/deployments/${String(id)}`);
    await expect(page.getByRole("button", { name: "AI assistant", exact: true })).toBeVisible();

    await script(page, consoleServer, [{ text: HOSTILE }]);
    await page.getByRole("button", { name: "Explain this" }).click();
    const panel = page.getByRole("complementary", { name: "AI assistant" });
    await expect(panel).toBeVisible();
    await expect(panel.getByText(/Reads this page:/)).toBeVisible();
    await expect(panel.getByText(`Deployment ${String(id)} of ${APP}`)).toBeVisible();

    const answer = panel.locator("[data-turn]").last();
    await expect(answer.getByText("a type error")).toBeVisible();
    // Whatever the model wrote, nothing of it is markup: the tag is on screen as the characters it is.
    await expect(answer).toContainText('<img src=x onerror="alert(1)">');
    expect(await panel.locator("img, script, iframe").count()).toBe(0);
    // A link shows where it goes, and a destination that is not a web address is no link.
    await expect(answer.getByRole("link", { name: /https:\/\/noust\.example\/docs/ })).toHaveAttribute("href", "https://evil.example.net/login");
    await expect(answer.getByText("(evil.example.net)")).toBeVisible();
    await expect(answer.getByRole("link", { name: /run me/ })).toHaveCount(0);
    await expect(answer.getByText("[image not shown: tracking pixel]")).toBeVisible();
    // It says it is an AI, with the model and where it runs.
    await expect(answer.getByText("tiny-model", { exact: true })).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "the panel with an answer");
  });

  serial("a proposal is a card of the exact call, approved with the person's own session", async ({ page, consoleServer }) => {
    const marta = people.get("marta");
    if (marta === undefined) throw new Error("marta was not made");
    await signInAs(page, marta);
    await page.goto(`/apps/${APP}`);
    await page.getByRole("button", { name: "AI assistant", exact: true }).click();
    const panel = page.getByRole("complementary", { name: "AI assistant" });
    await expect(panel).toBeVisible();
    await panel.getByRole("button", { name: "New conversation" }).click();

    await script(page, consoleServer, [{ calls: [{ name: "restart_application", arguments: { domain: APP } }] }, { text: "I propose to restart it." }]);
    await panel.getByLabel("Ask the assistant").fill(`Restart ${APP}`);
    await panel.getByRole("button", { name: "Send the question" }).click();

    const card = panel.getByRole("article").filter({ hasText: APP }).first();
    await expect(card).toBeVisible();
    await expect(card.getByText("Asks for your approval")).toBeVisible();
    await expect(card.getByRole("button", { name: "Approve" })).toBeVisible();
    await expect(panel.getByRole("button", { name: /approve all/i })).toHaveCount(0);
    await expectNoA11yViolations(page, "a proposal");

    await card.getByRole("button", { name: "Approve" }).click();
    await expect(card.getByText("Done.")).toBeVisible();
    await expect(card.getByText(/marta approved it at/)).toBeVisible();
  });

  serial("below 1792 px goes over the page as a dialog that Escape closes", async ({ page }) => {
    const marta = people.get("marta");
    if (marta === undefined) throw new Error("marta was not made");
    await page.setViewportSize({ width: 1440, height: 900 });
    await signInAs(page, marta);
    await page.goto(`/apps/${APP}`);
    await page.getByRole("button", { name: "AI assistant", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "AI assistant" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel("Ask the assistant")).toBeVisible();
    await expectNoA11yViolations(page, "the panel over the page");
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
  });

  serial("is not drawn for a person who may not use it", async ({ page, consoleServer }) => {
    const lucas = people.get("lucas");
    if (lucas === undefined) throw new Error("lucas was not made");
    await signInAs(page, lucas);
    await page.goto(`/apps/${APP}`);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.getByRole("button", { name: "AI assistant", exact: true })).toHaveCount(0);
    await expect(page.getByRole("complementary", { name: "AI assistant" })).toHaveCount(0);
    expect(consoleServer.url).not.toBe("");
  });
});
