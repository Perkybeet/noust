/**
 * The account against the real backend (docs/DESIGN.md 6.14, NV-7, NV-8): the page the avatar opens,
 * with the person's profile and password and their sign-in security, which are not Settings. Turning on
 * the second factor is two clicks from anywhere, as it was in Settings > Security. Theme and language
 * live in the account menu and nowhere else. Both themes.
 */

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

test("the avatar menu names who is signed in and leads to the account in one click, and to its sign-in security in two", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/databases");
  await page.evaluate(() => {
    (window as unknown as { __clicks: number }).__clicks = 0;
    document.addEventListener(
      "click",
      (event) => {
        if (event.isTrusted) (window as unknown as { __clicks: number }).__clicks += 1;
      },
      true,
    );
  });
  await page.getByRole("button", { name: "Session and preferences" }).click();
  const menu = page.getByRole("dialog", { name: "Session" });
  await expect(menu.getByText(/^Signed in with the access token on /)).toBeVisible();
  await menu.getByRole("link", { name: "Sign-in security" }).click();
  await expect(page).toHaveURL(/\/account\/security$/);
  await expect(page.getByRole("heading", { level: 1, name: "Account" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: "Two-factor authentication" })).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { __clicks: number }).__clicks)).toBeLessThanOrEqual(2);
  await settle(page);
  await expectNoA11yViolations(page, "the account's sign-in security");
});

test("the account is its own page with two sections, and the person's API tokens are where the instance's are", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/account");
  await expect(page.getByRole("heading", { level: 1, name: "Account" })).toBeVisible();
  const sections = page.getByRole("navigation", { name: "Account sections" });
  await expect(sections.getByRole("link", { name: "Profile and password" })).toHaveAttribute("aria-current", "page");
  await expect(sections.getByRole("link", { name: "Sign-in security" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open API tokens" })).toHaveAttribute("href", "/settings/tokens");
  // Not here: the browser's preferences live in the account menu only (NV-8).
  await expect(page.getByRole("main").getByText("Theme", { exact: true })).toHaveCount(0);
  await settle(page);
  await expectNoA11yViolations(page, "the account");
});

test("Settings no longer has Security: the account's sign-in security and the controls are where they belong", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/settings");
  const list = page.getByRole("navigation", { name: "Settings sections" });
  await expect(list.getByRole("link", { name: "Security", exact: true })).toHaveCount(0);
  await expect(list.getByRole("link", { name: "Controls" })).toBeVisible();
  await page.goto("/settings/security");
  await expect(page).toHaveURL(/\/account\/security$/);
  await page.goto("/settings/controls");
  await expect(page.getByRole("heading", { level: 2, name: "Security profile" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the controls");
});
