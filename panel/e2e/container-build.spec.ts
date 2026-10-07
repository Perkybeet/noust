/**
 * Container applications and build commands against the real backend, in both themes (checklist
 * A-31, A-14, A-30, A-41, A-42, A-43): an image from a registry configured in the wizard with
 * its registry password typed masked and sent once, a repository with a Dockerfile proposed as a
 * container whose Dockerfile settings go with the deploy, and an application's build commands
 * saved behind "Confirm it's you" and taken out again. The machine the console server models
 * has no Docker, so a deploy of either ends in the server's own refusal, which is shown as it
 * was written. The container and build settings of seeded applications are catalog.spec.ts's.
 */

import type { Page } from "@playwright/test";

import { forgetApp } from "./apps-cleanup";
import { confirmItsYou, expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";
import { inspectSource, wizardSource } from "./wizard-sources";

/** Chooses one of the wizard's ways in: "A Docker image", "From a recipe"... */
async function chooseStart(page: Page, name: string): Promise<void> {
  const choice = page.getByRole("radiogroup", { name: "Where the code is" }).getByRole("radio", { name });
  await expect(choice).toBeVisible();
  await choice.click();
  await expect(choice).toHaveAttribute("aria-checked", "true");
}

function stepTitle(page: Page) {
  return page.locator('[data-template="wizard"] [data-slot="step"] h2');
}

test("an image from a registry is configured in the wizard and sent with its registry account, which is never shown", async ({ page, consoleServer }) => {
  test.setTimeout(240_000);
  const domain = "caja-imagen.example.net";
  await signIn(page, consoleServer, "/apps/new");
  await chooseStart(page, "A Docker image");
  await page.getByLabel("Image", { exact: true }).fill("ghcr.io/example-org/caja:1.4");
  await settle(page);
  await expectNoA11yViolations(page, "the image start");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(stepTitle(page)).toHaveText("Address");
  await page.getByLabel("Domain", { exact: true }).fill(domain);
  await page.getByRole("button", { name: "Continue", exact: true }).click();

  await expect(stepTitle(page)).toHaveText("Configuration");
  // What is pulled; nothing was read, so there is no type to choose, no Dockerfile, no build secrets.
  await expect(page.getByRole("main").getByText("ghcr.io/example-org/caja:1.4").first()).toBeVisible();
  await expect(page.getByRole("combobox", { name: "App type" })).toHaveCount(0);
  await expect(page.getByRole("group", { name: "Build secrets" })).toHaveCount(0);
  const registry = page.getByRole("group", { name: "Private registry" });
  await registry.getByLabel(/^Username/).fill("ci");
  const password = registry.getByLabel(/^Password/);
  await expect(password).toHaveAttribute("type", "password");
  await password.fill("tok_live_never_shown_123");
  await page.getByLabel(/^Container port/).fill("8080");
  const volumes = page.getByRole("group", { name: "Volumes" });
  await volumes.getByRole("button", { name: "Add volume" }).click();
  await volumes.getByRole("textbox").fill("/data");
  await settle(page);
  await expectNoA11yViolations(page, "the container settings of an image");
  await page.getByRole("button", { name: "Continue", exact: true }).click();

  // What is about to happen, and not the password.
  await expect(stepTitle(page)).toHaveText("Deploy");
  await expect(page.getByText("Its password is sent once and never shown.")).toBeVisible();
  expect(await page.content()).not.toContain("tok_live_never_shown_123");
  await settle(page);
  await expectNoA11yViolations(page, "the summary of an image application");

  const queued = page.waitForRequest((request) => request.url().endsWith("/api/apps") && request.method() === "POST");
  await page.getByRole("button", { name: `Deploy ${domain}` }).click();
  const body = (await queued).postDataJSON() as Record<string, unknown>;
  expect(body).toMatchObject({
    domain,
    app_type: "container",
    container: { method: "image", image: "ghcr.io/example-org/caja:1.4", container_port: 8080, volumes: ["/data"], registry_username: "ci", registry_password: "tok_live_never_shown_123" },
  });
  expect(body).not.toHaveProperty("source");
  expect(body).not.toHaveProperty("layout");
  // The machine has no Docker: the job fails and the console says what the server said, once,
  // without the account's password anywhere on the page.
  await expect(page.getByText(/before it started building|was not deployed|failed/i).first()).toBeVisible({ timeout: 120_000 });
  expect(await page.content()).not.toContain("tok_live_never_shown_123");
  await forgetApp(page, consoleServer, domain);
});

test("a repository with only a Dockerfile is configured as a container, its Dockerfile settings sent with the deploy", async ({ page, consoleServer, problems }) => {
  test.setTimeout(240_000);
  const domain = "caja-dockerfile.example.net";
  await signIn(page, consoleServer, "/apps/new");
  await inspectSource(page, consoleServer, problems, await wizardSource(page, "container-api"));
  await page.getByLabel("Domain", { exact: true }).fill(domain);
  await page.getByRole("button", { name: "Continue", exact: true }).click();

  await expect(stepTitle(page)).toHaveText("Configuration");
  await expect(page.getByRole("combobox", { name: "App type" })).toHaveText(/Container/);
  await expect(page.getByRole("radio", { name: /A Dockerfile/ })).toBeChecked();
  // The port the real inspection read off the Dockerfile.
  await expect(page.getByText(/Empty uses 3000, the one the Dockerfile exposes/)).toBeVisible();
  await page.getByLabel(/^Dockerfile/).fill("Dockerfile");
  await page.getByLabel(/^Build stage/).fill("runtime");
  const args = page.getByRole("group", { name: "Build arguments" });
  await args.getByRole("button", { name: "Add argument" }).click();
  await args.getByLabel("Name").fill("NODE_ENV");
  await args.getByLabel("Value").fill("production");
  const secrets = page.getByRole("group", { name: "Build secrets" });
  await secrets.getByRole("button", { name: "Add secret" }).click();
  await secrets.getByLabel("Id").fill("npmrc");
  await secrets.getByLabel("Value", { exact: true }).fill("//registry.npmjs.org/:_authToken=never_shown_456");
  await settle(page);
  await expectNoA11yViolations(page, "the container settings of a Dockerfile");
  await page.getByRole("button", { name: "Continue", exact: true }).click();

  await expect(stepTitle(page)).toHaveText("Deploy");
  await expect(page.getByText("1 arguments and 1 secrets")).toBeVisible();
  expect(await page.content()).not.toContain("never_shown_456");
  const queued = page.waitForRequest((request) => request.url().endsWith("/api/apps") && request.method() === "POST");
  await page.getByRole("button", { name: `Deploy ${domain}` }).click();
  const body = (await queued).postDataJSON() as { container: Record<string, unknown> } & Record<string, unknown>;
  expect(body).toMatchObject({ domain, app_type: "container", container: { method: "dockerfile", dockerfile: "Dockerfile", target: "runtime", build_args: { NODE_ENV: "production" } } });
  expect(body.container.build_secrets).toEqual({ npmrc: "//registry.npmjs.org/:_authToken=never_shown_456" });
  await expect(page.getByText(/before it started building|was not deployed|failed/i).first()).toBeVisible({ timeout: 120_000 });
  expect(await page.content()).not.toContain("never_shown_456");
  await forgetApp(page, consoleServer, domain);
});

test("an application's build commands are saved behind confirming it is you, win over the repository whole, and can be taken out", async ({ page, consoleServer }) => {
  test.setTimeout(120_000);
  const domain = "shop.example.net";
  await signIn(page, consoleServer, `/apps/${domain}/settings/build`);
  await expect(page.getByRole("heading", { level: 2, name: "Build commands" })).toBeVisible();
  const nav = page.getByRole("navigation", { name: "Application settings" }).first();
  await expect(nav.getByRole("link", { name: "Build commands" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByText("The commands of the application's type")).toBeVisible();
  await expect(page.getByText("This application's own settings win, whole")).toBeVisible();
  // The runtimes the server has are a command away: no endpoint says them.
  await expect(page.getByText("noust runtime list")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "an application's build commands");

  // What is wrong is said before anything is sent, beside the field.
  await page.getByRole("textbox", { name: /^Base directory/ }).fill("../outside");
  await page.getByRole("region", { name: "Unsaved changes" }).getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("A path inside the repository: relative, without .., ~, spaces or quotes.")).toBeVisible();
  await page.getByRole("textbox", { name: /^Base directory/ }).fill("");

  await page.getByRole("textbox", { name: /^Start command/ }).fill("node dist/server.js --port ${PORT}");
  await page.getByRole("textbox", { name: /^Build command/ }).fill("npm ci && npm run build");
  await expect(page.getByText(/pipe, &&, a redirection or a substitution in it/)).toBeVisible();
  await page.getByRole("textbox", { name: /^Build command/ }).fill("npm run build");
  await page.getByRole("button", { name: "node@22", exact: true }).click();
  const saved = page.waitForRequest((request) => request.url().endsWith(`/api/apps/${domain}/build`) && request.method() === "PUT");
  await page.getByRole("region", { name: "Unsaved changes" }).getByRole("button", { name: "Save" }).click();
  await confirmItsYou(page, consoleServer);
  expect((await saved).postDataJSON()).toEqual({ build: "npm run build", start: "node dist/server.js --port ${PORT}", runtime: "node@22" });
  await expect(page.getByText("Saved. It takes effect with the next update.")).toBeVisible();
  await expect(page.getByText("This application's own settings", { exact: true })).toBeVisible();
  const stored = (await (await page.request.get(`/api/apps/${domain}/build`)).json()) as { source: string; start: string; runtime: string };
  expect(stored).toMatchObject({ source: "operator", start: "node dist/server.js --port ${PORT}", runtime: "node@22" });
  await settle(page);
  await expectNoA11yViolations(page, "an application with its own build commands");

  // Taken out: the repository's apply again, or the type's where it has none. One question first.
  await page.getByRole("button", { name: "Remove its own settings" }).click();
  const confirm = page.getByRole("alertdialog", { name: `Remove the build commands of ${domain}?` });
  await expect(confirm).toBeVisible();
  await confirm.getByRole("button", { name: "Remove its own settings" }).click();
  await expect(confirm).toBeHidden();
  await expect(page.getByText("The commands of the application's type")).toBeVisible();
  const cleared = (await (await page.request.get(`/api/apps/${domain}/build`)).json()) as { source: string };
  expect(cleared.source).toBe("none");
});
