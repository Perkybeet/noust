/**
 * Settings > AI assistant, Settings > Approval policy and what a credential did (items 171, 184 and 185) against the real backend and
 * a scripted model that answers what a test dictates (tests/support/scripted_model.py): no provider is ever asked.
 *
 * One server of this file's own, started with `--assistant ready`: the model is running and the assistant is not configured, so the
 * page that turns it on can be walked end to end. Sudo mode is confirmed once per test over the API.
 */

import type { Page } from "@playwright/test";

import { csrf } from "./apps-cleanup";
import { expect, expectNoA11yViolations, settle, signIn, startConsoleServer, test } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

const serial = test.extend<object, { consoleServer: ConsoleServer }>({
  consoleServer: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const server = await startConsoleServer(["--totp", "--backup-codes", "120", "--assistant", "ready"]);
      try {
        await use(server);
      } finally {
        await server.stop();
      }
    },
    { scope: "worker", timeout: 75_000 },
  ],
});
serial.describe.configure({ mode: "serial" });

/** Confirms sudo mode for this browser over the API, with a second factor nothing has spent. */
async function elevate(page: Page, server: ConsoleServer): Promise<void> {
  const answer = await page.request.post("/api/auth/elevate", { data: { code: server.secondFactor() }, headers: await csrf(page) });
  expect(answer.ok()).toBe(true);
}

/** What the scripted model answers to the next requests, dictated by the test. */
async function script(page: Page, server: ConsoleServer, replies: object[]): Promise<void> {
  const address = server.extras.assistant_model;
  if (typeof address !== "string") throw new Error("the server was not started with --assistant");
  const answer = await page.request.post(`${address}/__script`, { data: { replies } });
  expect(answer.ok()).toBe(true);
}

const TOY_CALL = { calls: [{ name: "get_status", arguments: { service: "noust" } }] };

serial.describe("Settings > AI assistant", () => {
  serial("is off at first: it says what it is and offers one way to turn it on", async ({ page, consoleServer }) => {
    await signIn(page, consoleServer, "/settings/assistant");
    await expect(page.getByRole("heading", { level: 1, name: "AI assistant" })).toBeVisible();
    await expect(page.getByText("Off", { exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "What it is, in three things" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Turn on…" })).toBeVisible();
    // Off, nothing of it exists anywhere else in the console.
    await expect(page.getByRole("button", { name: "AI assistant", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Explain this" })).toHaveCount(0);
    await expectNoA11yViolations(page, "assistant off");
  });

  serial("turns on in four steps, saying what the probe found in sentences", async ({ page, consoleServer }) => {
    await signIn(page, consoleServer, "/settings/assistant");
    await elevate(page, consoleServer);
    await page.getByRole("link", { name: "Turn on…" }).click();
    await expect(page.getByRole("heading", { name: "Where does the model come from?" })).toBeVisible();
    await expect(page.getByRole("list", { name: "Steps" })).toBeVisible();

    await page.getByRole("radio", { name: /This machine/ }).click();
    const address = `${String(consoleServer.extras.assistant_model)}/v1`;
    await page.getByLabel("Address", { exact: true }).fill(address);
    await page.getByLabel("Model", { exact: true }).fill("tiny-model");
    await page.getByRole("checkbox", { name: /I allow this server to call that address/ }).check();

    // Pressing Continue before the test says what is missing and moves nowhere.
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByText("Test the connection first: the assistant is turned on only when the provider answers.")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Where does the model come from?" })).toBeVisible();

    await script(page, consoleServer, [TOY_CALL]);
    await page.getByRole("button", { name: "Test the connection" }).click();
    await expect(page.getByText("With this model it explains, investigates and proposes.")).toBeVisible();
    await expect(page.getByRole("list", { name: "Result of the test" })).toBeVisible();
    await expectNoA11yViolations(page, "assistant step model");

    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByRole("heading", { name: "What leaves the server" })).toBeVisible();
    await expect(page.getByText("Values of variables, keys and passwords: they are hidden even if they appear in a log.")).toBeVisible();
    await expectNoA11yViolations(page, "assistant step data");

    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByRole("heading", { name: "Limits" })).toBeVisible();
    await page.getByLabel("Input", { exact: true }).fill("0.08");
    await page.getByLabel("Output", { exact: true }).fill("0.4");
    await expectNoA11yViolations(page, "assistant step limits");

    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByRole("heading", { name: "Who may use it" })).toBeVisible();
    await expectNoA11yViolations(page, "assistant step who");

    await page.getByRole("button", { name: "Turn it on", exact: true }).click();
    await expect(page.getByText("On", { exact: true })).toBeVisible();
    await expect(page.getByText("tiny-model", { exact: true })).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "assistant on");
  });

  serial("once on, says what leaves and the limits, and turns off by asking once", async ({ page, consoleServer }) => {
    await signIn(page, consoleServer, "/settings/assistant/data");
    await expect(page.getByRole("heading", { name: "What leaves the server" })).toBeVisible();
    await expect(page.getByLabel("Steps per answer")).toHaveValue("20");
    await expectNoA11yViolations(page, "assistant data");

    await page.goto("/settings/assistant");
    await elevate(page, consoleServer);
    await page.getByRole("button", { name: "Turn off the assistant…" }).click();
    await page.getByRole("button", { name: "Turn off the assistant" }).last().click();
    await expect(page.getByText("Off", { exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Turn on…" })).toBeVisible();
  });
});

serial.describe("Settings > Approval policy", () => {
  serial("is a table per area, with the floor as a padlock and the factory's values said only where they differ", async ({ page, consoleServer }) => {
    await signIn(page, consoleServer, "/settings/approval-policy");
    await expect(page.getByRole("heading", { level: 1, name: "Approval policy" })).toBeVisible();
    const apps = page.getByRole("table", { name: "Approval policy, Applications" });
    await expect(apps).toBeVisible();
    await expect(apps.getByText("apps.operate")).toBeVisible();
    // A row that asks to confirm it is you never offers "Does it" to the assistant.
    await apps.getByRole("button", { name: /Create, configure and delete applications.*The AI assistant, Production/ }).click();
    await expect(page.getByRole("menuitem", { name: /Does it/ })).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expectNoA11yViolations(page, "approval policy");
  });

  serial("saves what changed after showing what it does to the credentials that exist", async ({ page, consoleServer }) => {
    await signIn(page, consoleServer, "/settings/approval-policy");
    await elevate(page, consoleServer);
    const operate = page.getByRole("table", { name: "Approval policy, Applications" });
    await operate.getByRole("button", { name: /Start, stop and restart.*The AI assistant, Other environments/ }).click();
    await page.getByRole("menuitem", { name: /Does it/ }).click();
    await expect(page.getByText("1 unsaved change")).toBeVisible();
    await page.getByRole("button", { name: "Save" }).click();
    const dialog = page.getByRole("alertdialog", { name: "Save the approval policy" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText("1 cell changes")).toBeVisible();
    await expect(dialog.getByRole("button", { name: /See what it will do/ })).toBeVisible();
    await expectNoA11yViolations(page, "approval policy save");
    await dialog.getByRole("button", { name: "Save the policy" }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByText("Factory: proposes it")).toBeVisible();
  });
});
