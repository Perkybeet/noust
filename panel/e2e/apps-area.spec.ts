/**
 * The Applications area, walked as the tasks of its mockup (4.2, F3-APPS; `docs/superpowers/mockups/4.2/tanda-3`,
 * "Los recorridos"), each in both themes against the real backend, with the CSP and console gates of the `problems`
 * fixture and axe on every screen:
 *
 * - read the list three ways from its column: all of it, by project and by state, and the deployments of every application;
 * - deploy a new application and give it its domain: five ways to start in one page, the name and address, the
 *   review, and the first deployment followed on the application's own page;
 * - see why an application fails: the line on its summary, and "See why" leading to the cause;
 * - export it to take it to another server: Export and declaration, no longer behind Delete.
 */

import path from "node:path";

import { forgetApp } from "./apps-cleanup";
import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";
import { continueTo, fillStorefrontVariables, inspectSource, wizardSource } from "./wizard-sources";

/** Its unit is failed: systemd gave up restarting it. */
const FAILED = "clientes.example.com";
/** On releases and running. */
const SERVING = "tienda.example.org";

test("an application that fails says so on its summary, with the line of the error and the way to why", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer, `/apps/${FAILED}`);
  const why = page.getByRole("link", { name: "See why" });
  await expect(why).toBeVisible();
  await expect(why).toBeInViewport();
  await settle(page);
  await expectNoA11yViolations(page, "the summary of an application that fails");

  await why.click();
  await expect(page).toHaveURL(new RegExp(`/apps/${FAILED.replace(/\./g, "\\.")}/diagnose$`));
  await expect(page.getByRole("heading", { level: 2, name: /^Verdict:/ })).toBeVisible();
  // The cause is read first, and the way to act on it beside it.
  await expect(page.getByText("Most likely", { exact: true })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "why an application fails");
});

test("exporting is in the column beside Several servers, not behind Delete, and downloads the application as a file", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer, `/apps/${SERVING}`);
  const column = page.getByRole("navigation", { name: "Application sections" });
  // "Move elsewhere" is a group of its own, and Delete is alone under Danger.
  await expect(column.getByRole("link", { name: "Export and declaration" })).toBeVisible();
  await expect(column.getByRole("link", { name: "Several servers" })).toBeVisible();
  await column.getByRole("link", { name: "Export and declaration" }).click();
  await expect(page).toHaveURL(new RegExp(`/apps/${SERVING.replace(/\./g, "\\.")}/export$`));

  // Nothing here changes the application: the file is what `noust app import` reads on another server.
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export application" }).click();
  expect((await download).suggestedFilename()).toBe(`${SERVING}.noust-app.json`);
  await settle(page);
  await expectNoA11yViolations(page, "Export and declaration");

  await column.getByRole("link", { name: "Delete" }).click();
  await expect(page).toHaveURL(new RegExp(`/apps/${SERVING.replace(/\./g, "\\.")}/delete$`));
  // Deleting is alone at the end: it offers no export of its own.
  await expect(page.getByRole("button", { name: "Export application" })).toHaveCount(0);
  await settle(page);
  await expectNoA11yViolations(page, "Delete, alone under Danger");
});

test("a new application: five ways on one page, its name and address, and its first deploy followed on its own page", async ({ page, consoleServer, problems }, testInfo) => {
  // The deploy runs to its end (a failed health check: nothing listens in the sandbox) before the test lets go of the machine.
  test.setTimeout(180_000);
  const domain = "reservas.example.net";
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer, "/apps");
  // One way to start: the same page from the list's button.
  await page.getByRole("link", { name: "New application" }).click();
  await expect(page).toHaveURL(/\/apps\/new$/);

  const ways = page.getByRole("radiogroup", { name: "Where it comes from" });
  await expect(ways.getByRole("radio")).toHaveText([/From a repository/, /From the catalog/, /A container image/, /Upload files/, /Something that already runs here/]);
  await expect(ways.getByRole("radio", { name: /From a repository/ })).toBeChecked();
  await settle(page);
  await expectNoA11yViolations(page, "the five ways to start");

  // Adopting is one of the five, with its own steps, and builds nothing.
  await ways.getByRole("radio", { name: /Something that already runs here/ }).click();
  await expect(page.getByText(/Adopting changes nothing of what runs/)).toBeVisible();
  await expect(page.getByRole("radio", { name: "Running on this server" })).toBeChecked();
  await settle(page);
  await expectNoA11yViolations(page, "adopting, as one of the ways to start");
  await ways.getByRole("radio", { name: /From a repository/ }).click();

  await inspectSource(page, consoleServer, problems, await wizardSource(page, "storefront"));
  await page.getByLabel("Domain", { exact: true }).fill(domain);
  // Checked as it is typed: example.net is a seeded zone, so this name resolves here.
  await expect(page.getByText(`${domain} points here`)).toBeVisible();
  await continueTo(page, "Variables");
  await fillStorefrontVariables(page);
  await continueTo(page, "Deploy");
  await expect(page.getByText(`https://${domain}`)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the review before deploying");

  await page.getByRole("button", { name: `Deploy ${domain}` }).click();
  // The application's own page, not the deployment's: it says it is deploying for the first time, and follows the build.
  await expect(page).toHaveURL(new RegExp(`/apps/${domain.replace(/\./g, "\\.")}$`), { timeout: 30_000 });
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(domain);
  await expect(page.getByRole("heading", { level: 2, name: "Deploying for the first time" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the first deploy of an application");
  // For review, when asked for (`NOUST_APPS_SCREENS=/tmp/apps-screens`): the page while it follows the build.
  if (process.env.NOUST_APPS_SCREENS) {
    await page.screenshot({ path: path.join(process.env.NOUST_APPS_SCREENS, testInfo.project.name, "app-first-deploy-1440.png") });
  }

  await forgetApp(page, consoleServer, domain);
});

test("the column reads the list as one table, by project, by state, and the deployments of all", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer, "/apps");
  const column = page.getByRole("navigation", { name: /^Applications sections?$/ });
  await expect(column.getByRole("link", { name: /^All/ })).toHaveAttribute("aria-current", "page");
  const table = page.getByRole("region", { name: "Applications" });
  await expect(table.getByRole("link", { name: "shop.example.net" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the list of applications");

  await column.getByRole("link", { name: "By state" }).click();
  await expect(page).toHaveURL(/\/apps\?view=states$/);
  await expect(page.getByRole("heading", { level: 1, name: "Applications by state" })).toBeVisible();
  // What needs a person leads, and the same table is under each heading.
  await expect(page.getByRole("heading", { level: 2 }).first()).toHaveText("Needs attention");
  await expect(page.getByRole("link", { name: FAILED, exact: true })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the applications by state");

  await column.getByRole("link", { name: "By project" }).click();
  await expect(page).toHaveURL(/\/apps\?view=projects$/);
  await expect(page.getByRole("heading", { level: 1, name: "Applications by project" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open the project" }).first()).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the applications by project");

  await column.getByRole("link", { name: "Deployments of all" }).click();
  await expect(page).toHaveURL(/\/apps\?view=deployments$/);
  await expect(page.getByRole("heading", { level: 1, name: "Deployments of all applications" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Deployments of every application, newest first" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the deployments of every application");
});
