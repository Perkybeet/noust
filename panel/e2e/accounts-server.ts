/**
 * A console server that already has an account, for the specs about the sign-in of people who have one.
 *
 * The suite's own server has no account (the first administrator is made after signing in with the installer's
 * token), and a server without one opens the sign-in on the access token: it has nobody to ask for a name. The
 * account form, the doors under it ("Other ways to sign in") and what the page says to someone who arrives
 * without the installer's link are the sign-in of every other server, and are looked at on a server of their own:
 * an account changes how every other test of a worker signs in, so it cannot be made on the shared one.
 *
 * `withAccounts` is `test` with `consoleServer` (and so `baseURL`) pointing at such a server, the way the specs
 * that need a server of their own override it.
 */

import { request as playwrightRequest } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";

import { expect, startConsoleServer, test } from "./fixtures";
import type { ConsoleServer, RunningServer } from "./fixtures";

/** The owner the server is given: the first account of a server is its owner, whatever role it is asked to be. */
export const OWNER = { username: "first-owner", password: "quiet meadow owner lantern 1111" } as const;

/** The CSRF header of a session made over the API. */
async function csrf(context: APIRequestContext): Promise<Record<string, string>> {
  const { cookies } = await context.storageState();
  const token = cookies.find((cookie) => cookie.name === "noust_csrf");
  return token ? { "X-Noust-CSRF": token.value } : {};
}

/**
 * Starts a console server with two-factor sign-in, as the suite's is, and one account (`OWNER`), made through the
 * API by the access token in sudo mode, as an administrator does it.
 *
 * @returns The running server. Its owner has no authenticator of their own, so the access token and its second
 *   factor (`secondFactor()`) are still the way a test signs in.
 */
export async function startServerWithAccounts(): Promise<RunningServer> {
  const server = await startConsoleServer(["--totp", "--backup-codes", "200"]);
  const admin = await playwrightRequest.newContext({ baseURL: server.url });
  try {
    expect((await admin.post("/api/auth/login", { data: { token: server.token, totp_code: server.secondFactor() } })).ok()).toBe(true);
    expect((await admin.post("/api/auth/elevate", { data: { code: server.secondFactor() }, headers: await csrf(admin) })).ok()).toBe(true);
    const created = await admin.post("/api/auth/accounts", { data: { username: OWNER.username, role: "admin", password: OWNER.password }, headers: await csrf(admin) });
    expect(created.status(), await created.text()).toBe(201);
  } catch (error: unknown) {
    await server.stop();
    throw error;
  } finally {
    await admin.dispose();
  }
  return server;
}

/** `test` on a server that has an account: for what a person with an account sees where a new server shows the token. */
export const withAccounts = test.extend<object, { consoleServer: ConsoleServer }>({
  consoleServer: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const server = await startServerWithAccounts();
      try {
        await use(server);
      } finally {
        await server.stop();
      }
    },
    { scope: "worker", timeout: 120_000 },
  ],
});
