/**
 * A server with nothing on it (`scripts/console_server.py --empty`): no applications, no accounts, an address of its
 * own so a temporary name can be made. The specs that start from it share how the first run is walked.
 */

import type { Page } from "@playwright/test";

import { expect } from "./fixtures";
import type { RunningServer } from "./fixtures";

/** The password the first administrator is given. */
export const FIRST_RUN_PASSWORD = "a-long-password-for-the-first-run";

/**
 * Walks the first run of a server with nothing on it to its Overview: the link the installer prints,
 * the access token, the first administrator, and "Set up later". None of it is counted by the specs
 * that count clicks: they start from the Overview an operator lands on.
 *
 * @param page - A page that has no session.
 * @param server - The server started with `--empty`.
 */
export async function firstRun(page: Page, server: RunningServer): Promise<void> {
  await page.goto(`${server.url}/login?with=token`);
  await page.getByLabel("Access token").fill(server.token);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Create the administrator" })).toBeVisible();
  await page.getByLabel("Username").fill("admin");
  await page.getByLabel("Password", { exact: true }).fill(FIRST_RUN_PASSWORD);
  await page.getByRole("button", { name: "Create administrator" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Protect your account" })).toBeVisible();
  await page.getByRole("button", { name: "Set up later" }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Deploy your first app" })).toBeVisible();
}
