/**
 * Going back, against the real backend: one dialog for every way in (the header's Roll back, a
 * row of the history, a deployment's page, Diagnose). It says what goes back, what stays as it
 * is and what happens to the automatic deploys before anything is pressed, and it is a question
 * with a way out: Escape leaves it, and the focus returns to where it was asked from. Nothing is
 * confirmed here, so the worker's machine keeps its state. Runs in both themes, with the CSP and
 * console gates of the `problems` fixture.
 *
 * The seeded machine (scripts/console_server.py): tienda.example.org is on releases with two
 * versions on disk to go back to; pedidos.example.org is kept in a single folder, with a
 * deployment whose snapshot backup exists.
 */

import type { Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

const RELEASES_APP = "tienda.example.org";
const LIVE_APP = "pedidos.example.org";

interface Row {
  id: number;
  rollback_available: boolean;
}

/** The CSRF header every write through `page.request` carries, mirrored from its cookie. */
async function csrf(page: Page): Promise<Record<string, string>> {
  const cookie = (await page.context().cookies()).find((entry) => entry.name === "noust_csrf");
  return cookie ? { "X-Noust-CSRF": cookie.value } : {};
}

/** Another test of this worker may have gone back and paused the app's automatic deploys. */
async function resumed(page: Page, domain: string): Promise<void> {
  const rollout = (await (await page.request.get(`/api/apps/${domain}/rollout`)).json()) as { paused: unknown };
  if (rollout.paused === null) return;
  const response = await page.request.post(`/api/apps/${domain}/rollout/resume`, { headers: await csrf(page) });
  expect(response.ok(), await response.text()).toBe(true);
}

function header(page: Page) {
  return page.locator("main header").filter({ has: page.getByRole("heading", { level: 1 }) });
}

test("one dialog says what goes back, what stays, and what happens to the automatic deploys", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${RELEASES_APP}`);
  await resumed(page, RELEASES_APP);
  await page.reload();

  const trigger = header(page).getByRole("button", { name: "Roll back", exact: true });
  await trigger.click();
  const dialog = page.getByRole("alertdialog", { name: `Roll back ${RELEASES_APP}` });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("combobox", { name: "Version to go back to" })).toBeVisible();
  await expect(dialog.getByText("What goes back", { exact: true })).toBeVisible();
  await expect(dialog.getByText("What stays as it is", { exact: true })).toBeVisible();
  await expect(dialog.getByText("Automatic deploys", { exact: true })).toBeVisible();
  await expect(dialog.getByText("Noust puts code back, never data", { exact: false })).toBeVisible();
  // The one action names the version it goes back to; Cancel is the way out.
  await expect(dialog.getByRole("button", { name: /^Go back to #\d+$/ })).toHaveAttribute("data-variant", "primary");
  await settle(page);
  await expectNoA11yViolations(page, "the rollback dialog");

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("a row of the history opens the same dialog on that version", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${RELEASES_APP}/deployments`);
  const response = await page.request.get(`/api/deployments?domain=${RELEASES_APP}&limit=50`);
  const target = ((await response.json()) as { items: Row[] }).items.find((row) => row.rollback_available);
  if (!target) throw new Error(`no seeded deployment of ${RELEASES_APP} can be gone back to`);

  const table = page.getByRole("region", { name: `Deploys of ${RELEASES_APP}, newest first` });
  await table.getByRole("button", { name: `Actions for #${String(target.id)}` }).click();
  await page.getByRole("menuitem", { name: "Roll back to this" }).click();
  const dialog = page.getByRole("alertdialog", { name: `Roll back ${RELEASES_APP}` });
  await expect(dialog.getByRole("combobox", { name: "Version to go back to" })).toContainText(`#${String(target.id)}`);
  await expect(dialog.getByRole("button", { name: `Go back to #${String(target.id)}` })).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
});

test("an app kept in one folder goes back to a snapshot or a backup, and says a backup is taken first", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${LIVE_APP}`);
  await header(page).getByRole("button", { name: "Roll back", exact: true }).click();
  const dialog = page.getByRole("alertdialog", { name: `Roll back ${LIVE_APP}` });
  await expect(dialog.getByText(/A backup of how it is now is taken first/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the rollback dialog of an app in one folder");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
});

test("on a phone the question and its answer are both in view, and nothing runs off the screen", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, consoleServer, `/apps/${RELEASES_APP}`);
  await header(page).getByRole("button", { name: "Roll back", exact: true }).click();
  const dialog = page.getByRole("alertdialog", { name: `Roll back ${RELEASES_APP}` });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeInViewport();
  await expect(dialog.getByRole("button", { name: /^Go back to #\d+$/ })).toBeInViewport();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await settle(page);
  await expectNoA11yViolations(page, "the rollback dialog on a phone");
});
