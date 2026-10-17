/**
 * What holds a deploy, against the real backend: an application's automatic deploys paused with a
 * reason and resumed, a freeze made, seen on the Deployments page and lifted, an update scheduled
 * and stopped, and the history and stop policy saved from the Deploys form. Each test leaves the
 * application as it found it, because both theme projects may run in the same worker.
 */

import type { Page } from "@playwright/test";

import { csrf } from "./apps-cleanup";
import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

const APP = "tienda.example.org";

/** A card of the subsection, by its title. */
function card(page: Page, title: string) {
  return page.locator("section").filter({ has: page.getByRole("heading", { name: title, exact: true }) }).last();
}

test("automatic deploys are paused with a reason and resumed", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${APP}/settings/deploys`);
  const auto = card(page, "Automatic deploys");
  await expect(auto.getByText("Automatic deploys are running")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "automatic deploys, running");

  await auto.getByRole("button", { name: "Pause" }).click();
  const dialog = page.getByRole("dialog", { name: `Pause automatic deploys of ${APP}?` });
  await dialog.getByRole("textbox", { name: /Reason/ }).fill("Looking into the checkout bug");
  await dialog.getByRole("button", { name: "Pause" }).click();
  await expect(auto.getByText("Automatic deploys are paused")).toBeVisible();
  await expect(auto.getByText("Reason: Looking into the checkout bug")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "automatic deploys, paused");

  // The pause is said where deploys are read, too.
  await page.goto(`/apps/${APP}/deployments`);
  await expect(page.getByText("Reason: Looking into the checkout bug")).toBeVisible();

  await page.goto(`/apps/${APP}/settings/deploys`);
  await card(page, "Automatic deploys").getByRole("button", { name: "Resume" }).click();
  await expect(card(page, "Automatic deploys").getByText("Automatic deploys are running")).toBeVisible();
});

test("a freeze needs its reason, is seen on the Deployments page and is lifted", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${APP}/settings/deploys`);
  const freezes = card(page, "Freeze deploys");
  await expect(freezes.getByText("No freeze is set for this app.")).toBeVisible();

  await freezes.getByRole("button", { name: "Freeze deploys" }).click();
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

  await page.goto(`/apps/${APP}/settings/deploys`);
  await card(page, "Freeze deploys").getByRole("button", { name: /Lift the freeze/ }).click();
  await page.getByRole("alertdialog", { name: "Lift the freeze?" }).getByRole("button", { name: "Lift the freeze" }).click();
  await expect(card(page, "Freeze deploys").getByText("No freeze is set for this app.")).toBeVisible();
});

test("an app is scheduled to update itself and stopped doing so", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${APP}/settings/deploys`);
  const schedule = card(page, "Scheduled updates");
  await expect(schedule.getByText("This app does not update itself")).toBeVisible();

  await schedule.getByRole("button", { name: "Schedule updates" }).click();
  await expect(schedule.getByRole("combobox", { name: "When" })).toHaveText(/Every Sunday at 03:00/);
  await schedule.getByRole("button", { name: "Save the schedule" }).click();
  await expect(schedule.getByText("This app updates itself")).toBeVisible();
  await expect(schedule.getByText("It has not run yet.")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a scheduled update");

  await schedule.getByRole("button", { name: "Stop updating by itself" }).click();
  await page.getByRole("alertdialog", { name: `Stop ${APP} updating itself?` }).getByRole("button", { name: "Stop" }).click();
  await expect(schedule.getByText("This app does not update itself")).toBeVisible();
});

test("the history kept and the stop policy are saved from the Deploys form, and put back", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${APP}/settings/deploys`);
  const limits = card(page, "History and stopping");
  const history = limits.getByRole("textbox", { name: "Deploys kept in history" });
  await expect(history).toHaveValue("");

  await history.fill("0");
  await page.getByRole("region", { name: "Unsaved changes" }).getByRole("button", { name: "Save" }).click();
  await expect(limits.getByText("A number from 1 to 1000, or empty for the server's.")).toBeVisible();

  await history.fill("55");
  await limits.getByRole("textbox", { name: "Time to stop" }).fill("120");
  await page.getByRole("region", { name: "Unsaved changes" }).getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("region", { name: "Unsaved changes" }).getByText("No unsaved changes")).toBeVisible();
  const saved = (await (await page.request.get(`/api/apps/${APP}/rollout`)).json()) as { history_keep: number | null; stop_timeout: number | null };
  expect(saved).toMatchObject({ history_keep: 55, stop_timeout: 120 });

  await history.fill("");
  await limits.getByRole("textbox", { name: "Time to stop" }).fill("");
  await page.getByRole("region", { name: "Unsaved changes" }).getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("region", { name: "Unsaved changes" }).getByText("No unsaved changes")).toBeVisible();
  const back = (await (await page.request.get(`/api/apps/${APP}/rollout`)).json()) as { history_keep: number | null; stop_timeout: number | null };
  expect(back).toMatchObject({ history_keep: null, stop_timeout: null });
});
