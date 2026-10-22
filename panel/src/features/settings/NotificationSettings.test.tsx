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

interface EmailApiFixture {
  provider: string;
  providers: string[];
  from_address: string;
  region: string;
  api_key_set: boolean;
  access_key_id_set: boolean;
  secret_access_key_set: boolean;
  recipients: string[];
  configured: boolean;
}

/** GET /api/config/email-api: no service chosen, so the mail goes through SMTP. */
const EMAIL_API_UNSET: EmailApiFixture = {
  provider: "",
  providers: ["resend", "postmark", "brevo", "ses"],
  from_address: "",
  region: "",
  api_key_set: false,
  access_key_id_set: false,
  secret_access_key_set: false,
  recipients: [],
  configured: false,
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

const CHANNEL_TYPES = ["webhook", "slack", "discord", "telegram", "email", "pushover", "ntfy", "gotify", "teams", "mattermost", "matrix", "lark", "pagerduty"];

interface ChannelView {
  name: string;
  type: string;
  enabled: boolean;
  configured: boolean;
  default: boolean;
  settings: Record<string, unknown>;
  events: Record<string, boolean>;
  route: Record<string, unknown>;
  quiet: Record<string, unknown> | null;
  flood: Record<string, unknown> | null;
  summary: Record<string, unknown> | null;
}

function channelView(name: string, extra: Partial<ChannelView> = {}): ChannelView {
  return { name, type: name, enabled: true, configured: false, default: true, settings: {}, events: {}, route: {}, quiet: null, flood: null, summary: null, ...extra };
}

/** GET /api/notifications/channels: the nine defaults (Slack, Webhook and Telegram set up), then whatever else is given. */
function channelsBody(extra: ChannelView[] = [], overrides: Record<string, Partial<ChannelView>> = {}) {
  const defaults = CHANNEL_TYPES.map((type) => channelView(type, { configured: ["webhook", "slack", "telegram"].includes(type), ...overrides[type] }));
  return { items: [...defaults, ...extra], types: CHANNEL_TYPES.map((name) => ({ name, keys: [] })), enabled: false };
}

const NO_DELIVERIES = { items: [], counts: {} };

function notificationsBackend(
  options: {
    smtp?: typeof SMTP_UNSET | typeof SMTP_SET;
    emailApi?: EmailApiFixture;
    config?: ReturnType<typeof configBody>;
    routes?: Parameters<typeof fakeBackend>[0];
    channels?: ReturnType<typeof channelsBody>;
    deliveries?: unknown;
  } = {},
) {
  let elevated = false;
  const needsElevation = () => (elevated ? null : problem(403, "elevation_required", "Confirm it's you to continue."));
  // The channels as the server holds them, changed by the PUTs of the page.
  const base = options.channels ?? channelsBody();
  const held = new Map(base.items.map((channel) => [channel.name, channel]));
  const putChannel = (name: string): [string, (call: { body: unknown }) => Response] => [
    `PUT /api/notifications/channels/${name}`,
    (call) => {
      const refused = needsElevation();
      if (refused !== null) return refused;
      const body = call.body as {
        type?: string;
        settings?: Record<string, unknown>;
        events?: Record<string, boolean>;
        route?: Record<string, unknown>;
        quiet?: Record<string, unknown>;
        flood?: Record<string, unknown>;
        summary?: Record<string, unknown>;
      };
      const current = held.get(name) ?? channelView(name, { type: body.type ?? name, default: false });
      const settings = { ...current.settings, ...body.settings };
      const next: ChannelView = {
        ...current,
        settings,
        configured: Object.values(settings).some((value) => value !== "" && value !== null),
        events: body.events ?? current.events,
        route: body.route ?? current.route,
        quiet: body.quiet === undefined ? current.quiet : Object.keys(body.quiet).length === 0 ? null : body.quiet,
        flood: body.flood === undefined ? current.flood : Object.keys(body.flood).length === 0 ? null : body.flood,
        summary: body.summary === undefined ? current.summary : Object.keys(body.summary).length === 0 ? null : body.summary,
      };
      held.set(name, next);
      return json(200, next);
    },
  ];
  return fakeBackend({
    ...signedInRoutes(),
    "GET /api/config": () => json(200, options.config ?? configBody()),
    "GET /api/config/smtp": () => json(200, options.smtp ?? SMTP_UNSET),
    "GET /api/config/email-api": () => json(200, options.emailApi ?? EMAIL_API_UNSET),
    "PUT /api/config/email-api": () => needsElevation() ?? json(200, { message: "Email provider configuration updated" }),
    "GET /api/notifications/channels": () => json(200, { ...base, items: [...held.values()] }),
    "GET /api/notifications/deliveries": () => json(200, options.deliveries ?? NO_DELIVERIES),
    ...Object.fromEntries([...CHANNEL_TYPES, "slack-staging", "oncall"].map(putChannel)),
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
  await user.click(within(confirm).getByRole("button", { name: "Continue" }));
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
    expect(within(row("Webhook")).getByText("Messages are not signed.")).toBeInTheDocument();
    expect(within(row("Telegram")).getByText("Bot saved; no chat chosen yet.")).toBeInTheDocument();
    const emailOff = within(row("Email")).getByText("Off");
    expect(emailOff).toHaveAttribute("data-state", "stopped");
    expect(emailOff).toHaveClass("text-idle", "bg-idle-soft");
    expect(emailOff.querySelector("svg")).toHaveAttribute("data-glyph", "ring");
    expect(within(row("Email")).getByRole("button", { name: "Set up Email" })).toBeInTheDocument();
    // Every event, by area, each group saying how many of its events are sent.
    expect(screen.getByText("Deploys", { selector: "summary *" })).toBeInTheDocument();
    // Deploys: deploy_hook_failed is missing from the file, so it is sent (on by default). Backups
    // lists the events 3.4 added, which are on by default as well: deploy_started and
    // backup_success ship off, everything else is on.
    expect(screen.getAllByText("4 of 5 sent")).toHaveLength(1);
    expect(screen.getAllByText("5 of 6 sent")).toHaveLength(1);
    // The alerts and jobs: only the heartbeat of a cron job that ended well ships off.
    expect(screen.getAllByText("7 of 8 sent")).toHaveLength(1);
    // This server: a disk cleanup that freed space ships off too; the security release, the
    // container restart loop and the recipe updates ship on.
    expect(screen.getAllByText("13 of 14 sent")).toHaveLength(1);
    expect(screen.getByText("Alerts and scheduled jobs", { selector: "summary *" })).toBeInTheDocument();
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
    // Refused once for sudo mode, then sent again once confirmed. Through the channel API, which
    // changes the settings and keeps the channel's routing.
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/notifications/channels/webhook")).toHaveLength(2);
    });
    expect(backend.callsTo("PUT /api/notifications/channels/webhook").at(-1)?.body).toEqual({
      type: "webhook",
      settings: { webhook_url: "***", secret, webhook_headers: "***" },
    });
    expect(backend.callsTo("PATCH /api/config")).toHaveLength(0);
    // Saving keeps the drawer open, so the test is one click away.
    expect(screen.getByRole("dialog", { name: "Webhook" })).toBeInTheDocument();
  });

  it("sends the receiver's own headers as typed, in a field that never shows them again", { timeout: 20_000 }, async () => {
    const backend = notificationsBackend();
    const { user } = renderConsole("/settings/notifications");
    await screen.findByRole("switch", { name: /Send notifications/ });
    await user.click(within(row("Webhook")).getByRole("button", { name: "Edit Webhook" }));
    const drawer = await screen.findByRole("dialog", { name: "Webhook" });
    const headers = within(drawer).getByLabelText<HTMLTextAreaElement>("Headers to send");
    expect(headers).toHaveValue("");
    await user.click(headers);
    await user.paste("Authorization: Bearer abc");
    expect(headers).toHaveValue("Authorization: Bearer abc");
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    await confirmItsYou(user);
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/notifications/channels/webhook")).toHaveLength(2);
    });
    expect(backend.callsTo("PUT /api/notifications/channels/webhook").at(-1)?.body).toEqual({
      type: "webhook",
      settings: { webhook_url: "***", secret: "***", webhook_headers: "Authorization: Bearer abc" },
    });
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
      expect(backend.callsTo("PUT /api/notifications/channels/slack").at(-1)?.body).toEqual({ type: "slack", settings: { webhook_url: "" } });
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
      expect(backend.callsTo("PUT /api/notifications/channels/email").length).toBeGreaterThan(0);
    });
    expect(backend.callsTo("PUT /api/config/smtp").at(-1)?.body).toMatchObject({
      host: "smtp.example.com",
      password: "hunter2",
      recipients: ["ops@example.com", "dev@example.com"],
    });
    expect(backend.callsTo("PUT /api/notifications/channels/email").at(-1)?.body).toEqual({ enabled: true });
  });

  it("sends through an email service instead of SMTP: its key is write-only and the SMTP fields go away", { timeout: 30_000 }, async () => {
    const backend = notificationsBackend();
    const { user } = renderConsole("/settings/notifications");
    await screen.findByRole("switch", { name: /Send notifications/ });
    await user.click(within(row("Email")).getByRole("button", { name: "Set up Email" }));
    const drawer = await screen.findByRole("dialog", { name: "Email" });
    expect(await within(drawer).findByLabelText("SMTP server")).toBeInTheDocument();
    await user.click(within(drawer).getByRole("combobox", { name: "Send through" }));
    await user.click(await screen.findByRole("option", { name: "Resend" }));
    // The service asks for a key and a sender; the SMTP account is not shown while it stands in.
    expect(within(drawer).queryByLabelText("SMTP server")).toBeNull();
    expect(within(drawer).getByText(/Mail goes through Resend; the SMTP server below is not used/)).toBeInTheDocument();
    await user.type(within(drawer).getByLabelText("API key"), "re_live_secret");
    await user.type(within(drawer).getByLabelText("Sender address"), "noust@example.com");
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    await confirmItsYou(user);
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/config/email-api")).toHaveLength(2);
    });
    expect(backend.callsTo("PUT /api/config/email-api").at(-1)?.body).toEqual({
      provider: "resend",
      from_address: "noust@example.com",
      region: "",
      api_key: "re_live_secret",
      access_key_id: "",
      secret_access_key: "",
    });
    // The SMTP account was not touched: nothing about it changed.
    expect(backend.callsTo("PUT /api/config/smtp")).toHaveLength(0);
  });

  it("asks Amazon SES for a region and an access pair, and keeps a saved key when the service is the same", { timeout: 30_000 }, async () => {
    const backend = notificationsBackend({
      emailApi: {
        provider: "ses",
        providers: ["resend", "postmark", "brevo", "ses"],
        from_address: "noust@example.com",
        region: "eu-west-1",
        api_key_set: false,
        access_key_id_set: true,
        secret_access_key_set: true,
        recipients: ["ops@example.com"],
        configured: true,
      },
    });
    const { user } = renderConsole("/settings/notifications");
    await screen.findByRole("switch", { name: /Send notifications/ });
    // A saved service with somebody to write to is the account: the row says so, and that email is still off.
    expect(await within(row("Email")).findByText("Amazon SES saved for 1 recipient; email notifications are off.")).toBeInTheDocument();
    await user.click(within(row("Email")).getByRole("button", { name: "Edit Email" }));
    const drawer = await screen.findByRole("dialog", { name: "Email" });
    expect(await within(drawer).findByLabelText("AWS region")).toHaveValue("eu-west-1");
    // Credentials are never shown; the field says one is saved.
    expect(within(drawer).getByLabelText("Access key ID")).toHaveAttribute("placeholder", "Saved - leave empty to keep it");
    expect(within(drawer).getByLabelText("Secret access key")).toHaveAttribute("placeholder", "Saved - leave empty to keep it");
    const region = within(drawer).getByLabelText("AWS region");
    await user.clear(region);
    await user.type(region, "eu-central-1");
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    await confirmItsYou(user);
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/config/email-api")).toHaveLength(2);
    });
    expect(backend.callsTo("PUT /api/config/email-api").at(-1)?.body).toEqual({
      provider: "ses",
      from_address: "noust@example.com",
      region: "eu-central-1",
      api_key: "",
      access_key_id: "",
      secret_access_key: "",
    });
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
    expect(Object.keys(events.value)).toHaveLength(38);
    expect(written("notifications.language")?.body).toEqual({ path: "notifications.language", value: "es" });
  });

  it("sets up Pushover with both of its keys, and a device that is plain text", { timeout: 20_000 }, async () => {
    const backend = notificationsBackend();
    const { user } = renderConsole("/settings/notifications");
    await screen.findByRole("switch", { name: /Send notifications/ });
    expect(within(row("Pushover")).getByText("Off")).toBeInTheDocument();
    await user.click(within(row("Pushover")).getByRole("button", { name: "Set up Pushover" }));
    const drawer = await screen.findByRole("dialog", { name: "Pushover" });
    await user.type(within(drawer).getByLabelText("User key"), "u".repeat(30));
    await user.type(within(drawer).getByLabelText("Application token"), "a".repeat(30));
    // A plain field shows what is typed; only the keys are masked.
    await user.type(within(drawer).getByLabelText(/Device/), "phone");
    expect(within(drawer).getByLabelText(/Device/)).toHaveValue("phone");
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    await confirmItsYou(user);
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/notifications/channels/pushover")).toHaveLength(2);
    });
    expect(backend.callsTo("PUT /api/notifications/channels/pushover").at(-1)?.body).toEqual({
      type: "pushover",
      settings: { user_key: "u".repeat(30), app_token: "a".repeat(30), device: "phone" },
    });
  });

  it("limits a channel to some applications and hours from its Routing drawer, and says so under its row", { timeout: 30_000 }, async () => {
    const backend = notificationsBackend();
    const { user } = renderConsole("/settings/notifications");
    await screen.findByRole("switch", { name: /Send notifications/ });
    // A channel with nowhere to send has no routing to set.
    expect(within(row("Pushover")).queryByRole("button", { name: "Routing for Pushover" })).toBeNull();
    await user.click(within(row("Slack")).getByRole("button", { name: "Routing for Slack" }));
    const drawer = await screen.findByRole("dialog", { name: "Routing for Slack" });
    await user.type(within(drawer).getByRole("textbox", { name: /^Applications/ }), "shop.example.com, *.staging.example.com");
    await user.click(within(drawer).getByRole("switch", { name: "Hold messages during quiet hours" }));
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    await confirmItsYou(user);
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/notifications/channels/slack")).toHaveLength(2);
    });
    expect(backend.callsTo("PUT /api/notifications/channels/slack").at(-1)?.body).toMatchObject({
      events: {},
      route: { apps: ["shop.example.com", "*.staging.example.com"] },
      quiet: { start: "22:00", end: "07:00", allow: "failed", digest: true },
    });
    // Closed, the page behind it can be read again.
    await user.click(within(drawer).getByRole("button", { name: "Done" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Routing for Slack" })).toBeNull();
    });
    expect(await within(row("Slack")).findByText("Applications: shop.example.com, *.staging.example.com")).toBeInTheDocument();
    expect(within(row("Slack")).getByText("Quiet from 22:00 to 07:00")).toBeInTheDocument();
  });

  it("caps a channel and keeps what is not urgent for a daily summary, and says so under its row", { timeout: 30_000 }, async () => {
    const backend = notificationsBackend();
    const { user } = renderConsole("/settings/notifications");
    await screen.findByRole("switch", { name: /Send notifications/ });
    await user.click(within(row("Slack")).getByRole("button", { name: "Routing for Slack" }));
    const drawer = await screen.findByRole("dialog", { name: "Routing for Slack" });
    expect(within(drawer).queryByLabelText("At most this many messages")).toBeNull();
    await user.click(within(drawer).getByRole("switch", { name: "Limit how much this channel says" }));
    // A number the server would refuse stops the save, and says why.
    const most = within(drawer).getByLabelText("At most this many messages");
    await user.clear(most);
    await user.type(most, "0");
    expect(within(drawer).getAllByText("Use whole numbers: up to 1000 messages, in up to 1440 minutes.").length).toBeGreaterThan(0);
    expect(within(drawer).getByRole("button", { name: "Save" })).toBeDisabled();
    await user.clear(most);
    await user.type(most, "5");
    await user.click(within(drawer).getByRole("switch", { name: "Send what is not urgent as a daily summary" }));
    await user.click(within(drawer).getByRole("checkbox", { name: "Fri" }));
    await user.click(within(drawer).getByRole("checkbox", { name: "Mon" }));
    expect(within(drawer).getByText("It goes out only on the days chosen.")).toBeInTheDocument();
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    await confirmItsYou(user);
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/notifications/channels/slack")).toHaveLength(2);
    });
    expect(backend.callsTo("PUT /api/notifications/channels/slack").at(-1)?.body).toMatchObject({
      flood: { max: 5, minutes: 10 },
      summary: { at: "08:00", below: "warning", days: ["mon", "fri"] },
    });
    await user.click(within(drawer).getByRole("button", { name: "Done" }));
    expect(await within(row("Slack")).findByText("At most 5 per 10 min")).toBeInTheDocument();
    expect(within(row("Slack")).getByText("Daily summary at 08:00")).toBeInTheDocument();
  });

  it("does not offer a cap or a summary to a paging channel, and says why", { timeout: 30_000 }, async () => {
    notificationsBackend({
      config: configBody({
        enabled: true,
        channels: { slack: { webhook_url: "***" }, pagerduty: { routing_key: "***", region: "us" } },
      }),
    });
    const { user } = renderConsole("/settings/notifications");
    await screen.findByRole("switch", { name: /Send notifications/ });
    await user.click(within(row("PagerDuty")).getByRole("button", { name: "Routing for PagerDuty" }));
    const drawer = await screen.findByRole("dialog", { name: "Routing for PagerDuty" });
    expect(within(drawer).getByText(/A paging channel is neither capped nor summarised/)).toBeInTheDocument();
    expect(within(drawer).queryByRole("switch", { name: "Limit how much this channel says" })).toBeNull();
    expect(within(drawer).queryByRole("switch", { name: "Send what is not urgent as a daily summary" })).toBeNull();
    // Quiet hours are still the channel's to set.
    expect(within(drawer).getByRole("switch", { name: "Hold messages during quiet hours" })).toBeInTheDocument();
  });

  it("sets up Matrix with a homeserver, a room and an access token, and PagerDuty with its key", { timeout: 30_000 }, async () => {
    const backend = notificationsBackend();
    const { user } = renderConsole("/settings/notifications");
    await screen.findByRole("switch", { name: /Send notifications/ });
    expect(within(row("Matrix")).getByText("Off")).toBeInTheDocument();
    await user.click(within(row("Matrix")).getByRole("button", { name: "Set up Matrix" }));
    const matrix = await screen.findByRole("dialog", { name: "Matrix" });
    await user.type(within(matrix).getByLabelText("Homeserver"), "https://matrix.example.com");
    await user.type(within(matrix).getByLabelText("Room ID"), "!AbCdEf:example.com");
    await user.type(within(matrix).getByLabelText("Access token"), "syt_secret_token");
    // Only the token is masked: the server and the room are plain text.
    expect(within(matrix).getByLabelText("Homeserver")).toHaveValue("https://matrix.example.com");
    await user.click(within(matrix).getByRole("button", { name: "Save" }));
    await confirmItsYou(user);
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/notifications/channels/matrix")).toHaveLength(2);
    });
    expect(backend.callsTo("PUT /api/notifications/channels/matrix").at(-1)?.body).toEqual({
      type: "matrix",
      settings: { homeserver: "https://matrix.example.com", room_id: "!AbCdEf:example.com", access_token: "syt_secret_token" },
    });
    await user.click(within(matrix).getByRole("button", { name: "Done" }));
    await user.click(within(row("PagerDuty")).getByRole("button", { name: "Set up PagerDuty" }));
    const pager = await screen.findByRole("dialog", { name: "PagerDuty" });
    await user.type(within(pager).getByLabelText("Integration key"), "k".repeat(32));
    await user.type(within(pager).getByLabelText(/Service region/), "eu");
    await user.click(within(pager).getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/notifications/channels/pagerduty")).toHaveLength(1);
    });
    expect(backend.callsTo("PUT /api/notifications/channels/pagerduty").at(-1)?.body).toEqual({
      type: "pagerduty",
      settings: { routing_key: "k".repeat(32), region: "eu" },
    });
  });

  it("gives a channel its own events instead of the server's, all written at once", { timeout: 30_000 }, async () => {
    const backend = notificationsBackend();
    const { user } = renderConsole("/settings/notifications");
    await screen.findByRole("switch", { name: /Send notifications/ });
    await user.click(within(row("Slack")).getByRole("button", { name: "Routing for Slack" }));
    const drawer = await screen.findByRole("dialog", { name: "Routing for Slack" });
    expect(within(drawer).getByText("It hears every event switched on under What is sent.")).toBeInTheDocument();
    await user.click(within(drawer).getByRole("radio", { name: "Choose here" }));
    await user.click(within(drawer).getByRole("checkbox", { name: "Deploy failed" }));
    await user.click(within(drawer).getByRole("checkbox", { name: "Deploy started" }));
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    await confirmItsYou(user);
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/notifications/channels/slack")).toHaveLength(2);
    });
    const events = (backend.callsTo("PUT /api/notifications/channels/slack").at(-1)?.body as { events: Record<string, boolean> }).events;
    expect(Object.keys(events)).toHaveLength(38);
    expect(events).toMatchObject({ deploy_failed: false, deploy_started: true, deploy_success: true });
    await user.click(within(drawer).getByRole("button", { name: "Done" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Routing for Slack" })).toBeNull();
    });
    expect(await within(row("Slack")).findByText("Own switches for 38 events")).toBeInTheDocument();
  });

  it("adds a channel by name and type, then asks for its destination in the drawer it opens", { timeout: 30_000 }, async () => {
    const backend = notificationsBackend();
    const { user } = renderConsole("/settings/notifications");
    await screen.findByRole("switch", { name: /Send notifications/ });
    expect(screen.getByText("No more channels. Add one to send some events somewhere else.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add a channel" }));
    const add = await screen.findByRole("dialog", { name: "Add a channel" });
    // A name the server would refuse is not sent.
    await user.type(within(add).getByLabelText("Name"), "Bad Name");
    expect(within(add).getByRole("button", { name: "Add channel" })).toBeDisabled();
    await user.clear(within(add).getByLabelText("Name"));
    await user.type(within(add).getByLabelText("Name"), "slack-staging");
    await user.click(within(add).getByRole("button", { name: "Add channel" }));
    await confirmItsYou(user);
    const drawer = await screen.findByRole("dialog", { name: "slack-staging" });
    expect(backend.callsTo("PUT /api/notifications/channels/slack-staging").at(-1)?.body).toEqual({ type: "slack" });
    // The new channel is asked for what its type needs, with nothing yet to send to.
    expect(within(drawer).getByLabelText("Incoming webhook URL")).toHaveValue("");
    await user.click(within(drawer).getByRole("button", { name: "Done" }));
    const list = await screen.findByRole("list", { name: "More notification channels" });
    expect(within(list).getByText("Slack channel")).toBeInTheDocument();
    expect(within(list).getByText("Off")).toBeInTheDocument();
  });

  it("lists what became of each notification, in the destination's own words, and sends a waiting one now", { timeout: 20_000 }, async () => {
    const now = Math.floor(Date.now() / 1000);
    const failing = {
      id: 7,
      notification_id: "n-7",
      kind: "deploy_failed",
      code: "deploy.failed",
      subject: "shop.example.com",
      channel: "slack",
      channel_type: "slack",
      state: "retrying",
      attempts: 2,
      created_at: now - 120,
      updated_at: now - 60,
      next_attempt_at: now + 300,
      last_error: "HTTP 502 Bad Gateway",
      can_retry: true,
    };
    const backend = notificationsBackend({
      deliveries: { items: [failing, { ...failing, id: 6, state: "sent", attempts: 1, last_error: null, can_retry: false, next_attempt_at: null }], counts: { retrying: 1, sent: 1 } },
      routes: { "POST /api/notifications/deliveries/7/retry": () => json(200, { ...failing, state: "sent", can_retry: false }) },
    });
    const { user } = renderConsole("/settings/notifications");
    await screen.findByRole("switch", { name: /Send notifications/ });
    const list = await screen.findByRole("list", { name: "Recent deliveries" });
    expect(screen.getByText("1 waiting to be tried again")).toBeInTheDocument();
    const [waiting] = within(list).getAllByRole("listitem");
    if (waiting === undefined) throw new Error("no delivery listed");
    expect(within(waiting).getByText("Trying again")).toBeInTheDocument();
    expect(within(waiting).getByText("HTTP 502 Bad Gateway")).toBeInTheDocument();
    await user.click(within(waiting).getByRole("button", { name: "Send shop.example.com to slack now" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/notifications/deliveries/7/retry")).toHaveLength(1);
    });
  });

  it("says why a delivery is held: the quiet hours, a cap that was reached or the summary", { timeout: 20_000 }, async () => {
    const now = Math.floor(Date.now() / 1000);
    const held = {
      id: 9,
      notification_id: "n-9",
      kind: "deploy_success",
      code: "deploy.succeeded",
      subject: "shop.example.com",
      channel: "slack",
      channel_type: "slack",
      state: "held",
      attempts: 0,
      created_at: now - 60,
      updated_at: now - 60,
      next_attempt_at: now + 600,
      last_error: null,
      can_retry: true,
      held_for: "flood",
    };
    notificationsBackend({
      deliveries: {
        items: [held, { ...held, id: 8, held_for: "summary" }, { ...held, id: 7, held_for: null }],
        counts: { held: 3 },
      },
    });
    renderConsole("/settings/notifications");
    const list = await screen.findByRole("list", { name: "Recent deliveries" });
    expect(screen.getByText("3 held back")).toBeInTheDocument();
    const items = within(list).getAllByRole("listitem");
    const at = (index: number): HTMLElement => {
      const item = items[index];
      if (item === undefined) throw new Error(`no delivery ${String(index)}`);
      return item;
    };
    expect(within(at(0)).getByText("cap reached")).toBeInTheDocument();
    expect(within(at(1)).getByText("for the summary")).toBeInTheDocument();
    // A delivery held before the cause was recorded was held by the quiet hours.
    expect(within(at(2)).getByText("quiet hours")).toBeInTheDocument();
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
