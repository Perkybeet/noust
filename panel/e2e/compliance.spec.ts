/**
 * Settings > Compliance against the real backend.
 *
 * - Nothing is on until it is chosen, and with none on nothing is red: the page says it as a fact, lists the nine frameworks
 *   the server offers, off, with one line on what each is, and never says a server complies.
 * - A framework is turned on after the dialog lists the controls it will change and the clause each one serves; once it is
 *   on the page is its report; the controls it asks for are locked with the framework's name, and turning it off is a
 *   dangerous confirmation behind "Confirm it's you".
 * - The page passes axe in both themes, with nothing on, in the dialog and with a framework on.
 */

import type { Locator } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";
import { confirmItsYou, stillness, toastSaying } from "./settings.helpers";

/** The controls are folded by group, each to a line that says how many are strict: a person opens the group they want. */
async function openControlGroups(section: Locator): Promise<void> {
  for (const name of [/^Access and accounts/, /^Accountability and the audit trail/, /^Backups/, /^Network and host/]) {
    const group = section.getByRole("button", { name });
    if ((await group.count()) > 0 && (await group.getAttribute("aria-expanded")) === "false") await group.click();
  }
}

test("frameworks are off by default, turn on after listing what changes, and lock the controls they ask for", async ({
  page,
  consoleServer,
  problems,
}) => {
  // Turning one off asks "Confirm it's you" by answering 403 first, by design.
  problems.expect(/status of 403 .* \/api\/compliance$/);
  await signIn(page, consoleServer, "/settings/compliance");
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { level: 2, name: "No framework is on" })).toBeVisible();

  for (const name of ["ENS BÁSICA", "ENS MEDIA", "ENS ALTA", "ISO/IEC 27001:2022", "NIS2", "CIS Controls v8, IG1", "CIS Controls v8, IG2", "SOC 2, Security", "GDPR / RGPD, article 32"]) {
    await expect(main.getByRole("heading", { level: 3, name })).toBeVisible();
  }
  // The honest half is one press away, in the "?", and no word says a server complies.
  await page.getByRole("button", { name: "About compliance" }).click();
  await expect(page.getByText(/It does not make a server compliant/)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(main).not.toContainText(/is compliant|are compliant/);
  // With none on there is nothing in red.
  await expect(page.locator('main [data-state="failed"], main [data-state="warning"]')).toHaveCount(0);
  await settle(page);
  await expectNoA11yViolations(page, "Compliance with no framework on");

  const card = page.locator("article").filter({ has: page.getByRole("heading", { level: 3, name: "NIS2" }) });
  await card.getByRole("button", { name: "Turn on…" }).click();
  const dialog = page.getByRole("alertdialog", { name: "Turn on NIS2" });
  await expect(dialog.getByText("Encrypted backups")).toBeVisible();
  await expect(dialog.getByText("Art. 21(2)(h)").first()).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "the dialog that turns a framework on");
  await dialog.getByRole("button", { name: "Turn on" }).click();
  await expect(toastSaying(page, "NIS2 is on")).toBeVisible();

  // The page is its report now, and the controls it asks for are locked with its name.
  const report = page.locator("section").filter({ has: page.getByRole("heading", { level: 2, name: "NIS2" }) }).last();
  await expect(report.getByText(/controls in force|The control is in force/)).toBeVisible();
  const controls = page.locator("section").filter({ has: page.getByRole("heading", { level: 2, name: "Controls" }) }).last();
  await openControlGroups(controls);
  const locked = controls.getByRole("switch", { name: "Restricted API tokens" });
  await expect(locked).toBeDisabled();
  await expect(controls.getByText("Required by NIS2").first()).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "Compliance with a framework on");

  // Turning it off puts the server back as it was, behind "Confirm it's you".
  await report.getByRole("button", { name: "Turn off…" }).click();
  const off = page.getByRole("alertdialog", { name: "Turn off NIS2" });
  await expect(off.getByText(/Protection goes down/)).toBeVisible();
  await off.getByRole("button", { name: "Turn off" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(toastSaying(page, "NIS2 is off")).toBeVisible();
  await expect(main.getByRole("heading", { level: 2, name: "No framework is on" })).toBeVisible();
  await expect(controls.getByRole("switch", { name: "Restricted API tokens" })).toBeEnabled();
});
