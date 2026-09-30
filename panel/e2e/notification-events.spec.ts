/**
 * What is sent, on Settings > Notifications against the real backend: every event by area,
 * folded to a line that counts what is sent, the ones that ship off marked; the events and the
 * language saved together from the one save bar; and the console's public address (General),
 * which a notification links back to, refused by the configuration's own rule when it is not
 * https. The worker's machine is shared by every spec it runs, so each test puts back what it
 * changed.
 */

import type { Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";
import { confirmItsYou, stillness } from "./settings.helpers";

function saveBar(page: Page) {
  return page.getByRole("region", { name: "Unsaved changes" });
}

function group(page: Page, title: string) {
  return page.locator("details").filter({ has: page.locator("summary", { hasText: title }) });
}

async function storedEvents(page: Page): Promise<{ events: Record<string, boolean>; language?: string }> {
  const body = (await (await page.request.get("/api/config")).json()) as { config: { notifications: { events: Record<string, boolean>; language?: string } } };
  return body.config.notifications;
}

test("turns the deploy started event on and the rolled back one off, with the language, and back", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/settings/notifications");
  const deploys = group(page, "Deploys");
  await expect(deploys.locator("summary")).toContainText("3 of 4 sent");
  await deploys.locator("summary").click();
  const started = deploys.getByRole("checkbox", { name: /^Deploy started/ });
  const rolledBack = deploys.getByRole("checkbox", { name: /^Deploy rolled back/ });
  // The defaults: a rollback is worth a message, every attempt starting is noise - and says so.
  await expect(started).not.toBeChecked();
  await expect(deploys.getByText("Off by default")).toBeVisible();
  await expect(rolledBack).toBeChecked();
  await expect(rolledBack).toHaveAccessibleDescription("A new version did not start, and the previous one serves again.");

  await started.click();
  await rolledBack.click();
  await page.getByRole("radio", { name: "Español" }).click();
  await expect(saveBar(page).getByText("3 unsaved changes")).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "the events with unsaved changes");
  await saveBar(page).getByRole("button", { name: "Save" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();

  const stored = await storedEvents(page);
  expect(stored.events.deploy_started).toBe(true);
  expect(stored.events.deploy_rolled_back).toBe(false);
  expect(stored.events.backup_success).toBe(false);
  expect(stored.language).toBe("es");

  await page.reload();
  await expect(group(page, "Deploys").locator("summary")).toContainText("3 of 4 sent");
  await group(page, "Deploys").locator("summary").click();
  await expect(group(page, "Deploys").getByRole("checkbox", { name: /^Deploy started/ })).toBeChecked();

  // Leave the worker's server as it was.
  await group(page, "Deploys").getByRole("checkbox", { name: /^Deploy started/ }).click();
  await group(page, "Deploys").getByRole("checkbox", { name: /^Deploy rolled back/ }).click();
  await page.getByRole("radio", { name: "English" }).click();
  await saveBar(page).getByRole("button", { name: "Save" }).click();
  await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();
  const restored = await storedEvents(page);
  expect(restored.events.deploy_started).toBe(false);
  expect(restored.events.deploy_rolled_back).toBe(true);
  expect(restored.language).toBe("en");
});

test("the console's public address is refused unless https, and notifications then link to it", async ({ page, consoleServer, problems }) => {
  // The refused address is a 400 by design, shown under its field.
  problems.expect(/status of 400 .* \/api\/config$/);
  await signIn(page, consoleServer, "/settings");
  const input = page.getByLabel("Console address");
  await expect(input).toHaveValue("");
  await settle(page);

  await input.fill("http://console.example.org");
  await saveBar(page).getByRole("button", { name: "Save" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(page.getByText(/web\.public_url must be an absolute https:\/\/ URL/)).toBeVisible();
  await expect(input).toHaveAttribute("aria-invalid", "true");
  await expect(input).toHaveAccessibleDescription(/must be an absolute https:\/\/ URL/);
  await stillness(page);
  await expectNoA11yViolations(page, "a refused public address");

  // An https address is saved without its trailing slash, and Notifications says it links there.
  await input.fill("https://console.example.org/");
  await saveBar(page).getByRole("button", { name: "Save" }).click();
  await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();
  await page.goto("/settings/notifications");
  await expect(page.getByText("Messages link to")).toContainText("https://console.example.org");

  // Leave the worker's server as it was: empty means notifications carry no link.
  await page.goto("/settings");
  await page.getByLabel("Console address").fill("");
  await saveBar(page).getByRole("button", { name: "Save" }).click();
  await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();
  const config = (await (await page.request.get("/api/config")).json()) as { config: { web: { public_url?: string } } };
  expect(config.config.web.public_url ?? "").toBe("");
});
