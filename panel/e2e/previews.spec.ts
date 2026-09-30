/**
 * Pull request previews against the real backend: portal.example.org has previews on under
 * previews.example.org, with #42 ready (deployed as its own application), #57 building and #61
 * failed. Its Previews settings draw every state and the failed build's output verbatim, the
 * applications list says whose preview #42 is, the settings are saved through "Confirm it's
 * you", and removing #42 takes its application down through the real deletion job.
 *
 * The tests change the one seeded server of their worker, so they run in order: they read
 * the seeded states first, and remove the preview last.
 */

import type { Page } from "@playwright/test";

import { confirmItsYou, expect, expectNoA11yViolations, signIn, stillness, test } from "./fixtures";

const APP = "portal.example.org";
const READY = "pr-42-portal-example-org.previews.example.org";

test.describe.configure({ mode: "serial" });

function previewsSection(page: Page) {
  return page.getByRole("region", { name: "Pull request previews", exact: true });
}

function previewList(page: Page) {
  return previewsSection(page).getByRole("list", { name: "Previews", exact: true });
}

function previewItem(page: Page, number: number) {
  return previewList(page)
    .getByRole("listitem")
    .filter({ has: page.getByRole("button", { name: `Remove the preview of pull request #${String(number)}` }) });
}

test("the settings show previews on, and each preview in its state", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await signIn(page, consoleServer, `/apps/${APP}/settings/previews`);
  const section = previewsSection(page);
  await expect(section.getByText("On", { exact: true })).toBeVisible();
  await expect(section.getByLabel("Base domain")).toHaveValue("previews.example.org");
  await expect(section.getByText(/^Now: at most 5 previews under previews\.example\.org/)).toBeVisible();
  await expect(section.getByText("3 of at most 5", { exact: true })).toBeVisible();

  const ready = previewItem(page, 42);
  await expect(ready.getByText("Ready", { exact: true })).toBeVisible();
  await expect(ready.getByRole("link", { name: READY, exact: true })).toBeVisible();
  await expect(ready.getByText("feature/checkout-redesign", { exact: true })).toBeVisible();
  await expect(ready.getByText("8c1f2e7", { exact: true })).toBeVisible();
  await expect(ready.getByText("github example-org/portal", { exact: true })).toBeVisible();
  await expect(ready.getByRole("link", { name: "Open preview of pull request #42 (opens in a new tab)" })).toHaveAttribute(
    "href",
    `https://${READY}`,
  );

  await expect(previewItem(page, 57).getByText("Deploying", { exact: true })).toBeVisible();

  // A failed build says so, with npm's own words under it.
  const failed = previewItem(page, 61);
  await expect(failed.getByText("Failed", { exact: true })).toBeVisible();
  await expect(failed.getByText("The last build of #61 failed", { exact: true })).toBeVisible();
  await expect(failed.getByText(/Type error: Property 'total' does not exist on type 'Cart'\./)).toBeVisible();
  await expect(failed.getByText(/npm ERR! portal@0\.9\.0 build: `next build`/)).toBeVisible();

  await section.scrollIntoViewIfNeeded();
  await stillness(page);
  await expectNoA11yViolations(page, "an app with previews in every state");
});

test("the applications list says whose preview an app is", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/apps");
  const row = page.getByRole("row").filter({ has: page.getByRole("link", { name: READY, exact: true }) });
  await expect(row.getByText(`Preview of ${APP}`, { exact: true })).toBeVisible();
  // The app previewed is not marked: it is one the operator deployed.
  const parent = page.getByRole("row").filter({ has: page.getByRole("link", { name: APP, exact: true }) });
  await expect(parent.getByText(/^Preview of/)).toHaveCount(0);

  // A preview's own settings point at its parent instead of offering previews of its own.
  await page.goto(`/apps/${READY}/settings/previews`);
  const section = previewsSection(page);
  await expect(section.getByText("This app is a preview of", { exact: false })).toBeVisible();
  await expect(section.getByRole("link", { name: APP, exact: true })).toHaveAttribute("href", `/apps/${APP}/settings/previews`);
  await expect(section.getByLabel("Base domain")).toHaveCount(0);
});

test("the preview settings are saved and kept", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await signIn(page, consoleServer, `/apps/${APP}/settings/previews`);
  const section = previewsSection(page);
  const save = section.getByRole("button", { name: "Save", exact: true });
  await expect(section.getByLabel("At most")).toHaveValue("5");
  await expect(save).toBeDisabled();

  // Out of range: said under the field, and nothing is sent.
  await section.getByLabel("At most").fill("40");
  await save.click();
  await expect(section.getByText("From 1 to 20 previews at once.", { exact: true })).toBeVisible();

  await section.getByLabel("At most").fill("4");
  await section.getByRole("radio", { name: "Hours" }).click();
  await section.getByLabel("Removed after").fill("72");
  const sent = page.waitForRequest((r) => r.url().endsWith(`/api/apps/${APP}/previews/settings`) && r.method() === "PUT");
  await save.click();
  await confirmItsYou(page, consoleServer);
  expect((await sent).postDataJSON()).toMatchObject({ base_domain: "previews.example.org", max_previews: 4, ttl_hours: 72 });
  await expect(section.getByText("No unsaved changes", { exact: true })).toBeVisible();
  await expect(section.getByText(/^Now: at most 4 previews under previews\.example\.org, each removed after 3 days/)).toBeVisible();

  await page.reload();
  await expect(previewsSection(page).getByLabel("At most")).toHaveValue("4");
  await expect(previewsSection(page).getByText("3 of at most 4", { exact: true })).toBeVisible();
});

test("a preview is removed with its application", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await signIn(page, consoleServer, `/apps/${APP}/settings/previews`);
  const ready = previewItem(page, 42);
  await ready.getByRole("button", { name: "Remove the preview of pull request #42" }).click();
  await confirmItsYou(page, consoleServer);

  const dialog = page.getByRole("alertdialog", { name: "Remove the preview of pull request #42?" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText(`${READY} stops answering`, { exact: false })).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "removing a preview");

  const removal = page.waitForResponse((r) => r.url().endsWith(`/api/apps/${APP}/previews/42`) && r.request().method() === "DELETE");
  await dialog.getByRole("button", { name: "Remove preview" }).click();
  expect((await removal).status()).toBe(202);
  await expect(dialog).toBeHidden();

  // The row goes when the removal job ends: that is the outcome, on screen.
  await expect(previewItem(page, 42)).toHaveCount(0, { timeout: 20_000 });
  await expect(previewItem(page, 57)).toBeVisible();
  await expect(previewItem(page, 61)).toBeVisible();

  // Its application went with it.
  const apps = (await (await page.request.get("/api/apps")).json()) as { apps: { domain: string }[] };
  expect(apps.apps.map((app) => app.domain)).not.toContain(READY);
  await page.goto("/apps");
  await expect(page.getByRole("link", { name: APP, exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: READY, exact: true })).toHaveCount(0);
});
