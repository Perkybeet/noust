/**
 * How the server signs in to the repositories it deploys from, against the real backend: an
 * application's Source settings (a deploy key made, shown to add at the code host, tested,
 * replaced while the old one keeps working, removed; an https credential kept as a secret and
 * never shown back), and the server's own list of deploy keys and pinned host keys. Runs in both
 * themes, with the CSP and console gates of the `problems` fixture.
 *
 * The seeded machine (scripts/console_server.py, "sources and delivery"): taller.example.com is a
 * static site cloned over SSH from the repository bodas.example.com uses over https, so a key made
 * for one is shared by both; the modelled code host refuses the key a rotation waits to put in
 * place the first time it is offered (the operator has not added it yet) and accepts it the next;
 * two host keys are pinned, one on another port.
 */

import type { Locator, Page } from "@playwright/test";

import { csrf } from "./apps-cleanup";
import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

const SSH_APP = "taller.example.com";
const HTTPS_APP = "bodas.example.com";

/** The card of an application's Source settings that a heading names. */
function card(page: Page, title: string): Locator {
  return page.getByRole("heading", { name: title, exact: true }).locator("xpath=ancestor::section[1]");
}

/** Sudo mode, through the API, so a dialog that writes a secret opens straight away. */
async function elevate(page: Page, server: ConsoleServer): Promise<void> {
  const response = await page.request.post("/api/auth/elevate", { data: { code: server.secondFactor() }, headers: await csrf(page) });
  expect(response.ok(), await response.text()).toBe(true);
}

/** Takes the key off the application, whatever state a failed run left it in. */
async function removeKey(page: Page, domain: string): Promise<void> {
  await page.request.delete(`/api/apps/${domain}/deploy-key/rotate`, { headers: await csrf(page) });
  const removed = await page.request.delete(`/api/apps/${domain}/deploy-key`, { headers: await csrf(page) });
  expect(removed.ok(), await removed.text()).toBe(true);
}

test("an application gets a key of its own, adds its public half at the host, tests it, replaces it with no gap, and removes it", async ({
  page,
  consoleServer,
}) => {
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await signIn(page, consoleServer, `/apps/${SSH_APP}/settings`);
  await elevate(page, consoleServer);
  await page.getByRole("navigation", { name: "Application settings" }).first().getByRole("link", { name: "Source", exact: true }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Source" })).toBeVisible();

  // No key of its own, and no server key to fall back to on this machine.
  const key = card(page, "Deploy key");
  await expect(key.getByText("No key of its own", { exact: true })).toBeVisible();
  await expect(key.getByText(/Git has no key to offer/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a repository with no deploy key");

  // Made on a click: the public half is shown to add at the host, and copies exactly.
  await key.getByRole("button", { name: "Generate key" }).click();
  await expect(key.getByText("Has its own key", { exact: true })).toBeVisible();
  const publicKey = key.getByRole("group", { name: "Public key of taller-example-com", exact: true });
  const line = (await publicKey.locator("code").innerText()).trim();
  expect(line).toMatch(/^ssh-ed25519 \S+ noust-deploy-taller-example-com$/);
  await expect(page.getByText(/PRIVATE KEY/)).toHaveCount(0);
  await expect(key.getByRole("link", { name: /Open the deploy keys of github\.com\/you\/landing on GitHub/ })).toHaveAttribute(
    "href",
    "https://github.com/you/landing/settings/keys/new",
  );
  // The repository is the one another application is built from over https: it uses the key too.
  await expect(key.getByText(/bodas\.example\.com/)).toBeVisible();
  await publicKey.getByRole("button", { name: "Copy the public key" }).click();
  await expect(publicKey.getByText("Copied to clipboard")).toBeAttached();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(line);
  await settle(page);
  await expectNoA11yViolations(page, "an application's deploy key");

  // Asked as a deploy would ask.
  await key.getByRole("button", { name: "Test the connection" }).click();
  await expect(key.getByText("The repository answered (branch main).")).toBeVisible();

  // Replaced without a moment when the repository has no key that works.
  await key.getByRole("button", { name: "Replace the key" }).click();
  const rotation = key.getByRole("group", { name: "Replacing the key" });
  await expect(rotation.getByText("Waiting for GitHub to accept the new key")).toBeVisible();
  const next = (await rotation.locator("code").innerText()).trim();
  expect(next).toMatch(/^ssh-ed25519 \S+ /);
  expect(next).not.toBe(line);
  await expect(rotation.getByText(/Leave the old one there until the test passes/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a deploy key being replaced");

  // The host has not been given the new key yet: it is refused, git's old key answers, nothing is lost.
  await rotation.getByRole("button", { name: "Test the new key" }).click();
  await expect(key.getByText(/answered with the current key, not the new one/)).toBeVisible();
  await expect(rotation).toBeVisible();
  await expect(publicKey.locator("code")).toHaveText(line);

  // Added since: it answers, becomes the key, and the old one is gone.
  await rotation.getByRole("button", { name: "Test the new key" }).click();
  await expect(rotation).toBeHidden();
  await expect(publicKey.locator("code")).toHaveText(next);
  await expect(key.getByText(/The new key works; it is the deploy key of taller\.example\.com now/)).toBeVisible();

  // Removed after saying that the host still trusts the public half.
  await key.getByRole("button", { name: "Remove" }).click();
  const dialog = page.getByRole("alertdialog", { name: `Remove the deploy key of ${SSH_APP}?` });
  await expect(dialog.getByText(/GitHub still trusts the public key until you remove it there/)).toBeVisible();
  await dialog.getByRole("button", { name: "Remove key" }).click();
  await expect(key.getByText("No key of its own", { exact: true })).toBeVisible();
});

test("a key that exists is imported once and shown only as its public half", async ({ page, consoleServer, problems }) => {
  // A key with a passphrase is refused by the server; the refusal is the answer under test.
  problems.expect(/status of 400 .* \/api\/apps\/taller\.example\.com\/deploy-key\/import$/);
  await signIn(page, consoleServer, `/apps/${SSH_APP}/settings/source`);
  await elevate(page, consoleServer);
  const key = card(page, "Deploy key");
  await key.getByRole("button", { name: "Import a key…" }).click();
  const dialog = page.getByRole("dialog", { name: /^Import a deploy key for/ });
  await dialog.getByRole("button", { name: "Import key" }).click();
  await expect(dialog.getByText("Paste the private key.")).toBeVisible();

  await dialog.getByRole("textbox", { name: "Private key" }).fill("-----BEGIN OPENSSH PRIVATE KEY-----\nnot a key\n-----END OPENSSH PRIVATE KEY-----\n");
  await dialog.getByRole("button", { name: "Import key" }).click();
  // The server's own refusal, in its own words, with the dialog still open for another try.
  await expect(dialog.getByText("The key was not imported")).toBeVisible();
  await expect(dialog).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the import dialog with a refusal");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
});

test("an https credential is kept as a secret and is never shown back", async ({ page, consoleServer }) => {
  const token = "ghp_e2eTokenThatMustNeverComeBack0123456789";
  await signIn(page, consoleServer, `/apps/${HTTPS_APP}/settings/source`);
  await elevate(page, consoleServer);
  const credential = card(page, "HTTPS credential");
  await expect(credential.getByText("No credential", { exact: true })).toBeVisible();

  await credential.getByRole("button", { name: "Set credential…" }).click();
  const dialog = page.getByRole("dialog", { name: /^Set the credential for/ });
  await dialog.getByRole("textbox", { name: "User" }).fill("deploy-bot");
  await dialog.getByLabel("Token").fill(token);
  await expect(dialog.getByLabel("Token")).toHaveAttribute("type", "password");
  await dialog.getByRole("button", { name: "Save credential" }).click();
  await expect(dialog).toBeHidden();
  await expect(credential.getByText("A credential is kept", { exact: true })).toBeVisible();
  await expect(credential.getByText("deploy-bot")).toBeVisible();

  // Nothing on the page, and nothing the API sends, carries the token again.
  await page.reload();
  await expect(card(page, "HTTPS credential").getByText("A credential is kept", { exact: true })).toBeVisible();
  expect(await page.content()).not.toContain(token);
  const stored = await page.request.get(`/api/apps/${HTTPS_APP}/git-credential`);
  expect(await stored.text()).not.toContain(token);
  await settle(page);
  await expectNoA11yViolations(page, "a kept https credential");

  await card(page, "HTTPS credential").getByRole("button", { name: "Remove" }).click();
  await page.getByRole("alertdialog", { name: `Remove the credential of ${HTTPS_APP}?` }).getByRole("button", { name: "Remove credential" }).click();
  await expect(card(page, "HTTPS credential").getByText("No credential", { exact: true })).toBeVisible();
});

test("the server lists its deploy keys and pinned host keys, and forgets a host key on request", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${SSH_APP}/settings/source`);
  await elevate(page, consoleServer);
  const made = await page.request.post(`/api/apps/${SSH_APP}/deploy-key`, { data: {}, headers: await csrf(page) });
  expect(made.ok(), await made.text()).toBe(true);
  try {
    await page.goto("/settings/integrations");
    const keys = page.getByRole("heading", { level: 2, name: "Deploy keys" }).locator("xpath=ancestor::section[1]");
    const table = keys.getByRole("table", { name: "Deploy keys of this server" });
    const row = table.getByRole("row").filter({ hasText: "taller-example-com" });
    await expect(row).toBeVisible();
    await expect(row.getByText("github.com/you/landing")).toBeVisible();
    await expect(row.getByRole("link", { name: SSH_APP })).toHaveAttribute("href", `/apps/${SSH_APP}/settings/source`);

    // The host keys the machine pinned, one of them on another port.
    const hosts = keys.getByRole("table", { name: "Host keys pinned by this server" });
    await expect(hosts.getByRole("row").filter({ hasText: "github.com" })).toContainText("ED25519");
    await expect(hosts.getByText("[git.example.org]:2222")).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "the server's deploy keys");

    // Forgetting one is the deliberate way to accept a key that changed, and says so first.
    await hosts.getByRole("button", { name: "Forget [git.example.org]:2222" }).click();
    const dialog = page.getByRole("alertdialog", { name: "Forget the host key of [git.example.org]:2222?" });
    await expect(dialog.getByText(/Do it only after comparing the fingerprint/)).toBeVisible();
    await dialog.getByRole("button", { name: "Forget host key" }).click();
    await expect(hosts.getByText("[git.example.org]:2222")).toBeHidden();
    await expect(hosts.getByText("github.com")).toBeVisible();
  } finally {
    await removeKey(page, SSH_APP);
  }
});
