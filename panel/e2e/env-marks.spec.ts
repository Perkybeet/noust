/**
 * Why each environment variable is hidden or shown, and the operator's own call about it,
 * against the real backend (2.2). The seed (tests/panel_factory.seed_env_marks) marks two
 * variables of blog.example.org: ANALYTICS_TOKEN, hidden by its name, as not secret, and
 * SMTP_HOST, which nothing about looks secret, as secret. Every other line is the
 * classifier's own verdict, said in words.
 *
 * A mark writes through PUT /api/apps/{domain}/env/marks at once, behind "Confirm it's you".
 * The worker's machine is shared by every spec it runs, so the test puts the seeded marks back.
 */

import type { Page } from "@playwright/test";

import { confirmItsYou, expect, expectNoA11yViolations, settle, signIn, stillness, test } from "./fixtures";

const DOMAIN = "blog.example.org";
const SEEDED_MARKS = { ANALYTICS_TOKEN: false, SMTP_HOST: true };

function table(page: Page) {
  return page.getByRole("table", { name: `Environment variables of ${DOMAIN}` });
}

function row(page: Page, name: string) {
  return table(page)
    .getByRole("row")
    // The name's own text: a masked value's cell also names its lock ("Secret").
    .filter({ has: page.getByText(name, { exact: true }) });
}

/** A variable's row menu: its edits, and whether it is a secret. */
function trigger(page: Page, name: string) {
  return page.getByRole("button", { name: `Actions for ${name}` });
}

/** The Type cell of a variable's row: "Secret" or "Plain", a few words why, the whole reason as its title. */
function kind(page: Page, name: string) {
  return row(page, name).getByRole("cell").nth(2);
}

async function csrf(page: Page): Promise<Record<string, string>> {
  const cookie = (await page.context().cookies()).find((entry) => entry.name === "wasm_csrf");
  return cookie ? { "X-WASM-CSRF": cookie.value } : {};
}

async function marksOf(page: Page): Promise<Record<string, { secret: boolean; reason: string; marked: boolean }>> {
  const response = await page.request.get(`/api/apps/${DOMAIN}/env`);
  expect(response.ok()).toBe(true);
  return ((await response.json()) as { secrets: Record<string, { secret: boolean; reason: string; marked: boolean }> }).secrets;
}

test("says why each value is hidden, and marks one secret, not secret or automatic", async ({ page, consoleServer, problems }) => {
  // A mark asks "Confirm it's you" by answering 403 first, by design.
  problems.expect(new RegExp(`status of 403 .*/api/apps/${DOMAIN.replace(/\./g, "\\.")}/env/marks$`));
  await signIn(page, consoleServer, `/apps/${DOMAIN}/environment`);
  await expect(table(page)).toBeVisible();

  // Each verdict in words: the operator's marks, and the classifier's reasons.
  await expect(kind(page, "ANALYTICS_TOKEN")).toHaveText("Plain (marked by you)");
  await expect(kind(page, "SMTP_HOST")).toHaveText("Secret (marked by you)");
  await expect(kind(page, "DATABASE_URL")).toHaveText("Secret (the URL has a password)");
  await expect(kind(page, "JWT_SECRET")).toHaveText("Secret (by its name)");
  await expect(kind(page, "FEATURE_FLAGS")).toHaveText("Plain");
  // The whole reason is there too, as each cell's title.
  await expect(kind(page, "JWT_SECRET").locator("[title]")).toHaveAttribute("title", "Hidden: its name suggests a secret");
  await settle(page);
  await expectNoA11yViolations(page, "the environment tab with marks");

  // A value marked not secret comes from the server in clear, in view, without asking.
  await expect(row(page, "ANALYTICS_TOKEN")).toContainText("G-8XK2M4PQ7L");
  await expect(page.getByRole("button", { name: "Reveal the value of ANALYTICS_TOKEN" })).toHaveCount(0);

  // Mark a plain variable secret: the menu names the current choice and offers the other two.
  await trigger(page, "FEATURE_FLAGS").click();
  const current = page.getByRole("menuitem", { name: /Decide automatically/ });
  await expect(current).toHaveAccessibleName("Decide automatically (current)");
  await expect(current).toBeDisabled();
  await stillness(page);
  await expectNoA11yViolations(page, "the secrecy menu");
  const put = page.waitForRequest((request) => request.method() === "PUT" && request.url().endsWith(`/api/apps/${DOMAIN}/env/marks`));
  await page.getByRole("menuitem", { name: "Yes, always hide it" }).click();
  expect((await put).postDataJSON()).toMatchObject({ marks: { FEATURE_FLAGS: true } });
  // The menu fades out under the dialog; measured mid-fade, its text reads as low contrast.
  await expect(page.getByRole("menu")).toHaveCount(0);
  await stillness(page);
  await expectNoA11yViolations(page, "the confirmation before a mark");
  await confirmItsYou(page, consoleServer);
  await expect(kind(page, "FEATURE_FLAGS")).toHaveText("Secret (marked by you)");

  // Mark the seeded secret as not secret: its value is shown in the listing.
  await trigger(page, "SMTP_HOST").click();
  await page.getByRole("menu", { name: /SMTP_HOST/ }).getByRole("menuitem", { name: "No, always show it" }).click();
  await expect(kind(page, "SMTP_HOST")).toHaveText("Plain (marked by you)");

  // Back to automatic: the name decides again, and hides the analytics id.
  await trigger(page, "ANALYTICS_TOKEN").click();
  await page.getByRole("menu", { name: /ANALYTICS_TOKEN/ }).getByRole("menuitem", { name: "Decide automatically" }).click();
  await expect(kind(page, "ANALYTICS_TOKEN")).toHaveText("Secret (by its name)");
  await settle(page);
  await expectNoA11yViolations(page, "the environment tab after marking");

  // The server holds every mark: a fresh load reads the same.
  await page.reload();
  await expect(kind(page, "FEATURE_FLAGS")).toHaveText("Secret (marked by you)");
  await expect(kind(page, "SMTP_HOST")).toHaveText("Plain (marked by you)");
  await expect(kind(page, "ANALYTICS_TOKEN")).toHaveText("Secret (by its name)");
  // The listing follows the marks: a secret comes masked, a value marked not secret in clear.
  const listing = (await (await page.request.get(`/api/apps/${DOMAIN}/env`)).json()) as { variables: Record<string, string> };
  expect(listing.variables.FEATURE_FLAGS).toBe("***");
  expect(listing.variables.SMTP_HOST).toBe("smtp.example.org");
  expect(listing.variables.ANALYTICS_TOKEN).toBe("***");
  const saved = await marksOf(page);
  expect(saved.FEATURE_FLAGS).toEqual({ secret: true, reason: "marked secret", marked: true });
  expect(saved.SMTP_HOST).toEqual({ secret: false, reason: "marked not secret", marked: true });
  expect(saved.ANALYTICS_TOKEN).toEqual({ secret: true, reason: "name", marked: false });

  // Leave the worker's machine as seeded (the session is still elevated).
  const restored = await page.request.put(`/api/apps/${DOMAIN}/env/marks`, {
    headers: await csrf(page),
    data: { marks: { ...SEEDED_MARKS, FEATURE_FLAGS: null } },
  });
  expect(restored.ok()).toBe(true);
  const after = await marksOf(page);
  expect(after.ANALYTICS_TOKEN?.reason).toBe("marked not secret");
  expect(after.SMTP_HOST?.reason).toBe("marked secret");
  expect(after.FEATURE_FLAGS?.marked).toBe(false);
});
