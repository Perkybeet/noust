/**
 * Settings > Security > Compliance against the real backend.
 *
 * - Nothing is on until it is chosen: the frameworks are listed off, with one line on what each is,
 *   and the section never says a server complies.
 * - A framework is turned on after the dialog lists the controls it will change and the clause each
 *   one serves; the controls it asks for are then locked with the framework's name, and turning it off
 *   is a dangerous confirmation behind "Confirm it's you".
 * - The page passes axe with the section open, in the dialog and in both themes.
 */

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";
import { confirmItsYou, stillness, toastSaying } from "./settings.helpers";

test("frameworks are off by default, turn on after listing what changes, and lock the controls they ask for", async ({
  page,
  consoleServer,
  problems,
}) => {
  // Turning one off asks "Confirm it's you" by answering 403 first, by design.
  problems.expect(/status of 403 .* \/api\/compliance$/);
  await signIn(page, consoleServer, "/settings/security");
  const section = page.getByRole("region", { name: "Compliance" });
  await expect(section.getByRole("heading", { level: 3, name: "Frameworks" })).toBeVisible();

  for (const name of ["ENS BÁSICA", "ENS MEDIA", "ENS ALTA", "ISO/IEC 27001:2022", "NIS2", "CIS Controls v8, IG1", "CIS Controls v8, IG2", "SOC 2, Security", "GDPR / RGPD, article 32"]) {
    await expect(section.getByRole("heading", { level: 3, name })).toBeVisible();
  }
  await expect(section).toContainText("It does not make a server compliant");
  await expect(section).not.toContainText(/is compliant|are compliant/);

  // The framework's card is the innermost section holding its heading: its action sits in the card's
  // header, beside the div that holds the heading, so a div around the heading would miss the button.
  const card = section.locator("section").filter({ has: page.getByRole("heading", { level: 3, name: "NIS2" }) }).last();
  await card.getByRole("button", { name: "Turn on…" }).click();
  const dialog = page.getByRole("dialog", { name: "Turn on NIS2" });
  await expect(dialog.getByText("Encrypted backups")).toBeVisible();
  await expect(dialog.getByText("Art. 21(2)(h)").first()).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "the dialog that turns a framework on");
  await dialog.getByRole("button", { name: "Turn on" }).click();
  await expect(toastSaying(page, "NIS2 is on")).toBeVisible();

  const locked = section.getByRole("switch", { name: "Restricted API tokens" });
  await expect(locked).toBeDisabled();
  await expect(section.getByText("Required by NIS2").first()).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the compliance section with a framework on");

  // Turning it off puts the server back as it was, behind "Confirm it's you".
  await card.getByRole("button", { name: "Turn off…" }).click();
  const off = page.getByRole("dialog", { name: "Turn off NIS2" });
  await expect(off.getByText(/Protection goes down/)).toBeVisible();
  await off.getByRole("button", { name: "Turn off" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(toastSaying(page, "NIS2 is off")).toBeVisible();
  await expect(section.getByRole("switch", { name: "Restricted API tokens" })).toBeEnabled();
});
