/**
 * Settings > Console access (checklist N-05): the console published on a name of its own over
 * TLS, and taken down again. The checks come first and are said by name (an account that signs in
 * with a password alone stops it), what Noust will do is shown before it does it, publishing and
 * unpublishing are jobs followed in the page, and nothing is written until "Confirm it's you".
 * Against the real backend and its sandboxed machine, where the console is a service whose unit
 * Noust rewrites and nginx is the sandbox's own.
 */

import { request as playwrightRequest } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";

import { confirmItsYou, expect, expectNoA11yViolations, settle, signIn, startConsoleServer, test } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

const NAME = "console.example.org";

test("the console is published on a name of its own, with an allow-list, and made private again", async ({ page, consoleServer, problems }) => {
  // Publishing is behind sudo mode: the first answer is the 403 that asks for it.
  problems.expect(/status of 403 .*\/api\/console\/exposure$/);
  await signIn(page, consoleServer, "/settings/console");
  await expect(page.getByRole("heading", { name: "Public name" })).toBeVisible();
  await expect(page.locator("[data-state]").getByText("Private", { exact: true })).toBeVisible();
  // What it checks first is on the page before anything is asked.
  await expect(page.getByText("A second factor on every account that can sign in")).toBeVisible();
  await expect(page.getByText("The ENS profile")).toBeVisible();
  await expect(page.getByText("A name that points at this server")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Settings, Console access, private");

  await page.getByRole("button", { name: "Publish the console…" }).click();
  const dialog = page.getByRole("dialog", { name: "Publish the console" });
  await dialog.getByRole("button", { name: "Preview" }).click();
  await expect(dialog.getByText("Enter the name the console is published at.")).toBeVisible();
  await dialog.getByLabel("Name", { exact: true }).fill(NAME);
  await dialog.getByLabel("Reachable from").fill("203.0.113.0/24, nowhere");
  await dialog.getByRole("button", { name: "Preview" }).click();
  await expect(dialog.getByText("Not an address or a network: nowhere.")).toBeVisible();
  await dialog.getByLabel("Reachable from").fill("203.0.113.0/24");
  // A server behind NAT sees only its private address: the check that the name resolves to it is skippable.
  await dialog.getByRole("button", { name: "More options" }).click();
  await dialog.getByRole("checkbox", { name: /^Do not require the name to resolve to this server/ }).check();
  await settle(page);
  await expectNoA11yViolations(page, "the publish dialog");
  await dialog.getByRole("button", { name: "Preview" }).click();

  // The server's own plan, before anything is written: no plain-HTTP form, and the allow-list is in it.
  await expect(dialog.getByText("What Noust will do")).toBeVisible();
  await expect(dialog.getByText(new RegExp(`write the nginx site of ${NAME.replace(/\./g, "\\.")}`))).toBeVisible();
  await expect(dialog.getByText(/obtain a Let's Encrypt certificate for .* and serve only HTTPS/)).toBeVisible();
  await expect(dialog.getByText(/203\.0\.113\.0\/24/).first()).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the publish plan");
  await dialog.getByRole("button", { name: "Publish the console" }).click();
  await confirmItsYou(page, consoleServer);

  // A job, followed in the dialog, ending on what was made and what to know.
  await expect(dialog.getByRole("region", { name: "Output of publishing the console" })).toBeVisible();
  await expect(dialog.getByText(`The console is published at ${NAME}`)).toBeVisible();
  await expect(dialog.getByText(/The console restarts in a few seconds/).first()).toBeVisible();
  await expect(dialog.getByRole("link", { name: `Open https://${NAME}` })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "publishing, finished");
  await dialog.getByRole("button", { name: "Done" }).click();

  await expect(page.locator("[data-state]").getByText("Published", { exact: true })).toBeVisible();
  await expect(page.getByText(`https://${NAME}`).first()).toBeVisible();
  await expect(page.getByText("203.0.113.0/24", { exact: true })).toBeVisible();
  await expect(page.getByText("Written by Noust: it is removed when the console is unpublished").first()).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Settings, Console access, published");

  // Unpublishing removes exactly what publishing made, and says so before it does.
  await page.getByRole("button", { name: "Unpublish the console" }).click();
  const confirm = page.getByRole("alertdialog", { name: "Unpublish the console" });
  await expect(confirm.getByText(/removes exactly what publishing made/)).toBeVisible();
  await confirm.getByRole("button", { name: "Unpublish" }).click();
  await expect(page.getByText("The console is private again").first()).toBeVisible();
  await expect(page.locator("[data-state]").getByText("Private", { exact: true })).toBeVisible();
});

/** The access token's session over the API, in sudo mode. */
async function master(server: ConsoleServer): Promise<APIRequestContext> {
  const api = await playwrightRequest.newContext({ baseURL: server.url });
  expect((await api.post("/api/auth/login", { data: { token: server.token, totp_code: server.secondFactor() } })).ok()).toBe(true);
  const { cookies } = await api.storageState();
  const csrf = cookies.find((cookie) => cookie.name === "noust_csrf")?.value ?? "";
  expect((await api.post("/api/auth/elevate", { data: { code: server.secondFactor() }, headers: { "X-Noust-CSRF": csrf } })).ok()).toBe(true);
  return api;
}

test("an account that signs in with a password alone is named, and the server's refusal is shown as it wrote it", async ({ page, problems }) => {
  problems.expect(/status of 4\d\d .*\/api\/console\/exposure\/preview$/);
  // A server of its own: an account changes how every other test signs in.
  const server = await startConsoleServer(["--totp", "--backup-codes", "40"]);
  try {
    const api = await master(server);
    const { cookies } = await api.storageState();
    const csrf = cookies.find((cookie) => cookie.name === "noust_csrf")?.value ?? "";
    const created = await api.post("/api/auth/accounts", {
      data: { username: "ana", role: "viewer", password: "quiet meadow viewer lantern 4242", person_ref: "ana@example.org" },
      headers: { "X-Noust-CSRF": csrf },
    });
    expect(created.status(), await created.text()).toBe(201);
    await api.dispose();

    await page.goto(`${server.url}/login?with=token&next=${encodeURIComponent("/settings/console")}`);
    await signIn(page, server);
    await expect(page.getByText("1 account signs in with a password alone")).toBeVisible();
    await expect(page.getByText("ana", { exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Open accounts" })).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "Console access, an account without a second factor");

    await page.getByRole("button", { name: "Publish the console…" }).click();
    const dialog = page.getByRole("dialog", { name: "Publish the console" });
    await dialog.getByLabel("Name", { exact: true }).fill(NAME);
    await dialog.getByRole("button", { name: "Preview" }).click();
    // The refusal is the server's own, naming the account, and nothing moves to the next step.
    await expect(dialog.getByText("The console cannot be published")).toBeVisible();
    await expect(dialog.getByText(/ana/).first()).toBeVisible();
    await expect(dialog.getByText("What Noust will do")).toHaveCount(0);
    await settle(page);
    await expectNoA11yViolations(page, "the publish dialog, refused");
  } finally {
    // Nothing on screen may keep asking a server that is going away.
    await page.goto("about:blank");
    await server.stop();
  }
});
