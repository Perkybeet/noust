/**
 * The two deployment events 2.2 added (started, rolled back) and the console's public address
 * a deployment notification links back to, on Settings > Notifications against the real
 * backend. The address is checked by the configuration's own rule (absolute https), and its
 * refusal lands under the field in the server's words. The worker's machine is shared by
 * every spec it runs, so the test puts back what it changed.
 */

import type { Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";
import { confirmItsYou, stillness } from "./settings.helpers";

const NEW_EVENTS = [/^Deployment started/, /^Deployment rolled back/] as const;

function events(page: Page) {
  return page.getByRole("region", { name: "Events" });
}

function link(page: Page) {
  return page.getByRole("region", { name: "Link in notifications" });
}

test("turns the deployment started event on and the rolled back one off, and back", async ({ page, consoleServer, problems }) => {
  // Writing configuration asks "Confirm it's you" by answering 403 first, by design.
  problems.expect(/status of 403 .* \/api\/config$/);
  await signIn(page, consoleServer, "/settings/notifications");
  const section = events(page);
  const started = section.getByRole("checkbox", { name: NEW_EVENTS[0] });
  const rolledBack = section.getByRole("checkbox", { name: NEW_EVENTS[1] });
  // The defaults: a rollback is worth a message, every attempt starting is noise.
  await expect(started).not.toBeChecked();
  await expect(rolledBack).toBeChecked();
  // Both are sent by this version, unlike the certificate reminder.
  await expect(started).toHaveAccessibleDescription(/^A deploy, update or rollback began\./);
  await expect(rolledBack).toHaveAccessibleDescription("A new version failed its health check, and the previous one is serving again.");

  await started.click();
  await rolledBack.click();
  await expect(section.getByRole("button", { name: "Save changes" })).toBeEnabled();
  await stillness(page);
  await expectNoA11yViolations(page, "the events with unsaved changes");
  await section.getByRole("button", { name: "Save changes" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(section.getByRole("button", { name: "Save changes" })).toBeDisabled();

  const config = (await (await page.request.get("/api/config")).json()) as { config: { notifications: { events: Record<string, boolean> } } };
  expect(config.config.notifications.events.deploy_started).toBe(true);
  expect(config.config.notifications.events.deploy_rolled_back).toBe(false);

  await page.reload();
  await expect(events(page).getByRole("checkbox", { name: NEW_EVENTS[0] })).toBeChecked();
  await expect(events(page).getByRole("checkbox", { name: NEW_EVENTS[1] })).not.toBeChecked();

  // Leave the worker's server as it was.
  await events(page).getByRole("checkbox", { name: NEW_EVENTS[0] }).click();
  await events(page).getByRole("checkbox", { name: NEW_EVENTS[1] }).click();
  await events(page).getByRole("button", { name: "Save changes" }).click();
  await expect(events(page).getByRole("button", { name: "Save changes" })).toBeDisabled();
  await expect(events(page).getByRole("checkbox", { name: NEW_EVENTS[0] })).not.toBeChecked();
  await expect(events(page).getByRole("checkbox", { name: NEW_EVENTS[1] })).toBeChecked();
});

test("sets the console's public address, refusing one that is not https", async ({ page, consoleServer, problems }) => {
  problems.expect(/status of 403 .* \/api\/config$/);
  // The refused address is a 400 by design, shown under its field.
  problems.expect(/status of 400 .* \/api\/config$/);
  await signIn(page, consoleServer, "/settings/notifications");
  const section = link(page);
  const input = section.getByLabel("Console address");
  await expect(input).toHaveValue("");
  await settle(page);

  // http is refused by the configuration's own rule, in its words, under the field.
  await input.fill("http://console.cittek.es");
  await section.getByRole("button", { name: "Save changes" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(section.getByText(/web\.public_url must be an absolute https:\/\/ URL/)).toBeVisible();
  await expect(input).toHaveAttribute("aria-invalid", "true");
  await expect(input).toHaveAccessibleDescription(/must be an absolute https:\/\/ URL/);
  await stillness(page);
  await expectNoA11yViolations(page, "a refused public address");

  // An https address is saved without its trailing slash, and read back after a reload.
  await input.fill("https://console.cittek.es/");
  await section.getByRole("button", { name: "Save changes" }).click();
  await expect(section.getByRole("button", { name: "Save changes" })).toBeDisabled();
  await expect(input).not.toHaveAttribute("aria-invalid", "true");
  await page.reload();
  await expect(link(page).getByLabel("Console address")).toHaveValue("https://console.cittek.es");
  // The terminal equivalent names the key it reads.
  await expect(link(page).getByText("wasm config get web.public_url")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a saved public address");

  // Leave the worker's server as it was: empty means notifications carry no link.
  await link(page).getByLabel("Console address").fill("");
  await link(page).getByRole("button", { name: "Save changes" }).click();
  await expect(link(page).getByRole("button", { name: "Save changes" })).toBeDisabled();
  const config = (await (await page.request.get("/api/config")).json()) as { config: { web: { public_url?: string } } };
  expect(config.config.web.public_url ?? "").toBe("");
});
