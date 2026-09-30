/**
 * Settings > Notifications against the real backend and a real receiving endpoint.
 *
 * The notifier refuses private destinations, so the test lists 127.0.0.1 as an allowed
 * private host (through the page), sets up the webhook channel in its drawer - pointed at an
 * HTTP listener this test runs, with a generated signing secret - and sends a test: the
 * listener receives the notifier's signed JSON and the page reports it went. A refused private
 * address reports the server's words verbatim. No request leaves the machine.
 */

import type { Page } from "@playwright/test";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";
import { confirmItsYou, stillness } from "./settings.helpers";

interface Received {
  path: string;
  body: Record<string, unknown>;
  signature: string | undefined;
}

/** A local endpoint that records what it is sent and answers 204. */
async function listen(): Promise<{ url: string; received: Received[]; close: () => Promise<void> }> {
  const received: Received[] = [];
  const server = createServer((request, response) => {
    let text = "";
    request.setEncoding("utf8");
    request.on("data", (chunk: string) => {
      text += chunk;
    });
    request.on("end", () => {
      const signature = request.headers["x-noust-signature"];
      received.push({
        path: request.url ?? "",
        body: JSON.parse(text || "{}") as Record<string, unknown>,
        signature: typeof signature === "string" ? signature : undefined,
      });
      response.writeHead(204).end();
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;
  return {
    url: `http://127.0.0.1:${String(port)}/hooks/noust`,
    received,
    close: () => new Promise((resolve) => server.close(() => { resolve(); })),
  };
}

function row(page: Page, name: string) {
  return page.getByRole("list", { name: "Notification channels" }).getByRole("listitem").filter({ hasText: new RegExp(`^${name}`) });
}

function saveBar(page: Page) {
  return page.getByRole("region", { name: "Unsaved changes" });
}

test("sets up channels in their drawers and reports what the receiving server answered", async ({ page, consoleServer, problems }) => {
  // A drawer's save asks "Confirm it's you" by answering 403 first, by design.
  problems.expect(/status of 403 .* \/api\/config$/);
  const endpoint = await listen();
  try {
    await signIn(page, consoleServer, "/settings/notifications");
    await expect(page.getByRole("heading", { level: 2, name: "Channels", exact: true })).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "notifications");

    // A channel with nowhere to send has no test, only the way to set it up.
    await expect(row(page, "Slack").getByText("Off", { exact: true })).toBeVisible();
    await expect(row(page, "Slack").getByRole("button", { name: "Send a test to Slack" })).toHaveCount(0);
    await expect(row(page, "Slack").getByRole("button", { name: "Set up Slack" })).toBeVisible();

    // Allow this machine as a destination, which the SSRF guard refuses otherwise.
    await page.locator("summary", { hasText: "Private destinations" }).click();
    await page.getByLabel(/Allowed private hosts/).fill("127.0.0.1");
    await saveBar(page).getByRole("button", { name: "Save" }).click();
    await confirmItsYou(page, consoleServer);
    await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();

    // The webhook, in its drawer: the URL write-only, a signing secret generated and copied.
    await row(page, "Webhook").getByRole("button", { name: "Set up Webhook" }).click();
    const drawer = page.getByRole("dialog", { name: "Webhook" });
    await expect(drawer).toBeVisible();
    const url = drawer.getByLabel("Endpoint URL", { exact: true });
    await expect(url).toHaveAttribute("type", "password");
    await url.fill(endpoint.url);
    await expect(drawer.getByRole("button", { name: "Send test" })).toBeDisabled();
    await expect(drawer.getByText("Save a destination to test it.")).toBeVisible();
    await drawer.getByRole("button", { name: "Generate" }).click();
    await expect(drawer.getByText(/Copy it to your endpoint now/)).toBeVisible();
    await stillness(page);
    await expectNoA11yViolations(page, "the webhook drawer");
    await drawer.getByRole("button", { name: "Save" }).click();
    await expect(url).toHaveValue("");
    await expect(url).toHaveAttribute("placeholder", "Saved - leave empty to keep it");

    // The test goes to what is saved, signed, and the drawer says it went.
    await drawer.getByRole("button", { name: "Send test" }).click();
    await expect(drawer.getByText("Sent a test message. Check that it arrived.")).toBeVisible();
    expect(endpoint.received).toHaveLength(1);
    expect(endpoint.received[0]?.path).toBe("/hooks/noust");
    expect(endpoint.received[0]?.signature).toMatch(/^sha256=[0-9a-f]{64}$/);
    await drawer.getByRole("button", { name: "Done" }).click();
    await expect(row(page, "Webhook").getByText("Signed with a secret.")).toBeVisible();
    await expect(row(page, "Webhook").getByText("On", { exact: true })).toBeVisible();

    // A private address that is not allowed is refused before any request is made, in the
    // server's words, under the row that was tested.
    await row(page, "Discord").getByRole("button", { name: "Set up Discord" }).click();
    const discord = page.getByRole("dialog", { name: "Discord" });
    await discord.getByLabel("Webhook URL", { exact: true }).fill("http://10.20.30.40/hook");
    await discord.getByRole("button", { name: "Save" }).click();
    await expect(discord.getByRole("button", { name: "Remove destination" })).toBeVisible();
    await discord.getByRole("button", { name: "Done" }).click();
    await row(page, "Discord").getByRole("button", { name: "Send a test to Discord" }).click();
    await expect(row(page, "Discord").getByText("The test failed. What the receiving server said:")).toBeVisible();
    await expect(row(page, "Discord").locator("pre")).toContainText("allow_private_hosts");
    await stillness(page);
    await expectNoA11yViolations(page, "notifications with test results");

    // The master switch applies at once, and says what it means.
    const toggle = page.getByRole("switch", { name: /Send notifications/ });
    await expect(toggle).not.toBeChecked();
    await toggle.click();
    await expect(toggle).toBeChecked();
    await expect(page.getByText("On: the events chosen below go to 2 channels.")).toBeVisible();
    await toggle.click();
    await expect(toggle).not.toBeChecked();

    // Everything back as it was: each destination removed after one question.
    for (const name of ["Webhook", "Discord"]) {
      await row(page, name).getByRole("button", { name: `Edit ${name}` }).click();
      await page.getByRole("dialog", { name }).getByRole("button", { name: "Remove destination" }).click();
      const question = page.getByRole("alertdialog", { name: `Remove the ${name} destination?` });
      await question.getByRole("button", { name: "Remove destination" }).click();
      await expect(question).toBeHidden();
      await expect(row(page, name).getByRole("button", { name: `Set up ${name}` })).toBeVisible();
    }
    await page.getByLabel(/Allowed private hosts/).fill("");
    await saveBar(page).getByRole("button", { name: "Save" }).click();
    await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "channels after removing their destinations");
  } finally {
    await endpoint.close();
  }
});
