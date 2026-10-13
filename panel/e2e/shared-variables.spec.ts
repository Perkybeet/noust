/**
 * Shared variables, secret managers, references, who gets a variable, tags and cloning, against
 * the real backend (checklist B-43 to B-48, B-79, B-80).
 *
 * The tests change the one seeded server of their worker, so they run in order: a variable is
 * defined in a scope, then referred to from an application's environment, and the tag update
 * that queues real jobs is last.
 */

import type { Page } from "@playwright/test";

import { confirmItsYou, expect, expectNoA11yViolations, signIn, stillness, test } from "./fixtures";

test.describe.configure({ mode: "serial" });

async function chooseOption(page: Page, name: string, option: string): Promise<void> {
  await page.getByRole("combobox", { name, exact: true }).click();
  await page.getByRole("option", { name: option, exact: true }).click();
}

test("a team's shared variables are added, hidden, revealed and deleted", async ({ page, consoleServer, problems }) => {
  // The first write is answered with the question; the call is sent again once it is answered.
  problems.expect(/status of 403 .* \/api\/teams\/default\/variables\/REGION$/);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await signIn(page, consoleServer, "/teams/default/variables");
  await expect(page.getByRole("heading", { level: 2, name: "Team variables", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "No shared variables", exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Add variable" }).click();
  let dialog = page.getByRole("dialog", { name: "Add a shared variable" });
  await dialog.getByLabel("Name").fill("REGION");
  await dialog.getByLabel("Value").fill("eu-west");
  await dialog.getByRole("button", { name: "Add variable" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(dialog).toBeHidden();
  const table = page.getByRole("table", { name: "Shared variables of Default" });
  await expect(table.getByText("REGION", { exact: true })).toBeVisible();
  await expect(table.getByText("eu-west", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Add variable" }).click();
  dialog = page.getByRole("dialog", { name: "Add a shared variable" });
  await dialog.getByLabel("Name").fill("STRIPE_SECRET");
  await dialog.getByLabel("Value").fill("sk_live_not_a_real_key_0123456789");
  await chooseOption(page, "Is it a secret?", "Secret");
  await chooseOption(page, "Given to", "Runtime only");
  await dialog.getByRole("button", { name: "Add variable" }).click();
  await expect(dialog).toBeHidden();
  await expect(table.getByText("Runtime only", { exact: true })).toBeVisible();

  // A secret is hidden until revealed, and revealed only by reading it in clear.
  await expect(table.getByText("sk_live_not_a_real_key_0123456789")).toHaveCount(0);
  await table.getByRole("button", { name: "Reveal the value of STRIPE_SECRET" }).click();
  await expect(table.getByText("sk_live_not_a_real_key_0123456789", { exact: true })).toBeVisible();
  await table.getByRole("button", { name: "Hide the value of STRIPE_SECRET" }).click();
  await expect(table.getByText("sk_live_not_a_real_key_0123456789")).toHaveCount(0);
  await stillness(page);
  await expectNoA11yViolations(page, "a team's shared variables");

  await table.getByRole("button", { name: "Actions for REGION" }).click();
  await page.getByRole("menuitem", { name: "Delete" }).click();
  const confirm = page.getByRole("alertdialog", { name: "Delete REGION?" });
  await confirm.getByRole("button", { name: "Delete variable" }).click();
  await expect(table.getByText("REGION", { exact: true })).toHaveCount(0);
  await expect(table.getByText("STRIPE_SECRET", { exact: true })).toBeVisible();
});

test("a project's page holds the project's and the environment's variables", async ({ page, consoleServer, problems }) => {
  problems.expect(/status of 403 .* \/api\/teams\/default\/projects\/default\/variables\/DOCS_URL$/);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await signIn(page, consoleServer, "/projects/default/default");
  await expect(page.getByRole("heading", { level: 2, name: "Project variables", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: "Variables of Production", exact: true })).toBeVisible();

  const project = page.getByRole("region", { name: "Project variables", exact: true });
  await project.getByRole("button", { name: "Add variable" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a shared variable" });
  await dialog.getByLabel("Name").fill("DOCS_URL");
  await dialog.getByLabel("Value").fill("https://docs.example.org");
  await dialog.getByRole("button", { name: "Add variable" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(dialog).toBeHidden();
  await expect(project.getByText("DOCS_URL", { exact: true })).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "a project's variables");
});

test("the server's variables are referred to from an application's environment", async ({ page, consoleServer, problems }) => {
  problems.expect(/status of 403 .* \/api\/variables\/server\/REGION$/);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await signIn(page, consoleServer, "/settings/variables");
  await expect(page.getByRole("heading", { level: 2, name: "Server variables", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Add variable" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a shared variable" });
  await dialog.getByLabel("Name").fill("REGION");
  await dialog.getByLabel("Value").fill("eu-west");
  await dialog.getByRole("button", { name: "Add variable" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("table", { name: "Shared variables of this server" }).getByText("REGION", { exact: true })).toBeVisible();

  // The application refers to it; the file keeps the reference and holds what it resolved to.
  await page.goto("/apps/blog.example.org/environment");
  await page.getByRole("button", { name: "Add variable" }).click();
  const variable = page.getByRole("dialog", { name: "Add a variable" });
  await variable.getByLabel("Name").fill("SITE_REGION");
  await variable.getByLabel("Value").fill("{{server.REGION}}");
  await chooseOption(page, "Given to", "Build only");
  await variable.getByRole("button", { name: "Add variable" }).click();
  await page.getByRole("button", { name: "Review and save" }).click();
  const review = page.getByRole("dialog", { name: "Review changes" });
  await expect(review.getByText("Save changes", { exact: true })).toBeVisible();
  const saved = page.waitForResponse((r) => r.url().endsWith("/api/apps/blog.example.org/env") && r.request().method() === "PUT");
  await review.getByRole("button", { name: "Save changes" }).click();
  expect((await saved).status()).toBe(200);
  await page.getByRole("dialog", { name: "Environment saved" }).getByRole("button", { name: "Restart later" }).click();

  const table = page.getByRole("table", { name: "Environment variables of blog.example.org" });
  await expect(table.getByText("{{server.REGION}}", { exact: true })).toBeVisible();
  await expect(table.getByText("Build only", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Resolve references" })).toBeVisible();
  // What Noust itself gives the application is apart from the file's own variables.
  const given = page.getByRole("region", { name: "Given by Noust" });
  await expect(given.getByText("NOUST_DOMAIN", { exact: true })).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "an environment with a reference");

  // The reference survives a reload as written.
  await page.reload();
  await expect(page.getByRole("table", { name: "Environment variables of blog.example.org" }).getByText("{{server.REGION}}", { exact: true })).toBeVisible();
});

test("a secret manager is added and removed, its token never shown", async ({ page, consoleServer, problems }) => {
  problems.expect(/status of 403 .* \/api\/secret-providers\/doppler$/);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await signIn(page, consoleServer, "/settings/secret-managers");
  await expect(page.getByRole("heading", { level: 2, name: "Secret managers", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "No secret manager", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Add secret manager" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a secret manager" });
  await dialog.getByLabel("Name").fill("doppler");
  await chooseOption(page, "Manager", "Doppler");
  await dialog.getByLabel("project", { exact: true }).fill("shop");
  await dialog.getByLabel("config", { exact: true }).fill("prd");
  await dialog.getByLabel("Token").fill("dp.st.not-a-real-token");
  await stillness(page);
  await expectNoA11yViolations(page, "adding a secret manager");
  await dialog.getByRole("button", { name: "Add secret manager" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(dialog).toBeHidden();
  const table = page.getByRole("table", { name: "Secret managers set up on this server" });
  await expect(table.getByText("doppler", { exact: true })).toBeVisible();
  await expect(table.getByText("Stored", { exact: true })).toBeVisible();
  await expect(page.getByText("dp.st.not-a-real-token")).toHaveCount(0);

  await table.getByRole("button", { name: "Actions for doppler" }).click();
  await page.getByRole("menuitem", { name: "Remove" }).click();
  await page.getByRole("alertdialog", { name: "Remove doppler?" }).getByRole("button", { name: "Remove secret manager" }).click();
  await expect(page.getByRole("heading", { name: "No secret manager", exact: true })).toBeVisible();
});

test("an app is cloned from its menu, and asks for a domain first", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await signIn(page, consoleServer, "/apps");
  await page.getByRole("button", { name: "Actions for shop.example.net" }).click();
  await page.getByRole("menuitem", { name: /Clone application/ }).click();
  const dialog = page.getByRole("dialog", { name: "Clone shop.example.net" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Clone application" }).click();
  await expect(dialog.getByText("Give the domain the clone answers on.", { exact: true })).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "cloning an application");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
});

test("an app carries tags, the list filters by them and the tagged apps are updated together", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await signIn(page, consoleServer, "/apps/shop.example.net/settings");
  await page.getByRole("button", { name: "Edit tags" }).click();
  const dialog = page.getByRole("dialog", { name: "Tags of shop.example.net" });
  await dialog.getByLabel(/Tags/).fill("web, client-acme");
  await stillness(page);
  await expectNoA11yViolations(page, "an app's tags");
  await dialog.getByRole("button", { name: "Save tags" }).click();
  await expect(dialog).toBeHidden();
  const tags = page.getByRole("list", { name: "App tags", exact: true });
  await expect(tags.getByText("client-acme", { exact: true })).toBeVisible();

  await page.goto("/apps");
  await chooseOption(page, "Tag", "client-acme");
  await expect(page).toHaveURL(/tag=client-acme/);
  await expect(page.getByRole("link", { name: "shop.example.net", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "blog.example.org", exact: true })).toHaveCount(0);

  const queued = page.waitForResponse((r) => r.url().endsWith("/api/tags/client-acme/deploy") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Update tagged apps" }).click();
  const confirm = page.getByRole("alertdialog", { name: "Update the apps tagged client-acme?" });
  await expect(confirm).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "updating the apps of a tag");
  await confirm.getByRole("button", { name: "Update apps" }).click();
  expect((await queued).status()).toBe(202);
});
