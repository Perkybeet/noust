import type { Page } from "@playwright/test";

import { expect } from "./fixtures";

/**
 * Opens what an application's page keeps one press away: a part its page folds (Automation, Deploy hooks, Previews on
 * Deploys and triggers; Processes, Mounts, Disk and Service security on Resources and running) or a group of cards
 * inside a part (how Noust reaches the repository, the sandbox and the packages of a build). A person who needs it
 * presses its title; a test does the same, by the title, and waits for it to be open before it reads inside.
 */
export async function openFold(page: Page, title: string): Promise<void> {
  // The name starts with the title: a fold may say beside it what is inside.
  const name = new RegExp(`^${title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`);
  await page.getByRole("button", { name, expanded: false }).click();
  await expect(page.getByRole("button", { name, expanded: true })).toBeVisible();
}
