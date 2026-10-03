import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";

/** The notifications block as GET /api/config answers it: every secret redacted, set or not. */
function configBody(notifications: Record<string, unknown> = {}) {
  return {
    config: {
      notifications: {
        enabled: false,
        events: {
          deploy_started: false,
          deploy_success: true,
          deploy_failed: true,
          deploy_rolled_back: true,
          cert_expiring: true,
          unit_failed: true,
          disk_threshold: true,
          backup_failed: true,
        },
        channels: {
          webhook: { webhook_url: "***", secret: "" },
          slack: { webhook_url: "***" },
          discord: { webhook_url: "***" },
          telegram: { bot_token: "***", chat_id: "" },
          email: { enabled: false },
        },
        ...notifications,
      },
      monitor: { smtp: { host: "", port: 465, from_address: "", password: "***" }, email_recipients: [] },
    },
    path: "/etc/noust/config.yaml",
    writable: true,
  };
}

/** GET /api/config/smtp: the typed SMTP account, the password only as "one is stored". */
const SMTP_UNSET = {
  host: "",
  port: 465,
  use_ssl: true,
  use_tls: false,
  username: "",
  from_address: "",
  recipients: [],
  password_set: false,
};

const SMTP_SET = {
  host: "smtp.example.com",
  port: 465,
  use_ssl: true,
  use_tls: false,
  username: "noust@example.com",
  from_address: "noust@example.com",
  recipients: ["ops@example.com"],
  password_set: true,
};

function notificationsBackend(
  options: { smtp?: typeof SMTP_UNSET | typeof SMTP_SET; config?: ReturnType<typeof configBody>; routes?: Parameters<typeof fakeBackend>[0] } = {},
) {
  let elevated = false;
  const needsElevation = () => (elevated ? null : problem(403, "elevation_required", "Confirm it's you to continue."));
  return fakeBackend({
    ...signedInRoutes(),
    "GET /api/config": () => json(200, options.config ?? configBody()),
    "GET /api/config/smtp": () => json(200, options.smtp ?? SMTP_UNSET),
    "PUT /api/config/smtp": () => needsElevation() ?? json(200, { message: "SMTP configuration updated" }),
    "PUT /api/config/notifications/telegram": () => needsElevation() ?? json(200, { message: "Telegram configuration updated" }),
    "POST /api/auth/elevate": () => {
      elevated = true;
      return json(200, { elevated_until: new Date(Date.now() + 600_000).toISOString() });
    },
    "PATCH /api/config": (call) => {
      if (!elevated) return problem(403, "elevation_required", "Confirm it's you to continue.");
      const body = call.body as { path: string; value: unknown };
      return json(200, { message: `Configuration '${body.path}' updated`, path: "/etc/noust/config.yaml", value: body.value });
    },
    "POST /api/config/notifications/slack/test": () =>
      json(200, { ok: false, detail: "Channel slack is not configured; set notifications.channels.slack.webhook_url first." }),
    "POST /api/config/notifications/webhook/test": () => json(200, { ok: true, detail: "Test message sent through webhook." }),
    ...options.routes,
  });
}

/** A channel's row in the list, found by its name. */
function row(name: string): HTMLElement {
  const list = screen.getByRole("list", { name: "Notification channels" });
  const found = within(list)
    .getAllByRole("listitem")
    .find((item) => item.textContent.startsWith(name));
  if (found === undefined) throw new Error(`no row for ${name}`);
  return found;
}

async function confirmItsYou(user: ReturnType<typeof renderConsole>["user"]): Promise<void> {
  const confirm = await screen.findByRole("dialog", { name: "Confirm it's you" });
  await user.type(within(confirm).getByLabelText("Authentication code"), "123456");
  await user.click(within(confirm).getByRole("button", { name: "Confirm" }));
}

function saveBar(): HTMLElement {
  return screen.getByRole("region", { name: "Unsaved changes" });
}

describe("Settings > Notifications", () => {
  it("says first whether anything is sent, lists the channels with their state, folds the events, and passes axe", { timeout: 20_000 }, async () => {
    notificationsBackend();
    const { container } = renderConsole("/settings/notifications");
    const master = await screen.findByRole("switch", { name: /Send notifications/ });
    expect(master).not.toBeChecked();
    expect(screen.getByText(/Off: nothing is sent/)).toBeInTheDocument();
    // A channel's on or off is a state at a glance (item 56): the running green with its dot,
    // the stopped grey with its ring, and the word; a label, not a control.
    const slackOn = within(row("Slack")).getByText("On");
    expect(slackOn).toHaveAttribute("data-state", "running");
    expect(slackOn).toHaveClass("text-ok", "bg-ok-soft");
    expect(slackOn.querySelector("svg")).toHaveAttribute("data-glyph", "dot");
    expect(slackOn.closest("button, [role='switch'], a")).toBeNull();
    expect(within(row("Slack")).getByRole("button", { name: "Edit Slack" })).toBeInTheDocument();
    expect(within(row("Webhook")).getByText("Deliveries are not signed.")).toBeInTheDocument();
    expect(within(row("Telegram")).getByText("Bot saved; no chat chosen yet.")).toBeInTheDocument();
    const emailOff = within(row("Email")).getByText("Off");
    expect(emailOff).toHaveAttribute("data-state", "stopped");
    expect(emailOff).toHaveClass("text-idle", "bg-idle-soft");
    expect(emailOff.querySelector("svg")).toHaveAttribute("data-glyph", "ring");
    expect(within(row("Email")).getByRole("button", { name: "Set up Email" })).toBeInTheDocument();
    // Every event, by area, each group saying how many of its events are sent.
    expect(screen.getByText("Deploys", { selector: "summary *" })).toBeInTheDocument();
    // Deploys: deploy_hook_failed is missing from the file, so it is sent (on by default).
    expect(screen.getAllByText("4 of 5 sent")).toHaveLength(1);
    expect(screen.getAllByText("3 of 4 sent")).toHaveLength(1);
    expect(screen.getByText("Servers you manage", { selector: "summary *" })).toBeInTheDocument();
    // One save bar for the form, and nothing to save yet.
    expect(within(saveBar()).getByText("No unsaved changes")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("tests a channel from its row and shows what the receiving server said, verbatim", async () => {
    notificationsBackend();
    const { user } = renderConsole("/settings/notifications");
    await screen.findByRole("switch", { name: /Send notifications/ });
    await user.click(within(row("Slack")).getByRole("button", { name: "Send a test to Slack" }));
    expect(
      await within(row("Slack")).findByText("Channel slack is not configured; set notifications.channels.slack.webhook_url first."),
    ).toBeInTheDocument();
    expect(within(row("Slack")).getByText("The test failed. What the receiving server said:")).toBeInTheDocument();

    await user.click(within(row("Webhook")).getByRole("button", { name: "Send a test to Webhook" }));
    expect(await within(row("Webhook")).findByText("Sent a test message. Check that it arrived.")).toBeInTheDocument();
  });

  it("sets up a webhook in its drawer with a generated signing secret, keeping the URL it was not given", { timeout: 20_000 }, async () => {
    const backend = notificationsBackend();
    const { user } = renderConsole("/settings/notifications");
    await screen.findByRole("switch", { name: /Send notifications/ });
    await user.click(within(row("Webhook")).getByRole("button", { name: "Edit Webhook" }));
    const drawer = await screen.findByRole("dialog", { name: "Webhook" });
    // Write-only: the stored URL is never shown, and the field says one is kept.
    const url = within(drawer).getByLabelText("Endpoint URL");
    expect(url).toHaveValue("");
    expect(url).toHaveAttribute("placeholder", "Saved - leave empty to keep it");
    await user.click(within(drawer).getByRole("button", { name: "Generate" }));
    const secret = within(drawer).getByLabelText<HTMLInputElement>(/Signing secret/).value;
    expect(secret).toMatch(/^[0-9a-f]{64}$/);
    expect(within(drawer).getByText(/Copy it to your endpoint now/)).toBeInTheDocument();
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    await confirmItsYou(user);
    // Refused once for sudo mode, then sent again once confirmed.
    await waitFor(() => {
      expect(backend.callsTo("PATCH /api/config")).toHaveLength(2);
    });
    expect(backend.callsTo("PATCH /api/config").at(-1)?.body).toEqual({
      path: "notifications.channels.webhook",
      value: { webhook_url: "***", secret },
    });
    // Saving keeps the drawer open, so the test is one click away.
    expect(screen.getByRole("dialog", { name: "Webhook" })).toBeInTheDocument();
  });

  it("removes a destination after one question", { timeout: 20_000 }, async () => {
    const backend = notificationsBackend();
    const { user } = renderConsole("/settings/notifications");
    await screen.findByRole("switch", { name: /Send notifications/ });
    await user.click(within(row("Slack")).getByRole("button", { name: "Edit Slack" }));
    const drawer = await screen.findByRole("dialog", { name: "Slack" });
    await user.click(within(drawer).getByRole("button", { name: "Remove destination" }));
    const question = await screen.findByRole("alertdialog", { name: "Remove the Slack destination?" });
    await user.click(within(question).getByRole("button", { name: "Remove destination" }));
    await confirmItsYou(user);
    await waitFor(() => {
      expect(backend.callsTo("PATCH /api/config").at(-1)?.body).toEqual({ path: "notifications.channels.slack", value: { webhook_url: "" } });
    });
  });

  it("hints that a group's chat ID is negative, finds the bot's chats and shows Telegram's own words", { timeout: 20_000 }, async () => {
    const backend = notificationsBackend({
      routes: {
        "POST /api/config/notifications/telegram/chats": () =>
          json(200, { chats: [{ id: -1001234567890, type: "supergroup", title: "Ops alerts", username: null }] }),
        "POST /api/config/notifications/telegram/test": () => json(200, { ok: false, detail: "HTTP 400 Bad Request: Bad Request: chat not found" }),
      },
    });
    const { user } = renderConsole("/settings/notifications");
    await screen.findByRole("switch", { name: /Send notifications/ });
    await user.click(within(row("Telegram")).getByRole("button", { name: "Edit Telegram" }));
    const drawer = await screen.findByRole("dialog", { name: "Telegram" });
    expect(within(drawer).getByText(/A group's ID is negative, and a supergroup's starts with -100/)).toBeInTheDocument();
    const chat = within(drawer).getByLabelText("Chat ID");
    await user.type(chat, "1001234567890");
    expect(within(drawer).getByText(/try -1001234567890/)).toBeInTheDocument();
    await user.clear(chat);

    await user.click(within(drawer).getByRole("button", { name: "Find my chat" }));
    await user.click(await within(drawer).findByRole("button", { name: "Use Ops alerts" }));
    expect(chat).toHaveValue("-1001234567890");
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    await confirmItsYou(user);
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/config/notifications/telegram")).toHaveLength(2);
    });
    expect(backend.callsTo("PUT /api/config/notifications/telegram").at(-1)?.body).toEqual({ bot_token: "", chat_id: "-1001234567890" });

    await user.click(within(drawer).getByRole("button", { name: "Send test" }));
    expect(await within(drawer).findByText("HTTP 400 Bad Request: Bad Request: chat not found")).toBeInTheDocument();
    expect(within(drawer).getByText("The test failed. What Telegram said:")).toBeInTheDocument();
  });

  it("sets up email - the SMTP account, a write-only password and the recipients - in one save", { timeout: 30_000 }, async () => {
    const backend = notificationsBackend();
    const { user } = renderConsole("/settings/notifications");
    await screen.findByRole("switch", { name: /Send notifications/ });
    await user.click(within(row("Email")).getByRole("button", { name: "Set up Email" }));
    const drawer = await screen.findByRole("dialog", { name: "Email" });
    await user.click(within(drawer).getByRole("checkbox", { name: "Send notifications by email" }));
    await user.type(await within(drawer).findByLabelText("SMTP server"), "smtp.example.com");
    await user.type(within(drawer).getByLabelText(/Password/), "hunter2");
    await user.type(within(drawer).getByLabelText("Add a recipient"), "not-an-address");
    await user.click(within(drawer).getByRole("button", { name: "Add" }));
    expect(within(drawer).getByText(/not-an-address is not an email address/)).toBeInTheDocument();
    await user.clear(within(drawer).getByLabelText("Add a recipient"));
    await user.type(within(drawer).getByLabelText("Add a recipient"), "ops@example.com, dev@example.com");
    await user.click(within(drawer).getByRole("button", { name: "Add" }));
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    await confirmItsYou(user);
    await waitFor(() => {
      expect(backend.callsTo("PATCH /api/config").length).toBeGreaterThan(0);
    });
    expect(backend.callsTo("PUT /api/config/smtp").at(-1)?.body).toMatchObject({
      host: "smtp.example.com",
      password: "hunter2",
      recipients: ["ops@example.com", "dev@example.com"],
    });
    expect(backend.callsTo("PATCH /api/config").at(-1)?.body).toEqual({ path: "notifications.channels.email", value: { enabled: true } });
  });

  it("puts the SMTP server's refusal beside the field it is about, verbatim", { timeout: 20_000 }, async () => {
    notificationsBackend({
      smtp: SMTP_SET,
      routes: {
        "PUT /api/config/smtp": () => problem(400, "config_error", "monitor.smtp.host is not a valid hostname: 'smtp example'"),
      },
    });
    const { user } = renderConsole("/settings/notifications");
    await screen.findByRole("switch", { name: /Send notifications/ });
    await user.click(within(row("Email")).getByRole("button", { name: "Edit Email" }));
    const drawer = await screen.findByRole("dialog", { name: "Email" });
    const host = await within(drawer).findByLabelText("SMTP server");
    await user.clear(host);
    await user.type(host, "smtp example");
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    expect(await within(drawer).findByText(/monitor\.smtp\.host is not a valid hostname/)).toBeInTheDocument();
    expect(host).toHaveAttribute("aria-invalid", "true");
  });

  it("turns notifications on at once, and saves events and language from the one bar", { timeout: 20_000 }, async () => {
    const backend = notificationsBackend();
    const { user } = renderConsole("/settings/notifications");
    const master = await screen.findByRole("switch", { name: /Send notifications/ });
    await user.click(master);
    await confirmItsYou(user);
    await waitFor(() => {
      expect(backend.callsTo("PATCH /api/config").at(-1)?.body).toEqual({ path: "notifications.enabled", value: true });
    });

    // A folded group opens on demand; the kinds that ship off say so.
    await user.click(screen.getByText("Backups and restores", { selector: "summary *" }));
    const heartbeat = screen.getByRole("checkbox", { name: /Backup finished/ });
    expect(heartbeat).not.toBeChecked();
    expect(within(heartbeat.closest("label") ?? document.body).getByText("Off by default")).toBeInTheDocument();
    await user.click(heartbeat);
    await user.click(screen.getByRole("radio", { name: "Español" }));
    expect(within(saveBar()).getByText("2 unsaved changes")).toBeInTheDocument();
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    const written = (path: string) => backend.callsTo("PATCH /api/config").find((call) => (call.body as { path: string }).path === path);
    await waitFor(() => {
      expect(written("notifications.language")).toBeDefined();
    });
    const events = written("notifications.events")?.body as { path: string; value: Record<string, boolean> };
    expect(events.value).toMatchObject({ backup_success: true, deploy_started: false, restore_failed: true });
    expect(Object.keys(events.value)).toHaveLength(21);
    expect(written("notifications.language")?.body).toEqual({ path: "notifications.language", value: "es" });
  });

  it("reads in Spanish, with no accessibility violations", { timeout: 20_000 }, async () => {
    notificationsBackend();
    await act(async () => {
      await setLocale("es");
    });
    const { container } = renderConsole("/settings/notifications");
    expect(await screen.findByRole("switch", { name: /Enviar notificaciones/ })).toBeInTheDocument();
    expect(screen.getByText("Qué se envía")).toBeInTheDocument();
    expect(screen.getByText("Servidores que gestionas", { selector: "summary *" })).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });
});
