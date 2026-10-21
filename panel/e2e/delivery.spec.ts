/**
 * What holds a deploy, against the real backend, on the Automation page: an application's automatic
 * deploys paused with a reason and resumed from the same switch, a freeze made, seen on the
 * Deployments page and lifted, an update scheduled and stopped from the drawer, and the history and
 * stop policy saved from the deploy limits drawer. Each test leaves the application as it found it,
 * because both theme projects may run in the same worker.
 */

import type { Page } from "@playwright/test";

import { csrf } from "./apps-cleanup";
import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

const APP = "tienda.example.org";

/** One row of the page, by its label. */
function row(page: Page, label: string) {
  return page.locator('[data-slot="setting"]').filter({ has: page.getByText(label, { exact: true }) }).first();
}

test("automatic deploys are paused with a reason and resumed from the same switch", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${APP}/settings/automation`);
  const toggle = page.getByRole("switch", { name: "Automatic deploys", exact: true });
  await expect(toggle).toBeChecked();
  await expect(page.getByText("Paused", { exact: true })).toHaveCount(0);
  await settle(page);
  await expectNoA11yViolations(page, "automatic deploys, running");

  await toggle.click();
  const dialog = page.getByRole("dialog", { name: `Pause automatic deploys of ${APP}?` });
  await dialog.getByRole("textbox", { name: /Reason/ }).fill("Looking into the checkout bug");
  await dialog.getByRole("button", { name: "Pause" }).click();
  await expect(toggle).not.toBeChecked();
  await expect(page.getByText("Paused", { exact: true })).toBeVisible();
  await expect(page.getByText(/Reason: Looking into the checkout bug/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "automatic deploys, paused");

  // The pause is said where deploys are read, too.
  await page.goto(`/apps/${APP}/deployments`);
  await expect(page.getByText("Reason: Looking into the checkout bug")).toBeVisible();

  await page.goto(`/apps/${APP}/settings/automation`);
  await page.getByRole("switch", { name: "Automatic deploys", exact: true }).click();
  await expect(page.getByRole("switch", { name: "Automatic deploys", exact: true })).toBeChecked();
  await expect(page.getByText("Paused", { exact: true })).toHaveCount(0);
});

test("a freeze needs its reason, is seen on the Deployments page and is lifted", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${APP}/settings/automation`);
  const freezes = page.getByRole("list", { name: "Freeze deploys" });
  await expect(freezes).toHaveCount(0);

  await page.getByRole("button", { name: "Freeze deploys…" }).click();
  const dialog = page.getByRole("dialog", { name: `Freeze deploys of ${APP}` });
  await dialog.getByRole("button", { name: "Freeze" }).click();
  await expect(dialog.getByText("Say why: a person who is refused is told.")).toBeVisible();
  await dialog.getByRole("textbox", { name: /Reason/ }).fill("Black Friday: nothing ships");
  await dialog.getByRole("button", { name: "Freeze" }).click();
  await expect(freezes.getByText("Black Friday: nothing ships")).toBeVisible();
  await expect(freezes.getByText("Frozen now")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a freeze in force");

  await page.goto(`/apps/${APP}/deployments`);
  await expect(page.getByText(`Deploys of ${APP} are frozen`)).toBeVisible();
  await expect(page.getByText(/Reason: Black Friday: nothing ships/)).toBeVisible();

  // An update during the freeze is refused with its reason, and goes ahead only when told to.
  const response = await page.request.post("/api/jobs/update", { data: { domain: APP }, headers: await csrf(page) });
  expect(response.status()).toBe(409);
  expect(((await response.json()) as { error: string }).error).toBe("deploys_frozen");

  await page.goto(`/apps/${APP}/settings/automation`);
  await page.getByRole("button", { name: /Lift the freeze/ }).click();
  await page.getByRole("alertdialog", { name: "Lift the freeze?" }).getByRole("button", { name: "Lift the freeze" }).click();
  await expect(page.getByRole("list", { name: "Freeze deploys" })).toHaveCount(0);
});

test("an app is scheduled to update itself and stopped doing so", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${APP}/settings/automation`);
  const schedule = row(page, "Scheduled updates");
  await expect(schedule.getByText("On", { exact: true })).toHaveCount(0);

  await schedule.getByRole("button", { name: "Set an update schedule…" }).click();
  const drawer = page.getByRole("dialog", { name: `Update schedule of ${APP}` });
  await expect(drawer.getByRole("combobox", { name: "When" })).toHaveText(/Every Sunday at 03:00/);
  await drawer.getByRole("button", { name: "Save the schedule" }).click();
  await expect(drawer).toBeHidden();
  await expect(schedule.getByText("On", { exact: true })).toBeVisible();
  await expect(schedule.getByText("It has not run yet.")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a scheduled update");

  await schedule.getByRole("button", { name: "Edit schedule…" }).click();
  await page.getByRole("dialog", { name: `Update schedule of ${APP}` }).getByRole("button", { name: "Stop updating by itself" }).click();
  await page.getByRole("alertdialog", { name: `Stop ${APP} updating itself?` }).getByRole("button", { name: "Stop" }).click();
  await expect(schedule.getByRole("button", { name: "Set an update schedule…" })).toBeVisible();
  await expect(schedule.getByText("On", { exact: true })).toHaveCount(0);
});

test("the history kept and the stop policy are saved from the deploy limits drawer, and put back", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${APP}/settings/automation`);
  await page.getByRole("button", { name: "Edit deploy limits…" }).click();
  const drawer = page.getByRole("dialog", { name: "Deploy limits" });
  const history = drawer.getByRole("textbox", { name: "Deploys kept in history" });
  await expect(history).toHaveValue("");

  await history.fill("0");
  await drawer.getByRole("button", { name: "Save the limits" }).click();
  await expect(drawer.getByText("A number from 1 to 1000, or empty for the server's.")).toBeVisible();

  await history.fill("55");
  await drawer.getByRole("textbox", { name: "Time to stop" }).fill("120");
  await drawer.getByRole("button", { name: "Save the limits" }).click();
  await expect(drawer).toBeHidden();
  const saved = (await (await page.request.get(`/api/apps/${APP}/rollout`)).json()) as { history_keep: number | null; stop_timeout: number | null };
  expect(saved).toMatchObject({ history_keep: 55, stop_timeout: 120 });

  await page.getByRole("button", { name: "Edit deploy limits…" }).click();
  const again = page.getByRole("dialog", { name: "Deploy limits" });
  await again.getByRole("textbox", { name: "Deploys kept in history" }).fill("");
  await again.getByRole("textbox", { name: "Time to stop" }).fill("");
  await again.getByRole("button", { name: "Save the limits" }).click();
  await expect(again).toBeHidden();
  const back = (await (await page.request.get(`/api/apps/${APP}/rollout`)).json()) as { history_keep: number | null; stop_timeout: number | null };
  expect(back).toMatchObject({ history_keep: null, stop_timeout: null });
});
