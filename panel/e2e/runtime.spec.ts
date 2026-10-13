/**
 * The runtime pages against the real backend: where logs are shipped, the sandbox of a unit,
 * the processes beside the web process and the files kept for an application.
 *
 * Every page is also swept by axe, the layout-shift gate and the Spanish pass through
 * `routes.ts`; this holds what is said and done on them.
 */

import { confirmItsYou, expect, expectNoA11yViolations, settle, signIn, test, toasts } from "./fixtures";

const APP = "tienda.example.org";

test("log shipping adds a destination whose token is only written, lists it, and removes it", async ({ page, consoleServer, problems }) => {
  // Writing a destination and removing it are root-equivalent: each asks for sudo mode first.
  problems.expect(/status of 403 .*\/api\/log-drains\/e2e-grafana$/);
  await signIn(page, consoleServer, "/settings/log-shipping");
  await expect(page.getByRole("heading", { level: 2, name: "Log shipping" })).toBeVisible();
  await expect(page.getByText("No destinations")).toBeVisible();

  await page.getByRole("button", { name: "Add a destination" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a destination" });
  await dialog.getByRole("textbox", { name: "Name" }).fill("e2e-grafana");
  await dialog.getByRole("textbox", { name: "Address" }).fill("https://logs.example.com");
  await dialog.getByLabel(/Token or password/).fill("glc_e2e_secret_token");
  await expectNoA11yViolations(page, "the log destination dialog");

  const saved = page.waitForResponse(
    (response) => response.url().endsWith("/api/log-drains/e2e-grafana") && response.request().method() === "PUT" && response.status() !== 403,
  );
  await dialog.getByRole("button", { name: "Save destination" }).click();
  await confirmItsYou(page, consoleServer);
  const response = await saved;
  expect(response.status()).toBe(200);
  // The secret is written and never read back.
  const body = (await response.json()) as { has_secret: boolean };
  expect(body.has_secret).toBe(true);
  expect(JSON.stringify(body)).not.toContain("glc_e2e_secret_token");

  await expect(toasts(page).getByText("Saved e2e-grafana")).toBeVisible();
  const list = page.getByRole("list", { name: "Destinations of logs" });
  await expect(list.getByText("e2e-grafana", { exact: true })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "log shipping with a destination");

  await page.getByRole("button", { name: "Actions for e2e-grafana" }).click();
  await page.getByRole("menuitem", { name: "Remove" }).click();
  await page.getByRole("alertdialog", { name: "Stop shipping logs to e2e-grafana?" }).getByRole("button", { name: "Remove" }).click();
  await expect(page.getByText("No destinations")).toBeVisible();
});

test("an application's unit security says which sandbox it runs in and how exposed systemd finds it", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${APP}/settings/security`);
  await expect(page.getByRole("heading", { level: 2, name: "Unit security" })).toBeVisible();
  // The three profiles are choices of the page, with what each takes away.
  await expect(page.getByRole("radio", { name: /Default/ })).toBeVisible();
  await expect(page.getByRole("radio", { name: /Strict/ })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "unit security");
});

test("processes shows the copies of the web process and offers to add a background process", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${APP}/settings/processes`);
  await expect(page.getByRole("heading", { level: 2, name: "Processes" })).toBeVisible();
  await expect(page.getByText("Copies of the web process")).toBeVisible();

  await page.getByRole("button", { name: "Add a process" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a process" });
  await expect(dialog.getByRole("textbox", { name: "Name" })).toBeVisible();
  await expect(dialog.getByRole("textbox", { name: "Command" })).toBeVisible();
  await expectNoA11yViolations(page, "the add a process dialog");
});

test("the files of an application open from More actions, a folder at a time", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${APP}`);
  await page.getByRole("button", { name: "More actions" }).click();
  await page.getByRole("menuitem", { name: "Files" }).click();
  await expect(page).toHaveURL(new RegExp(`/apps/${APP.replaceAll(".", "\\.")}/files`));
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the files of an application");
});
