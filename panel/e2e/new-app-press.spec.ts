/**
 * Continue after a typed repository takes a press of any length. A browser decides a click by where the
 * finger goes down and where it lifts, and leaving the field to press the button used to start the read of
 * the repository 80ms later: its block went above the button, the button moved 50px or more from under the
 * finger, and the release landed on something else. A press of 90ms is the ordinary one, and it was lost
 * twice in a row on the recording of the product video. jsdom has no layout, so only a browser sees it.
 *
 * The read is answered here with the inspection of a Next.js project, after a moment: the sandbox has no
 * repository on GitHub to clone, and the moment is what a clone takes.
 */

import type { Page } from "@playwright/test";

import { expect, settle, signIn, test } from "./fixtures";

const INSPECTION = {
  app_type: "nextjs",
  detected_types: ["nextjs"],
  package_manager: "npm",
  install_command: ["npm", "ci"],
  build_command: ["npm", "run", "build"],
  start_command: "npm run start",
  default_port: 3000,
  env_keys: [],
  branch: "main",
  commit: "a1b2c3d",
  compatible: true,
};

const REPOSITORY = "https://github.com/acme/api.git";

async function answerReads(page: Page): Promise<{ reads: () => number }> {
  let reads = 0;
  await page.route("**/api/apps/inspect", async (route) => {
    reads += 1;
    // The time a clone takes: long enough for the block that says so to be drawn above the button.
    await new Promise((resolve) => setTimeout(resolve, 400));
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(INSPECTION) });
  });
  return { reads: () => reads };
}

async function typeRepository(page: Page): Promise<void> {
  const field = page.getByLabel("What to deploy", { exact: true });
  await field.fill(REPOSITORY);
}

for (const heldMs of [60, 150, 400]) {
  test(`Continue pressed for ${String(heldMs)} ms after a repository was typed goes on at the first press`, async ({ page, consoleServer }) => {
    await page.setViewportSize({ width: 1440, height: 810 });
    await signIn(page, consoleServer, "/apps/new");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("New application");
    // A page that is still arriving moves under the hand for reasons of its own (the fonts, the lists): the press is
    // the subject here, so it is made on a page that has finished.
    await settle(page);
    const { reads } = await answerReads(page);
    await typeRepository(page);

    // The page rearranges as the address is typed (the catalog gives way, the branch appears): the pointer goes to
    // the button once it has stopped moving, as a hand does.
    await page.getByRole("button", { name: "Continue", exact: true }).hover();
    await page.mouse.down();
    // How long the hand holds it is the input of the test, not something it waits for.
    await page.waitForTimeout(heldMs);
    await page.mouse.up();

    // The wizard went on from the source: it is no longer asking what to deploy.
    await expect(page.getByLabel("What to deploy", { exact: true })).toHaveCount(0);
    // One read, the press's own.
    expect(reads()).toBe(1);
  });
}

test("a repository left with the mouse is read by itself, and a later press joins that read", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 810 });
  await signIn(page, consoleServer, "/apps/new");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("New application");
  await settle(page);
  const { reads } = await answerReads(page);
  await typeRepository(page);
  // Leaving the field for the page's title, not for the button.
  await page.getByRole("heading", { level: 1 }).click();
  await expect(page.getByText("Loading the repository…")).toBeVisible();

  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByLabel("What to deploy", { exact: true })).toHaveCount(0);
  expect(reads()).toBe(1);
});
