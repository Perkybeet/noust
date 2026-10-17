/**
 * The command palette acts on an application (checklist M-25), against the real backend: a verb
 * is the application's own button, so the same endpoint answers, the stop asks its question and
 * the axe and CSP gates hold with the dialogs of the lazily loaded runner on screen.
 */

import { expect, expectNoA11yViolations, settle, signIn, test, toasts } from "./fixtures";

const SEARCH = "Search apps, databases, domains, servers, settings and actions";

test("a restart typed in the palette is the page's own call, and the operator stays where they were", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/cron");
  await settle(page);

  await page.keyboard.press("Control+k");
  const search = page.getByRole("combobox", { name: SEARCH });
  await search.fill("restart shop");
  const option = page.getByRole("option", { name: "Restart shop.example.net" });
  await expect(option).toBeVisible();
  await expectNoA11yViolations(page, "the palette with a verb");

  const restarted = page.waitForResponse((response) => response.url().endsWith("/api/apps/shop.example.net/restart"));
  await option.click();
  expect((await restarted).status()).toBe(200);
  await expect(toasts(page).getByText("Restarted shop.example.net")).toBeVisible();
  await expect(page).toHaveURL(/\/cron$/);
});

test("a stop typed in the palette asks the page's question first, and stops only when it is answered", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/cron");
  await settle(page);

  await page.keyboard.press("Control+k");
  await page.getByRole("combobox", { name: SEARCH }).fill("stop shop");
  const stopped = page.waitForResponse((response) => response.url().endsWith("/api/apps/shop.example.net/stop"));
  await page.getByRole("option", { name: "Stop shop.example.net" }).click();

  const dialog = page.getByRole("alertdialog", { name: "Stop shop.example.net?" });
  await expect(dialog).toBeVisible();
  await expectNoA11yViolations(page, "the question before a stop");
  await dialog.getByRole("button", { name: "Stop application" }).click();
  expect((await stopped).status()).toBe(200);
  await expect(toasts(page).getByText("Stopped shop.example.net")).toBeVisible();
  await expect(dialog).toBeHidden();
});

test("going back typed in the palette opens the dialog the Roll back button opens", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/cron");
  await settle(page);

  await page.keyboard.press("Control+k");
  await page.getByRole("combobox", { name: SEARCH }).fill("roll back shop");
  await page.getByRole("option", { name: "Roll back shop.example.net" }).click();

  const dialog = page.getByRole("dialog", { name: "Roll back shop.example.net" });
  await expect(dialog).toBeVisible();
  await expectNoA11yViolations(page, "the choice of version from the palette");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});

test("on an application's page the palette offers that application's verbs before anything is typed", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/apps/shop.example.net");
  await settle(page);

  await page.keyboard.press("Control+k");
  await expect(page.getByRole("option", { name: "Restart shop.example.net" })).toBeVisible();
  await expect(page.getByRole("option", { name: /^Restart (?!shop\.example\.net)/ })).toHaveCount(0);
});
