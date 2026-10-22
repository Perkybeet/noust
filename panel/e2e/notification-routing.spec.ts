/**
 * Settings > Notifications beyond the nine default channels, against the real backend: a second
 * Slack added by name for staging, the new channel types, what a channel is limited to (its own
 * events, applications and quiet hours, written through PUT /api/notifications/channels/{name}
 * and read back), and the record of deliveries.
 *
 * The channels are written into the configuration, so this runs on a console server of its own.
 */

import type { Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, signIn, startConsoleServer, test as base } from "./fixtures";
import type { ConsoleServer } from "./fixtures";
import { confirmItsYou, stillness } from "./settings.helpers";

const test = base.extend<object, { consoleServer: ConsoleServer }>({
  consoleServer: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const server = await startConsoleServer([]);
      try {
        await use(server);
      } finally {
        await server.stop();
      }
    },
    { scope: "worker", timeout: 75_000 },
  ],
});

const STAGING = "https://hooks.slack.test/services/T0/B1/staging-secret";

function row(page: Page, name: string) {
  return page.getByRole("list", { name: "Notification channels" }).getByRole("listitem").filter({ hasText: new RegExp(`^${name}`) });
}

test("a second Slack by name, set up in the drawer it opens, limited to an application and to the night", async ({ page, consoleServer, problems }) => {
  // The first write asks "Confirm it's you" by answering 403.
  problems.expect(/status of 403 .* \/api\/notifications\/channels\/slack-staging$/);
  await signIn(page, consoleServer, "/settings/notifications");
  await expect(page.getByText("No more channels. Add one to send some events somewhere else.")).toBeVisible();

  await page.getByRole("button", { name: "Add a channel" }).click();
  const add = page.getByRole("dialog", { name: "Add a channel" });
  await add.getByLabel("Name").fill("Slack Staging");
  // A name the server would refuse is not sent: pressing says what is wrong with it.
  await add.getByRole("button", { name: "Add channel" }).click();
  await expect(add.getByText("A name starts with a lowercase letter and has up to 32 of them, digits, - and _.")).toBeVisible();
  await add.getByLabel("Name").fill("slack-staging");
  await add.getByRole("button", { name: "Add channel" }).click();
  await confirmItsYou(page, consoleServer);

  // Added by name and type; its destination is asked for in the drawer that opens by itself.
  const drawer = page.getByRole("dialog", { name: "slack-staging" });
  await expect(drawer).toBeVisible();
  await drawer.getByLabel("Incoming webhook URL", { exact: true }).fill(STAGING);
  await drawer.getByRole("button", { name: "Save" }).click();
  await expect(drawer.getByRole("button", { name: "Save" })).toBeDisabled();
  await drawer.getByRole("button", { name: "Done" }).click();
  const more = page.getByRole("list", { name: "More notification channels" });
  await expect(more.getByText("Slack channel")).toBeVisible();
  await expect(more.getByText("On", { exact: true })).toBeVisible();

  // What it hears: one application, and nothing at night but failures.
  await more.getByRole("button", { name: "Routing for slack-staging" }).click();
  const routing = page.getByRole("dialog", { name: "Routing for slack-staging" });
  await routing.getByRole("textbox", { name: /^Applications/ }).fill("tienda.example.org, *.staging.example.org");
  await routing.getByRole("checkbox", { name: "Hold messages during quiet hours" }).click();
  await routing.getByLabel("Time zone").fill("Europe/Madrid");
  await stillness(page);
  await expectNoA11yViolations(page, "a channel's routing drawer");
  await routing.getByRole("button", { name: "Save" }).click();
  await expect(routing.getByRole("button", { name: "Save" })).toBeDisabled();
  await routing.getByRole("button", { name: "Done" }).click();
  await expect(more.getByText("Applications: tienda.example.org, *.staging.example.org")).toBeVisible();
  await expect(more.getByText("Quiet from 22:00 to 07:00")).toBeVisible();

  // The server keeps it, and a credential never comes back.
  await page.reload();
  const reloaded = page.getByRole("list", { name: "More notification channels" });
  await expect(reloaded.getByText("Applications: tienda.example.org, *.staging.example.org")).toBeVisible();
  await reloaded.getByRole("button", { name: "Edit slack-staging" }).click();
  const edit = page.getByRole("dialog", { name: "slack-staging" });
  await expect(edit.getByLabel("Incoming webhook URL", { exact: true })).toHaveValue("");
  await expect(edit.getByLabel("Incoming webhook URL", { exact: true })).toHaveAttribute("placeholder", "Saved - leave empty to keep it");
  await expect(page.getByText(STAGING)).toHaveCount(0);

  // Removing it asks once, and the list is as it was.
  await edit.getByRole("button", { name: "Remove channel" }).click();
  await page.getByRole("alertdialog", { name: "Remove slack-staging?" }).getByRole("button", { name: "Remove channel" }).click();
  await expect(page.getByText("No more channels. Add one to send some events somewhere else.")).toBeVisible();
});

test("a channel hears the events it was given instead of the server's, and the page says so", async ({ page, consoleServer, problems }) => {
  problems.expect(/status of 403 .* \/api\/notifications\/channels\/slack$/);
  await signIn(page, consoleServer, "/settings/notifications");
  // Slack has nowhere to send yet: set it up so it has routing to set.
  await row(page, "Slack").getByRole("button", { name: "Set up Slack" }).click();
  const slack = page.getByRole("dialog", { name: "Slack" });
  await slack.getByLabel("Incoming webhook URL", { exact: true }).fill(STAGING);
  await slack.getByRole("button", { name: "Save" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(slack.getByRole("button", { name: "Save" })).toBeDisabled();
  await slack.getByRole("button", { name: "Done" }).click();

  await row(page, "Slack").getByRole("button", { name: "Routing for Slack" }).click();
  const routing = page.getByRole("dialog", { name: "Routing for Slack" });
  await expect(routing.getByText("It hears every event switched on under What is sent.")).toBeVisible();
  await routing.getByRole("radio", { name: "Choose here" }).click();
  // Every event the server can say is listed in groups headed "5 of 8"; their totals add up to
  // the number the row repeats, whichever events a release adds.
  const headings = await routing.locator("fieldset legend").allTextContents();
  const events = headings.reduce((sum, heading) => sum + Number(/\d+ of (\d+)/.exec(heading)?.[1] ?? 0), 0);
  expect(events).toBeGreaterThan(0);
  await routing.getByRole("checkbox", { name: "Deploy started" }).check();
  await routing.getByRole("checkbox", { name: "Backup finished" }).uncheck();
  await routing.getByRole("button", { name: "Save" }).click();
  await expect(routing.getByRole("button", { name: "Save" })).toBeDisabled();
  await routing.getByRole("button", { name: "Done" }).click();
  await expect(row(page, "Slack")).toContainText(`Own switches for ${String(events)} events`);
});

test("the new channel types have a row each, and the record of deliveries says nothing has been sent on a fresh server", async ({
  page,
  consoleServer,
  problems,
}) => {
  problems.expect(/status of 403 .* \/api\/notifications\/channels\/pushover$/);
  await signIn(page, consoleServer, "/settings/notifications");
  for (const name of ["Pushover", "ntfy", "Gotify", "Microsoft Teams"]) {
    await expect(row(page, name).getByText("Off", { exact: true })).toBeVisible();
    await expect(row(page, name).getByRole("button", { name: `Set up ${name}` })).toBeVisible();
  }
  // Both keys, or Pushover has nowhere to send to.
  await row(page, "Pushover").getByRole("button", { name: "Set up Pushover" }).click();
  const pushover = page.getByRole("dialog", { name: "Pushover" });
  await pushover.getByLabel("User key", { exact: true }).fill("u".repeat(30));
  await pushover.getByRole("button", { name: "Save" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(pushover.getByRole("button", { name: "Save" })).toBeDisabled();
  await expect(pushover.getByRole("button", { name: "Send test" })).toBeDisabled();
  await pushover.getByLabel("Application token", { exact: true }).fill("a".repeat(30));
  await pushover.getByRole("button", { name: "Save" }).click();
  await expect(pushover.getByRole("button", { name: "Send test" })).toBeEnabled();
  await pushover.getByRole("button", { name: "Done" }).click();
  await expect(row(page, "Pushover").getByText("On", { exact: true })).toBeVisible();

  await expect(page.getByRole("heading", { name: "Sent messages" })).toBeVisible();
  await expect(page.getByText("Nothing has been sent yet.")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the notifications page with its routing, more channels and deliveries");
});
