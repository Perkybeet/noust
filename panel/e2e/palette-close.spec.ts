/**
 * Closing the search is judged by what is drawn while it closes. The dialog stays on screen for the length of its exit,
 * and at the instant a result was chosen the empty state, "Nothing matches", was drawn over the frames that fade out:
 * a test that waits for the dialog to be gone never sees it, so every change of the dialog is looked at.
 */

import { expect, settle, signIn, test } from "./fixtures";

const SEARCH = "Search apps, databases, domains, servers, settings and actions";

declare global {
  interface Window {
    __noustSaid?: string[];
  }
}

test("choosing a result does not say that nothing matches while the search closes", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/cron");
  await settle(page);
  await page.evaluate(() => {
    const said: string[] = [];
    window.__noustSaid = said;
    new MutationObserver(() => {
      const status = document.querySelector('[role="dialog"] [role="status"]');
      if (status?.textContent.includes("Nothing matches") === true) said.push(status.textContent);
    }).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true });
  });

  await page.keyboard.press("Control+k");
  await page.getByRole("combobox", { name: SEARCH }).fill("databases");
  const option = page.getByRole("option").first();
  await expect(option).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/databases/);
  await expect(page.getByRole("dialog", { name: "Search the console" })).toBeHidden();

  expect(await page.evaluate(() => window.__noustSaid)).toEqual([]);
});
