/**
 * Settings > About > Automatic updates against the real backend (checklist N-28): the section
 * reads the server's settings, the editor checks a time before asking, saving asks to confirm it is
 * you, and what was saved is what the server now reports.
 */

import { confirmItsYou, expect, expectNoA11yViolations, settle, signIn, test, toasts } from "./fixtures";

test("the automatic update is off, is set up in a drawer after confirming it is you, and is read back", async ({ page, consoleServer, problems }) => {
  // Saving it installs a timer as root, which asks to confirm it is you first.
  problems.expect(/status of 403 .* \/api\/system\/update\/auto$/);
  await signIn(page, consoleServer, "/settings/about");
  await settle(page);

  // A card, not a region: it is found by its heading.
  const section = page.getByRole("heading", { name: "Automatic updates", level: 3 }).locator("xpath=ancestor::section[1]");
  await expect(section.getByText("Off", { exact: true })).toBeVisible();
  await expectNoA11yViolations(page, "the automatic update, off");

  await section.getByRole("button", { name: "Set up" }).click();
  const drawer = page.getByRole("dialog", { name: "Automatic updates" });
  await expect(drawer.getByRole("radio", { name: "Patch releases" })).toBeChecked();
  await expectNoA11yViolations(page, "the automatic update's editor");

  // A time that is not one is caught before the server is asked.
  await drawer.getByLabel("From").fill("25:00");
  await drawer.getByRole("button", { name: "Save" }).click();
  await expect(drawer.getByText("Write a time as 03:00.")).toBeVisible();

  await drawer.getByRole("radio", { name: "Security fixes only" }).click();
  await drawer.getByLabel("From").fill("23:00");
  await drawer.getByLabel("To").fill("01:00");
  await expect(drawer.getByText(/Ends the next day\./)).toBeVisible();
  await drawer.getByRole("checkbox", { name: "Every day" }).click();
  await drawer.getByRole("checkbox", { name: "Monday" }).click();
  await drawer.getByRole("checkbox", { name: "Thursday" }).click();
  await drawer.getByRole("button", { name: "Save" }).click();
  await confirmItsYou(page, consoleServer);

  await expect(toasts(page).getByText("Automatic updates saved")).toBeVisible();
  await expect(drawer).toBeHidden();
  // What the server now holds, read back: the choices, and the window said as crossing midnight.
  await expect(section.getByText("Security fixes only")).toBeVisible();
  await expect(section.getByText("23:00-01:00, into the next day")).toBeVisible();
  await expect(section.getByText("Monday, Thursday", { exact: true })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the automatic update, set up");
});
