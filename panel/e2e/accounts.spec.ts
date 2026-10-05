/**
 * Accounts against the real backend, on a server of this file's own that starts with none:
 *
 * - the first accounts are created from the console after signing in with the access token,
 *   and the new administrator signs in (by the email of the person, which names one account),
 *   enrols an authenticator before anything else, and gets in with the backup codes shown once;
 * - a person with a second factor signs in in two steps: the password, then the code, or the
 *   passkey of that account;
 * - a security officer's role change becomes a request that a second officer approves from the
 *   inbox, and the first one runs it once approved - both people in their own browsers;
 * - passkeys, for real, through a CDP virtual authenticator on http://localhost: added behind
 *   "Confirm it's you", then used to sign in with no name or password, then to confirm it's you.
 */

import { request as playwrightRequest } from "@playwright/test";
import type { APIRequestContext, BrowserContext, CDPSession, Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, startConsoleServer, stillness, test, totpCode } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

const serial = test.extend<object, { consoleServer: ConsoleServer }>({
  consoleServer: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const server = await startConsoleServer(["--totp", "--backup-codes", "80"]);
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

/** An account the tests made, with what it signs in with. */
interface Person {
  username: string;
  password: string;
  secret: string;
  codes: string[];
}

const people = new Map<string, Person>();

function person(name: string): Person {
  const found = people.get(name);
  if (found === undefined) throw new Error(`${name} was not created`);
  return found;
}

/** A backup code nobody has spent: each signs in or confirms once. */
function spare(who: Person): string {
  const code = who.codes.shift();
  if (code === undefined) throw new Error(`${who.username} has no backup codes left`);
  return code;
}

async function csrf(context: APIRequestContext): Promise<Record<string, string>> {
  const { cookies } = await context.storageState();
  const token = cookies.find((cookie) => cookie.name === "wasm_csrf");
  return token ? { "X-WASM-CSRF": token.value } : {};
}

/** The access token's session over the API, in sudo mode. */
async function master(server: ConsoleServer): Promise<APIRequestContext> {
  const api = await playwrightRequest.newContext({ baseURL: server.url });
  expect((await api.post("/api/auth/login", { data: { token: server.token, totp_code: server.secondFactor() } })).ok()).toBe(true);
  expect((await api.post("/api/auth/elevate", { data: { code: server.secondFactor() }, headers: await csrf(api) })).ok()).toBe(true);
  return api;
}

/** Creates an account through the API and enrols its authenticator as the person would. */
async function createPerson(server: ConsoleServer, admin: APIRequestContext, username: string, role: string, personRef: string): Promise<Person> {
  const password = `quiet meadow ${role} ${String(username.length * 7919)} lantern`;
  const created = await admin.post("/api/auth/accounts", { data: { username, role, password, person_ref: personRef }, headers: await csrf(admin) });
  expect(created.status(), await created.text()).toBe(201);
  const own = await playwrightRequest.newContext({ baseURL: server.url });
  expect((await own.post("/api/auth/login", { data: { username, password } })).ok()).toBe(true);
  const enrol = (await (await own.post("/api/auth/2fa/enroll", { headers: await csrf(own) })).json()) as { secret: string };
  const confirmed = await own.post("/api/auth/2fa/confirm", { data: { code: totpCode(enrol.secret) }, headers: await csrf(own) });
  expect(confirmed.ok(), await confirmed.text()).toBe(true);
  const { backup_codes: codes } = (await confirmed.json()) as { backup_codes: string[] };
  await own.dispose();
  const made = { username, password, secret: enrol.secret, codes: [...codes] };
  people.set(username, made);
  return made;
}

/** The first step of the sign-in page: the name and the password. */
async function passwordStep(page: Page, who: Person, name = who.username): Promise<void> {
  await page.getByLabel("Username or email").fill(name);
  await page.getByLabel("Password").fill(who.password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByText(`Password accepted for ${name}.`)).toBeVisible();
}

/** Signs a person in through the sign-in page, with a backup code as the second factor. */
async function signInAs(page: Page, who: Person, next = "/"): Promise<void> {
  await page.goto(`/login?next=${encodeURIComponent(next)}`);
  await passwordStep(page, who);
  const code = page.getByLabel("Two-factor code");
  await expect(code).toBeFocused();
  await code.fill(spare(who));
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page).not.toHaveURL(/\/login/);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}

/** "Confirm it's you" for a person: one factor, a backup code (the session already proved the password). */
async function confirmAsPerson(page: Page, who: Person): Promise<void> {
  const dialog = page.getByRole("dialog", { name: "Confirm it's you" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Password")).toHaveCount(0);
  await dialog.getByLabel("Authentication code").fill(spare(who));
  await dialog.getByRole("button", { name: "Confirm" }).click();
  await expect(dialog).toBeHidden();
}

serial("the first accounts are made from the console, and the new administrator enrols before anything else", async ({ page, consoleServer, problems }) => {
  // Creating an account asks "Confirm it's you" by answering 403 first, by design.
  problems.expect(/status of 403 .* \/api\/auth\/accounts$/);
  await page.goto("/login?with=token");
  await page.getByLabel("Access token").fill(consoleServer.token);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByLabel("Two-factor code").fill(consoleServer.secondFactor());
  await page.getByRole("button", { name: "Verify" }).click();

  await expect(page.getByRole("heading", { level: 1, name: "Create the first account" })).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "the first account");
  // Which field signs in and which one names the person, said where each is typed.
  await expect(page.getByLabel(/^Username/)).toHaveAccessibleDescription(/^What you type to sign in/);
  await expect(page.getByLabel(/^Email/)).toHaveAccessibleDescription(/^Identifies the person/);
  await page.getByLabel(/^Username/).fill("ana");
  await page.getByLabel(/^Email/).fill("ana@example.com");
  await page.getByLabel(/^Password/).fill("violet harbor lantern 91");
  await page.getByRole("button", { name: "Create administrator" }).click();
  const confirm = page.getByRole("dialog", { name: "Confirm it's you" });
  await confirm.getByLabel("Authentication code").fill(consoleServer.secondFactor());
  await confirm.getByRole("button", { name: "Confirm" }).click();

  await expect(page.getByRole("heading", { level: 1, name: "Add a security officer" })).toBeVisible();
  await page.getByLabel(/^Username/).fill("sec");
  await page.getByLabel(/^Email/).fill("sec@example.com");
  await page.getByLabel(/^Password/).fill("amber quarry window 57");
  await page.getByRole("button", { name: "Create security officer" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Accounts created" })).toBeVisible();
  await page.getByRole("button", { name: "Sign in as ana" }).click();

  // The administrator's first sign-in, by the email of the person (it names one account): no
  // code is asked for a factor the account does not have yet; setting one up comes first.
  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByLabel(/Two-factor code/)).toHaveCount(0);
  await page.getByLabel("Username or email").fill("ana@example.com");
  await page.getByLabel("Password").fill("violet harbor lantern 91");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Set up a second factor" })).toBeVisible();
  await expect(page).toHaveURL(/\/welcome/);
  await stillness(page);
  await expectNoA11yViolations(page, "the second factor check");
  await page.getByRole("button", { name: "Use an authenticator app" }).click();
  const secret = ((await page.getByTestId("totp-secret").textContent()) ?? "").replace(/\s+/g, "");
  expect(secret).toMatch(/^[A-Z2-7]{16,}$/);
  await page.getByLabel("Authentication code").fill(totpCode(secret));
  await page.getByRole("button", { name: "Turn on" }).click();
  const codes = page.getByRole("list", { name: "Backup codes" }).getByRole("listitem");
  await expect(codes).toHaveCount(8);
  const saved = await codes.allTextContents();
  await page.getByRole("checkbox", { name: "I have saved these codes somewhere safe" }).click();
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
  people.set("ana", { username: "ana", password: "violet harbor lantern 91", secret, codes: saved.map((code) => code.trim()) });
  await settle(page);
});

serial("a role change waits for a second officer, who approves it in the inbox; the first one runs it", async ({ browser, page, consoleServer, problems }) => {
  problems.expect(/status of 403 .* \/api\/(auth\/accounts|approvals)\/\S+$/);
  problems.expect(/status of 202 .* \/api\/auth\/accounts\/viewer1$/);
  const admin = await master(consoleServer);
  await createPerson(consoleServer, admin, "gus", "security", "gus@example.com");
  await createPerson(consoleServer, admin, "sec1", "security", "sec1@example.com");
  await createPerson(consoleServer, admin, "viewer1", "viewer", "viewer1@example.com");
  expect((await admin.patch("/api/config", { data: { path: "approval.enabled", value: true }, headers: await csrf(admin) })).ok()).toBe(true);
  await admin.dispose();

  const requester = person("sec1");
  await signInAs(page, requester, "/settings/accounts");
  const row = page.getByRole("row").filter({ hasText: "viewer1" });
  await row.getByRole("button", { name: "Actions for viewer1" }).click();
  await page.getByRole("menuitem", { name: "Change role" }).click();
  const dialog = page.getByRole("dialog", { name: "Change the role of viewer1" });
  await dialog.getByRole("radio", { name: /^Operator/ }).check();
  await dialog.getByRole("button", { name: "Change role" }).click();
  await confirmAsPerson(page, requester);

  const waiting = page.getByRole("dialog", { name: "Waiting for approval" });
  await expect(waiting).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "a request waiting for approval");

  // The second officer, in a browser of their own.
  const other: BrowserContext = await browser.newContext({ baseURL: consoleServer.url });
  const decider = await other.newPage();
  const gus = person("gus");
  await signInAs(decider, gus, "/settings/approvals");
  // The inbox says what a request does in plain words; the call itself is in its drawer.
  const request = decider.getByRole("row").filter({ hasText: "Change the role of an account" }).filter({ hasText: "sec1" }).first();
  await request.getByRole("button", { name: /^Open request/ }).click();
  const drawer = decider.getByRole("dialog", { name: /^Request \d+$/ });
  await expect(drawer.getByText("PATCH /api/auth/accounts/viewer1", { exact: true })).toBeVisible();
  await expect(drawer.getByText(/"role": "operator"/)).toBeVisible();
  await stillness(decider);
  await expectNoA11yViolations(decider, "the request as the decider reads it");
  await drawer.getByLabel(/^Comment/).fill("Agreed with the team lead");
  await drawer.getByRole("button", { name: "Approve" }).click();
  await confirmAsPerson(decider, gus);
  await expect(decider.locator(".toast").filter({ hasText: /^Approved request \d+/ })).toBeVisible();
  await other.close();

  // Back with the requester: the dialog sees the decision and runs the call once.
  await expect(page.getByRole("dialog", { name: "Approved" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Agreed with the team lead")).toBeVisible();
  await page.getByRole("dialog", { name: "Approved" }).getByRole("button", { name: "Run it now" }).click();
  await expect(page.locator(".toast").filter({ hasText: "viewer1 is now Operator" })).toBeVisible();
  await expect(row).toContainText("Operator");
});

/** A platform authenticator with user verification, as a laptop's fingerprint reader. */
async function virtualAuthenticator(page: Page): Promise<CDPSession> {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  await cdp.send("WebAuthn.addVirtualAuthenticator", {
    options: {
      protocol: "ctap2",
      transport: "internal",
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
      automaticPresenceSimulation: true,
    },
  });
  return cdp;
}

serial("a passkey is added, signs in with no name or password, and confirms it's you", async ({ browser, consoleServer, browserName, problems }) => {
  test.skip(browserName !== "chromium", "the virtual authenticator is Chrome's DevTools protocol");
  problems.expect(/status of 403 .* \/api\/auth\/(passkeys\/registration\/options|2fa\/backup-codes)$/);
  // Passkeys need a name, never an address: the same server, reached as localhost.
  const local = consoleServer.url.replace("127.0.0.1", "localhost");
  const context = await browser.newContext({ baseURL: local });
  const page = await context.newPage();
  await virtualAuthenticator(page);
  const ana = person("ana");

  await signInAs(page, ana, "/settings/security");
  const passkeys = page.getByRole("region", { name: "Passkeys" });
  await expect(passkeys.getByText("No passkeys yet.")).toBeVisible();
  await passkeys.getByRole("button", { name: "Add a passkey" }).click();
  const add = page.getByRole("dialog", { name: "Add a passkey" });
  await add.getByLabel("Name").fill("Virtual laptop");
  await add.getByRole("button", { name: "Create passkey" }).click();
  await confirmAsPerson(page, ana);
  await expect(passkeys.getByRole("row").filter({ hasText: "Virtual laptop" })).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "security with a passkey");

  // Signed out, the passkey alone signs in again, from the button. The login page also offers
  // the passkey in the username field's autofill (conditional mediation), and Chrome's virtual
  // authenticator answers that request the moment it is made, where a person would have to
  // pick the passkey from the list. That raced this click: whenever the autofill answer won,
  // the page signed in on its own and navigated away under the pointer, and the click waited
  // out the test. So this page is a browser without autofill for passkeys, where the button is
  // the one way in, and the one this step proves.
  await page.addInitScript(() => {
    Object.defineProperty(PublicKeyCredential, "isConditionalMediationAvailable", { value: () => Promise.resolve(false) });
  });
  await context.clearCookies();
  await page.goto("/login?next=%2Fsettings%2Fsecurity");
  await page.getByRole("button", { name: "Sign in with a passkey" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible();
  await expect(passkeys.getByRole("row").filter({ hasText: "Virtual laptop" })).not.toContainText("Never");

  // And confirms it's you, offered first.
  await page.getByRole("button", { name: "New backup codes" }).click();
  await page.getByRole("dialog", { name: "Replace your backup codes?" }).getByRole("button", { name: "Replace backup codes" }).click();
  const confirm = page.getByRole("dialog", { name: "Confirm it's you" });
  await confirm.getByRole("button", { name: "Confirm with a passkey" }).click();
  await expect(page.getByRole("dialog", { name: "Save your backup codes" })).toBeVisible();

  // After the password, the second step offers this account's passkey as well as the code.
  await context.clearCookies();
  await page.goto("/login?next=%2Fsettings%2Fsecurity");
  await passwordStep(page, ana);
  await stillness(page);
  await expectNoA11yViolations(page, "the second step of a sign-in");
  await page.getByRole("button", { name: "Use your passkey" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible();
  await context.close();
});
