/**
 * The network pages against the real backend: the Domains page's DNS, Tunnels and Ports tabs are
 * one click apart and each says what is missing in the server's own words (the sandbox has no
 * DNS account, no cloudflared and an nginx without the stream module), and an application's
 * Access settings are two clicks from its Settings. Runs in both themes, with the CSP and
 * console gates of the `problems` fixture and axe on every state.
 */

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

test("the DNS, Tunnels and Ports tabs are one click from the certificates, and each says what it holds", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/domains");
  const tabs = page.getByRole("navigation", { name: "Domains and certificates sections" });
  await expect(tabs.getByRole("link", { name: /^Certificates/ })).toHaveAttribute("aria-current", "page");

  await tabs.getByRole("link", { name: /^DNS/ }).click();
  await expect(page).toHaveURL(/\/domains\/dns$/);
  await expect(page.getByText("No DNS accounts yet")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the DNS tab");

  await tabs.getByRole("link", { name: /^Tunnels/ }).click();
  await expect(page).toHaveURL(/\/domains\/tunnels$/);
  // The sandbox has no cloudflared: the page says so, and offers no way to create a tunnel.
  await expect(page.getByText("cloudflared is not installed")).toBeVisible();
  await expect(page.getByRole("button", { name: "Create a tunnel" }).first()).toBeDisabled();
  await settle(page);
  await expectNoA11yViolations(page, "the Tunnels tab");

  await tabs.getByRole("link", { name: /^Ports/ }).click();
  await expect(page).toHaveURL(/\/domains\/ports$/);
  await expect(page.getByRole("button", { name: "Publish a port" }).first()).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the Ports tab");
});

test("an application's Access settings are two clicks from its Settings", async ({ page, consoleServer }) => {
  const app = "tienda.example.org";
  await signIn(page, consoleServer, `/apps/${app}`);
  await page.getByRole("navigation", { name: "Application sections" }).getByRole("link", { name: "Settings" }).click();
  await page.getByRole("navigation", { name: "Application settings" }).first().getByRole("link", { name: "Access" }).click();
  await expect(page).toHaveURL(new RegExp(`/apps/${app.replace(/\./g, "\\.")}/settings/access$`));
  await expect(page.getByRole("heading", { level: 2, name: "Access" })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /Ask for a password/ })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the Access settings");
});

test("the private certificate authority is one menu away from the certificates, and says what it is before it is made", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/domains");
  await page.getByRole("button", { name: "More actions" }).click();
  await page.getByRole("menuitem", { name: "Private authority" }).click();

  const dialog = page.getByRole("dialog", { name: "Private certificate authority" });
  await expect(dialog.getByText("This server has no private authority yet")).toBeVisible();
  await expect(dialog.getByLabel(/More domains it may sign for/)).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Create authority" })).toBeEnabled();
  await settle(page);
  await expectNoA11yViolations(page, "the private authority dialog, before it is made");

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});
