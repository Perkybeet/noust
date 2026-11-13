/**
 * Settings > Notifications, the Email and Telegram channels, against the real backend.
 *
 * Each channel is set up in its drawer. Email: the SMTP account is a form over GET/PUT
 * /api/config/smtp. A value the configuration
 * refuses lands beside its field in the server's words, and a test that reaches an SMTP server
 * this spec runs - one that refuses to relay - shows that server's own reply verbatim.
 *
 * Telegram: the console server answers the Bot API from its model (console_server.py's
 * TELEGRAM_BOTS), so finding a chat and sending a test never leave the machine. A bot that has
 * seen chats lists them and picking one fills the chat ID; a bot that has seen none explains
 * how to make one appear; a test to a chat the bot is not in shows Telegram's own description.
 *
 * Both write the configuration, so they run on a console server of their own.
 */

import type { Page } from "@playwright/test";
import { createServer } from "node:net";
import type { AddressInfo, Socket } from "node:net";

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

/** The relay's refusal, as a mail server words it: what the page must show verbatim. */
const RELAY_DENIED = "5.7.1 Relaying denied: this server does not accept mail from noust@example.com";

/** A minimal SMTP server on loopback that greets, answers EHLO and refuses every sender. */
async function refusingSmtpServer(): Promise<{ port: number; close: () => Promise<void> }> {
  const sockets = new Set<Socket>();
  const server = createServer((socket) => {
    sockets.add(socket);
    socket.on("close", () => sockets.delete(socket));
    socket.setEncoding("utf8");
    socket.write("220 relay.test ESMTP\r\n");
    let buffered = "";
    socket.on("data", (chunk: string) => {
      buffered += chunk;
      let end = buffered.indexOf("\r\n");
      while (end !== -1) {
        const line = buffered.slice(0, end).toUpperCase();
        buffered = buffered.slice(end + 2);
        if (line.startsWith("EHLO")) socket.write("250-relay.test\r\n250 8BITMIME\r\n");
        else if (line.startsWith("HELO")) socket.write("250 relay.test\r\n");
        else if (line.startsWith("MAIL FROM")) socket.write(`550 ${RELAY_DENIED}\r\n`);
        else if (line.startsWith("QUIT")) socket.end("221 Bye\r\n");
        else socket.write("250 OK\r\n");
        end = buffered.indexOf("\r\n");
      }
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;
  return {
    port,
    close: () =>
      new Promise((resolve) => {
        for (const socket of sockets) socket.destroy();
        server.close(() => {
          resolve();
        });
      }),
  };
}

function row(page: Page, name: string) {
  return page.getByRole("list", { name: "Notification channels" }).getByRole("listitem").filter({ hasText: new RegExp(`^${name}`) });
}

async function openDrawer(page: Page, name: string) {
  await row(page, name).getByRole("button", { name: new RegExp(`^(Set up|Edit) ${name}$`) }).click();
  const drawer = page.getByRole("dialog", { name });
  await expect(drawer).toBeVisible();
  return drawer;
}

test("email: the SMTP account is a form, refusals land on their field, a failed test shows the server's reply", async ({
  page,
  consoleServer,
  problems,
}) => {
  // The first save asks "Confirm it's you" by answering 403 first; the refused host is a 400.
  problems.expect(/status of 403 .* \/api\/config\/smtp$/);
  problems.expect(/status of 400 .* \/api\/config\/smtp$/);
  const relay = await refusingSmtpServer();
  try {
    await signIn(page, consoleServer, "/settings/notifications");
    await expect(row(page, "Email").getByText("Off", { exact: true })).toBeVisible();
    const email = await openDrawer(page, "Email");
    const host = email.getByLabel("SMTP server");
    await expect(host).toHaveValue("");
    await expect(email.getByRole("button", { name: "Send test" })).toBeDisabled();
    await expect(email.getByText("Save the SMTP server and a recipient to test it.")).toBeVisible();
    await expect(email.getByRole("radio", { name: "SSL/TLS" })).toBeChecked();
    await settle(page);
    await expectNoA11yViolations(page, "the email drawer, empty");

    // A host the configuration refuses: its words, beside the field.
    await host.fill("smtp example");
    await email.getByRole("radio", { name: "None" }).click();
    await expect(email.getByLabel("Port")).toHaveValue("25");
    await email.getByLabel("Port").fill(String(relay.port));
    await email.getByLabel(/From address/).fill("noust@example.com");
    const add = email.getByLabel("Add a recipient");
    await add.fill("ops@example");
    await add.press("Enter");
    await expect(email.getByText("ops@example is not an email address. Use one such as ops@example.com.")).toBeVisible();
    await add.fill("ops@example.com");
    await email.getByRole("button", { name: "Add", exact: true }).click();
    await expect(email.getByRole("list", { name: "Recipients" }).getByRole("listitem")).toHaveCount(1);
    await email.getByRole("checkbox", { name: "Send notifications by email" }).click();
    await email.getByRole("button", { name: "Save" }).click();
    await confirmItsYou(page, consoleServer);
    await expect(email.getByText(/^monitor\.smtp\.host is not a valid hostname: .*Use a hostname such as smtp\.example\.com\.$/)).toBeVisible();
    await expect(host).toHaveAttribute("aria-invalid", "true");
    await stillness(page);
    await expectNoA11yViolations(page, "the email drawer with a refused host");

    // Fixed, it saves, the drawer stays for the test, and the password stays write-only.
    await host.fill("127.0.0.1");
    await email.getByRole("button", { name: "Save" }).click();
    await expect(email.getByRole("button", { name: "Save" })).toBeDisabled();
    await expect(email.getByLabel(/^Password/)).toHaveValue("");

    // The relay refuses the sender: its own reply, verbatim.
    await email.getByRole("button", { name: "Send test" }).click();
    await expect(email.getByText("The test failed. What the SMTP server said:")).toBeVisible();
    await expect(email.locator("pre")).toContainText(RELAY_DENIED);
    await stillness(page);
    await expectNoA11yViolations(page, "the email channel with a failed test");
    await email.getByRole("button", { name: "Done" }).click();
    await expect(row(page, "Email").getByText("On", { exact: true })).toBeVisible();
    await expect(row(page, "Email")).toContainText("To 1 recipient, through 127.0.0.1.");

    await page.reload();
    const reloaded = await openDrawer(page, "Email");
    await expect(reloaded.getByLabel("SMTP server")).toHaveValue("127.0.0.1");
    await expect(reloaded.getByLabel("Port")).toHaveValue(String(relay.port));
    await expect(reloaded.getByRole("radio", { name: "None" })).toBeChecked();
    await expect(reloaded.getByRole("checkbox", { name: "Send notifications by email" })).toBeChecked();
  } finally {
    await relay.close();
  }
});

test("telegram: the missing-minus warning is announced once the chat ID field is left, not on every keystroke", async ({
  page,
  consoleServer,
}) => {
  await signIn(page, consoleServer, "/settings/notifications");
  const telegram = await openDrawer(page, "Telegram");
  // The hint is there before anything is typed.
  await expect(telegram.getByText(/A group's ID is negative, and a supergroup's starts with -100/)).toBeVisible();
  const chatId = telegram.getByLabel("Chat ID");
  const warning = telegram.getByText(/without its minus sign/);
  const announcer = page.getByTestId("announcer-polite");

  await chatId.clear();
  await chatId.pressSequentially("1001987654321");
  // Drawn as it is typed, but never inside a live region: that would be read out on every digit.
  await expect(warning).toHaveText(/try -1001987654321\.$/);
  await expect(telegram.getByRole("alert")).toHaveCount(0);
  await expect(announcer).not.toContainText("minus sign");

  await chatId.press("Tab");
  await expect(announcer).toContainText(/without its minus sign.*try -1001987654321\.$/);
  await settle(page);
  await expectNoA11yViolations(page, "the missing-minus warning");

  // Adding the sign clears it.
  await chatId.press("Home");
  await chatId.pressSequentially("-");
  await expect(chatId).toHaveValue("-1001987654321");
  await expect(warning).toHaveCount(0);
});

test("telegram: finds the chats the bot has seen, fills the chat ID, and shows Telegram's own words", async ({
  page,
  consoleServer,
  problems,
}) => {
  problems.expect(/status of 403 .* \/api\/config\/notifications\/telegram$/);
  problems.expect(/status of 422 .* \/api\/config\/notifications\/telegram$/);
  await signIn(page, consoleServer, "/settings/notifications");
  const telegram = await openDrawer(page, "Telegram");
  const save = telegram.getByRole("button", { name: "Save" });
  const find = telegram.getByRole("button", { name: "Find my chat" });
  await expect(find).toBeDisabled();
  await expect(telegram.getByText("Save the bot token first.")).toBeVisible();

  // A bot nobody has written to yet: the list is empty, and says how to fill it.
  await telegram.getByLabel("Bot token", { exact: true }).fill("7000000002:console-sandbox-quiet");
  await expect(telegram.getByText("Save the new token first.")).toBeVisible();
  await save.click();
  await confirmItsYou(page, consoleServer);
  await expect(save).toBeDisabled();
  await find.click();
  await expect(telegram.getByText("Your bot has not seen any chat yet.", { exact: true }).last()).toBeVisible();
  await expect(telegram.getByText("/start@your_bot_name")).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "a bot that has seen no chat");

  // A chat ID Telegram would refuse: the server's words beside the field.
  await telegram.getByLabel("Chat ID").fill("1001987654321");
  await save.click();
  await expect(telegram.getByText(/did you mean -1001987654321\?/)).toBeVisible();
  await expect(telegram.getByLabel("Chat ID")).toHaveAttribute("aria-invalid", "true");

  // A chat the bot is not in: the test shows Telegram's own description.
  await telegram.getByLabel("Chat ID").fill("-100123");
  await save.click();
  await expect(save).toBeDisabled();
  await telegram.getByRole("button", { name: "Send test" }).click();
  await expect(telegram.getByText("The test failed. What Telegram said:")).toBeVisible();
  await expect(telegram.locator("pre")).toContainText("Bad Request: chat not found");

  // The bot that has seen chats: pick the group, save, and the test goes through.
  await telegram.getByLabel("Bot token", { exact: true }).fill("7000000001:console-sandbox-bot");
  await save.click();
  await expect(save).toBeDisabled();
  await find.click();
  const chats = telegram.getByRole("list", { name: "Chats your bot has seen" });
  await expect(chats.getByRole("listitem")).toHaveCount(2);
  await expect(chats.getByText("Noust alerts")).toBeVisible();
  await expect(chats.getByText(/Supergroup/)).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "the chats a bot has seen");
  await chats.getByRole("button", { name: "Use Noust alerts" }).click();
  await expect(telegram.getByLabel("Chat ID")).toHaveValue("-1001987654321");
  await expect(chats.getByText("Chosen")).toBeVisible();
  await save.click();
  await expect(save).toBeDisabled();
  await telegram.getByRole("button", { name: "Send test" }).click();
  await expect(telegram.getByText("Sent a test message. Check that it arrived.")).toBeVisible();
  await telegram.getByRole("button", { name: "Done" }).click();
  await expect(row(page, "Telegram")).toContainText("Sends to chat -1001987654321.");
});

/**
 * The channel set to a modelled bot and chat, through its drawer, so a test of the topics starts from a known group.
 * The first write of a test asks "Confirm it's you"; what follows is inside the sudo window and does not.
 */
async function useGroup(page: Page, consoleServer: ConsoleServer, bot: string, chat: string, asksSudoMode = false): Promise<void> {
  const telegram = await openDrawer(page, "Telegram");
  const save = telegram.getByRole("button", { name: "Save" });
  await telegram.getByLabel("Bot token", { exact: true }).fill(bot);
  await telegram.getByLabel("Chat ID").fill(chat);
  await save.click();
  if (asksSudoMode) await confirmItsYou(page, consoleServer);
  await expect(save).toBeDisabled();
  await telegram.getByRole("button", { name: "Done" }).click();
  await expect(row(page, "Telegram")).toContainText(`Sends to chat ${chat}.`);
}

async function openRouting(page: Page) {
  await row(page, "Telegram").getByRole("button", { name: "Routing for Telegram" }).click();
  const drawer = page.getByRole("dialog", { name: "Routing for Telegram" });
  await expect(drawer).toBeVisible();
  return drawer;
}

test("telegram: a topic for each subject, created by Noust in the group, and what a group that cannot take them says", async ({
  page,
  consoleServer,
  problems,
}) => {
  // Sudo mode is asked by answering 403 the first write of the page; what follows is inside its window.
  problems.expect(/status of 403 .* \/api\/config\/notifications\/telegram$/);
  await signIn(page, consoleServer, "/settings/notifications");
  await useGroup(page, consoleServer, "7000000001:console-sandbox-bot", "-1001987654321", true);

  const routing = await openRouting(page);
  await expect(routing.getByRole("heading", { name: "Where it is posted" })).toBeVisible();
  const place = routing.getByRole("radiogroup", { name: "Where this channel posts" });
  await expect(place.getByRole("radio", { name: "General thread" })).toBeChecked();
  await place.getByRole("radio", { name: "A topic per subject" }).click();
  for (const subject of ["Deploys", "Alerts", "Backups", "Security", "System"]) {
    await expect(routing.getByRole("textbox", { name: new RegExp(`^${subject}`) })).toBeVisible();
  }
  await stillness(page);
  await expectNoA11yViolations(page, "the topics of each subject, before any exists");

  // Noust asks the group, through the bot, and the numbers it made are in the fields.
  await routing.getByRole("button", { name: "Create the missing topics" }).click();
  await expect(routing.getByText("Created 5 topics.")).toBeVisible();
  for (const subject of ["Deploys", "Alerts", "Backups", "Security", "System"]) {
    await expect(routing.getByRole("textbox", { name: new RegExp(`^${subject}`) })).toHaveValue(/^\d+$/);
  }
  await expect(routing.getByRole("button", { name: "Save" })).toBeDisabled();
  await stillness(page);
  await expectNoA11yViolations(page, "the topics Noust created");
  await routing.getByRole("button", { name: "Done" }).click();
  await expect(row(page, "Telegram")).toContainText("A topic for each subject");

  // A group that is not a forum: said, with the way to turn topics on, and nothing is made.
  await useGroup(page, consoleServer, "7000000003:console-sandbox-plain", "-1001555555555");
  const plain = await openRouting(page);
  await plain.getByRole("button", { name: "Create the missing topics" }).click();
  await expect(plain.getByText("The group does not have topics turned on")).toBeVisible();
  await expect(plain.getByText(/Only the group's owner can turn them on/)).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "a group that has no topics turned on");
});

test("telegram: a topic that stopped taking messages is said in the row and in the drawer, with Telegram's own words", async ({
  page,
  consoleServer,
  problems,
}) => {
  problems.expect(/status of 403 .* \/api\/config\/notifications\/telegram$/);
  await signIn(page, consoleServer, "/settings/notifications");
  await useGroup(page, consoleServer, "7000000001:console-sandbox-bot", "-1001987654321", true);

  // A topic the group does not have: the test still arrives, in the general thread, and the channel is marked.
  const routing = await openRouting(page);
  await routing.getByRole("radio", { name: "One topic" }).click();
  await routing.getByRole("textbox", { name: /Topic number/ }).fill("99");
  await routing.getByRole("button", { name: "Save" }).click();
  await expect(routing.getByRole("button", { name: "Save" })).toBeDisabled();
  await routing.getByRole("button", { name: "Done" }).click();
  await row(page, "Telegram").getByRole("button", { name: "Send test to Telegram" }).click();
  await expect(row(page, "Telegram")).toContainText("Sent a test message. Check that it arrived.");
  await expect(row(page, "Telegram")).toContainText("Topic 99 does not take messages");

  const again = await openRouting(page);
  await expect(again.getByText("Topic 99 no longer takes messages")).toBeVisible();
  await expect(again.getByText("Bad Request: message thread not found")).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "a topic that stopped taking messages");
});
