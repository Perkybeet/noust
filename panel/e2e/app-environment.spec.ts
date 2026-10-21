/**
 * An application's environment against the real backend: a messy `.env` pasted into the
 * bulk editor is saved as exactly the change that makes the file the map EnvManager reads from
 * the same text (the shared fixture tests/fixtures/env/messy.env and its .json) - only the
 * variables that differ, never the ones left alone - and a secret is shown only after the
 * operator confirms it's them. Runs in both themes with the CSP and console gates of the
 * `problems` fixture.
 *
 * The paste replaces the whole file, so each theme project writes to its own app: the light
 * one to the in-place pedidos.example.org (<app>/.env), the dark one to the release app
 * tienda.example.org (shared/.env). Both projects can share a worker's machine.
 */

import type { Page, TestInfo } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";

import { confirmItsYou, expect, expectNoA11yViolations, settle, signIn, test, toasts } from "./fixtures";

const FIXTURES = path.resolve(import.meta.dirname, "..", "..", "tests", "fixtures", "env");
const MESSY = readFileSync(path.join(FIXTURES, "messy.env"), "utf8");
const MESSY_MAP = JSON.parse(readFileSync(path.join(FIXTURES, "messy.json"), "utf8")) as Record<string, string>;
const SAVED_MAP = Object.fromEntries(Object.entries(MESSY_MAP).filter(([name]) => name !== "PORT"));

const SCREENS = process.env.NOUST_TABS_SCREENS ?? "/tmp/console-tabs";

function appFor(testInfo: TestInfo): string {
  return testInfo.project.name === "dark" ? "tienda.example.org" : "pedidos.example.org";
}

function table(page: Page, domain: string) {
  return page.getByRole("table", { name: `Environment variables of ${domain}` });
}

test("a messy .env pasted in is saved as exactly the change that makes the file what EnvManager reads from it", async ({ page, consoleServer, problems }, testInfo) => {
  const domain = appFor(testInfo);
  // Writing the variables is sudo mode's: the first attempt is refused until the operator confirms it's them.
  problems.expect(new RegExp(`status of 403 .*/api/apps/${domain.replace(/\./g, "\\.")}/env$`));
  await signIn(page, consoleServer, `/apps/${domain}/environment`);
  await expect(table(page, domain)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the environment tab");
  const current = ((await (await page.request.get(`/api/apps/${domain}/env`)).json()) as { variables: Record<string, string> }).variables;

  await page.getByRole("button", { name: "Paste .env" }).click();
  const paste = page.getByRole("dialog", { name: "Paste a .env file" });
  await paste.getByLabel(".env contents").fill(MESSY);
  const count = Object.keys(MESSY_MAP).length;
  await expect(paste.getByText(`${String(count)} variables found`)).toBeVisible();
  await expect(paste.getByText(/PORT is set on lines \d+ and \d+; the later line wins\./)).toBeVisible();
  await paste.getByRole("radio", { name: "Replace all" }).click();
  await expectNoA11yViolations(page, "the paste dialog");
  await paste.getByRole("button", { name: `Stage ${String(count)} variables` }).click();
  await expect(paste).toBeHidden();
  // Noust sets PORT itself, and the backend refuses a save that adds or changes it; the
  // operator keeps the app's own before saving, which also proves a staged change can be undone.
  await page.getByRole("button", { name: "Actions for PORT" }).click();
  await page.getByRole("menuitem", { name: "Undo the change" }).click();

  // A pasted file is looked over whole first, over what the page knows: nothing is read in clear for it.
  await page.getByRole("button", { name: "Save and restart", exact: true }).click();
  const review = page.getByRole("dialog", { name: "Review changes" });
  await expect(review).toBeVisible();
  await review.getByRole("switch", { name: "Show values" }).click();
  await expectNoA11yViolations(page, "the review dialog");

  const patch = page.waitForRequest((request) => request.method() === "PATCH" && request.url().endsWith(`/api/apps/${domain}/env`));
  await review.getByRole("button", { name: "Save and restart" }).click();
  await confirmItsYou(page, consoleServer);
  // The variables whose value is not the one the page already holds, and the ones the file no longer has. PORT is neither.
  const kept = { ...SAVED_MAP };
  const set = Object.fromEntries(Object.entries(kept).filter(([name, value]) => current[name] !== value));
  const unset = Object.keys(current).filter((name) => name !== "PORT" && !(name in kept));
  // The comment above JWT_SECRET in the pasted file is kept with it, as the file says it.
  expect((await patch).postDataJSON()).toEqual({ set, unset, dry_run: false, comments: { JWT_SECRET: "Secrets" } });

  await expect(toasts(page).getByText(`Restarted ${domain}`)).toBeVisible();

  // The file on disk now reads back as the pasted map: what the table shows comes from it.
  await expect(table(page, domain).getByRole("cell", { name: "GREETING", exact: true })).toBeVisible();
  // A plain value is in view without asking.
  await expect(table(page, domain).getByText("hola # not a comment", { exact: true })).toBeVisible();
  await expect(table(page, domain).getByRole("cell", { name: "SESSION_SECRET", exact: true })).toHaveCount(0);
});

test("a hidden value is kept or replaced from its row, and is never read to be edited", async ({ page, consoleServer }) => {
  const domain = "pedidos.example.org";
  const unmasks: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes(`/api/apps/${domain}/env`) && request.url().includes("unmask=true")) unmasks.push(request.url());
  });
  await signIn(page, consoleServer, `/apps/${domain}/environment`);
  await expect(table(page, domain)).toBeVisible();
  // A row opens on press; a hidden value is not shown, not editable, and offers to be replaced.
  await table(page, domain).getByText("JWT_SECRET", { exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Edit JWT_SECRET" });
  await expect(dialog.getByLabel("Value")).toBeDisabled();
  await expect(dialog.getByText("The value is hidden. It stays as it is unless you replace it.")).toBeVisible();
  await expectNoA11yViolations(page, "a hidden value's dialog");
  await dialog.getByRole("button", { name: "Replace value" }).click();
  await expect(dialog.getByLabel("Value")).toBeFocused();
  await dialog.getByRole("button", { name: "Keep current value" }).click();
  await expect(dialog.getByLabel("Value")).toBeDisabled();
  // Not confirmed: the worker's file keeps its value.
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  expect(unmasks).toEqual([]);
});

test("a secret is shown only after confirming it's you", async ({ page, consoleServer, problems }) => {
  // The same value in the seeded file and in messy.env, so the other test's save does not matter.
  const domain = "pedidos.example.org";
  problems.expect(/status of 403 .*\/api\/apps\/pedidos\.example\.org\/env$/);
  await signIn(page, consoleServer, `/apps/${domain}/environment`);
  await expect(table(page, domain)).toBeVisible();
  await expect(table(page, domain).getByText("2f7c9e1a4b6d8f0a3c5e7b9d1f2a4c6e")).toHaveCount(0);

  await page.getByRole("button", { name: "Reveal the value of JWT_SECRET" }).click();
  await expectNoA11yViolations(page, "the confirmation before a secret is revealed");
  await confirmItsYou(page, consoleServer);
  await expect(table(page, domain).getByText("2f7c9e1a4b6d8f0a3c5e7b9d1f2a4c6e", { exact: true })).toBeVisible();
  await expectNoA11yViolations(page, "a revealed secret");

  await page.getByRole("button", { name: "Hide the value of JWT_SECRET" }).click();
  await expect(table(page, domain).getByText("2f7c9e1a4b6d8f0a3c5e7b9d1f2a4c6e")).toHaveCount(0);
});

test.describe("environment dialogs @screens", () => {
  const DESKTOP = { width: 1440, height: 900 };
  const PHONE = { width: 390, height: 844 };

  test("app tab environment dialogs", async ({ page, consoleServer }, testInfo) => {
    const dir = path.join(SCREENS, testInfo.project.name);
    // Read only: the dialogs are photographed and cancelled, nothing is saved.
    const domain = "blog.example.org";
    await page.setViewportSize(DESKTOP);
    await signIn(page, consoleServer, `/apps/${domain}/environment`);
    await expect(table(page, domain)).toBeVisible();

    await page.getByRole("button", { name: "Paste .env" }).click();
    const paste = page.getByRole("dialog", { name: "Paste a .env file" });
    await paste.getByLabel(".env contents").fill(`export API_URL=https://api.example.org\n${MESSY}`);
    await settle(page);
    await page.screenshot({ path: path.join(dir, "environment-paste-1440.png") });
    await page.setViewportSize(PHONE);
    await settle(page);
    await page.screenshot({ path: path.join(dir, "environment-paste-390.png") });

    // `export` is read the way a shell reads it: nothing to fix before staging.
    await paste.getByRole("button", { name: /^Stage \d+ variables$/ }).click();
    await page.setViewportSize(DESKTOP);
    await page.getByRole("button", { name: "Save and restart", exact: true }).click();
    const review = page.getByRole("dialog", { name: "Review changes" });
    await review.getByRole("switch", { name: "Show values" }).click();
    await settle(page);
    await page.screenshot({ path: path.join(dir, "environment-review-1440.png") });
    await page.setViewportSize(PHONE);
    await settle(page);
    await page.screenshot({ path: path.join(dir, "environment-review-390.png") });
    await review.getByRole("button", { name: "Cancel" }).click();

    await page.setViewportSize(DESKTOP);
    await settle(page);
    await page.screenshot({ path: path.join(dir, "environment-draft-1440.png"), fullPage: true });
  });
});
