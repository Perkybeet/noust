/**
 * What a central offers to add servers and keep them tidy (checklist F-07, F-09, F-22 to F-25,
 * F-34), against the real backend and its sandboxed machine: the cloud settings (accounts and the
 * boot scripts that ship with Noust), the way for a server behind NAT, the pause of a server's
 * monitoring is covered by its component tests (this machine has no registered server), and the
 * disk's automatic cleanup. No provider is called: a token is only ever checked against its own
 * shape here.
 */

import type { Page } from "@playwright/test";

import { confirmItsYou, expect, expectNoA11yViolations, settle, test } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

async function signIn(page: Page, server: ConsoleServer, path: string): Promise<void> {
  const answer = await page.request.post("/api/auth/login", { data: { token: server.token, totp_code: server.secondFactor() } });
  expect(answer.ok()).toBe(true);
  await page.goto(path);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}

test("Settings > Cloud: no account yet, the scripts that ship with Noust, and an account that is checked before it is sent", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/settings/cloud");
  await expect(page.getByText("No provider account yet")).toBeVisible();
  await expect(page.getByText("Add a provider account first, then create a server.")).toBeVisible();

  // The boot library has what ships with Noust, which can be read and copied, not changed.
  await expect(page.getByText("swap-2g")).toBeVisible();
  await expect(page.getByText("unattended-security-updates")).toBeVisible();
  await expect(page.getByText("Built in").first()).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Settings > Cloud, nothing yet");

  // An account is checked in the form before anything is asked of the central.
  await page.getByRole("button", { name: "Add an account" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a provider account" });
  await dialog.getByLabel(/^Name/).fill("Hetzner Prod");
  await dialog.getByRole("button", { name: "Add account" }).click();
  await expect(dialog.getByText(/Use 1 to 32 lower-case letters, digits and dashes, such as hetzner-prod/)).toBeVisible();
  await expect(dialog.getByText("Choose the provider this token is for.")).toBeVisible();
  await expect(dialog.getByText(/does not look like an API token/)).toBeVisible();
  // A token is a password: not shown unless the operator asks.
  await expect(dialog.getByLabel("API token")).toHaveAttribute("type", "password");
  await settle(page);
  await expectNoA11yViolations(page, "the dialog that adds a provider account");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();

  // Without an account there is nothing to create a server with, and the wizard says so.
  await page.goto("/settings/cloud?create=true");
  const wizard = page.getByRole("dialog", { name: "Create a server" });
  await expect(wizard.getByText("Add a provider account first")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the wizard that creates a server, without an account");
});

test("a server behind NAT: the way is one step from adding a server, and says what this central lacks", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/settings/servers");
  await page.getByRole("button", { name: "Add a server" }).first().click();
  await page.getByRole("button", { name: "It cannot be reached from outside: let it dial this central instead" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a server behind NAT" });
  await expect(dialog.getByText("Run it on the other server: the one you are adding")).toBeVisible();

  // This machine's sshd is not Noust's to prepare from the browser: the command is shown.
  await expect(dialog.getByText("This central is not ready for servers that dial in")).toBeVisible();
  await expect(dialog.getByText("noust node link setup")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Show the command" })).toBeDisabled();
  await settle(page);
  await expectNoA11yViolations(page, "adding a server behind NAT");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
});

test("Server > Storage: the disk cleans itself by a policy that is read back in words, with a history", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/server/storage");
  const heading = page.getByRole("heading", { name: "Automatic cleanup" });
  await expect(heading).toBeVisible();
  await expect(page.getByText("Nothing is cleaned unless you ask")).toBeVisible();
  await expect(page.getByText("No cleanup has run yet.")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Server > Storage, no policy");

  await page.getByRole("button", { name: "Set a policy" }).click();
  const drawer = page.getByRole("dialog", { name: "Automatic cleanup" });
  await drawer.getByLabel(/Run when a disk is this full/).fill("150");
  await drawer.getByRole("button", { name: "Save policy" }).click();
  await expect(drawer.getByText("Use a whole number from 0 to 99.")).toBeVisible();
  await drawer.getByLabel(/Run when a disk is this full/).fill("70");

  // What takes something back is accepted once, in a box the form will not let one skip.
  await drawer.getByRole("checkbox", { name: /Docker build cache/ }).click();
  await expect(drawer.getByText("These delete something you may want back")).toBeVisible();
  await drawer.getByRole("button", { name: "Save policy" }).click();
  await expect(drawer.getByText(/Accept it to schedule a Docker action/)).toBeVisible();
  await drawer.getByRole("checkbox", { name: "I accept that this policy deletes them" }).click();
  await settle(page);
  await expectNoA11yViolations(page, "the cleanup policy drawer");

  await drawer.getByRole("button", { name: "Save policy" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(drawer).toBeHidden();
  await expect(page.getByText("When a disk is 70% full or more")).toBeVisible();
  await expect(page.getByText("Every day at 02:00")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Server > Storage, with a policy");
});
