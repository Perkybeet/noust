/**
 * The `notifications` block of the configuration, read into something a form can hold.
 *
 * GET /api/config answers the whole file as an untyped tree with every secret replaced by
 * "" when nothing is stored and "***" when something is (wasm.core.config.redact_secrets) -
 * so the answer says whether a channel has a destination without ever showing it. The console
 * follows the same rule back: a secret field left untouched is sent as "***", which the server
 * resolves to the stored value (wasm.core.config.restore_redacted); a field the operator never
 * touched is otherwise sent back exactly as it was read, so saving one field never blanks
 * another it shares a channel with.
 */

import type { ConsoleConfig, SmtpBody, SmtpSettings, TelegramChat } from "../../api/queries/config";
import { getLocale } from "../../app/locale";
import { translate } from "../../i18n";
import type { Locale, T } from "../../i18n";

/** What the server sends in place of a secret, and accepts back as "keep the stored one". */
export const REDACTED = "***";

export type ChannelId = "webhook" | "slack" | "discord" | "telegram" | "email";

export interface ChannelField {
  /** The key under `notifications.channels.<channel>`. */
  key: string;
  label: string;
  /** Secrets are write-only: masked while typed and never read back. */
  secret: boolean;
  placeholder: string;
  /** Help shown under the field regardless of what is typed: format, consequence, default. */
  description?: string;
  /** A non-blocking warning shown under the field when what is typed looks like a mistake. */
  warn?: (value: string) => string | null;
}

/**
 * Telegram gives a bot's own chat a small positive ID, but a group or supergroup one that is
 * negative (a supergroup's starts with -100). Typing the group's number without its minus sign
 * is the one mistake that looks valid (it is still digits) and sends nothing, silently, because
 * the bot API answers "chat not found" for an ID that belongs to no chat it can reach - so this
 * warns before saving instead of after the first message never arrives. 13+ digits is a
 * supergroup's own id range with the sign stripped; a personal chat's id is far shorter.
 */
export function telegramChatIdWarning(value: string, locale: Locale = getLocale()): string | null {
  const trimmed = value.trim();
  if (!/^\d{13,}$/.test(trimmed)) return null;
  return translate(locale, "settings.notifications.telegram.idWarning", { value: trimmed });
}

export interface ChannelSpec {
  id: ChannelId;
  label: string;
  description: string;
  fields: readonly ChannelField[];
}

/** The channel ids and their field keys, in the notifier's delivery order (wasm.core.notifier.CHANNELS). */
export const CHANNEL_IDS: readonly ChannelId[] = ["webhook", "slack", "discord", "telegram", "email"];

const CHANNEL_FIELD_KEYS: Readonly<Record<ChannelId, readonly string[]>> = {
  webhook: ["webhook_url"],
  slack: ["webhook_url"],
  discord: ["webhook_url"],
  telegram: ["bot_token", "chat_id"],
  email: [],
};

/** The channels, translated, in the notifier's delivery order. */
export function channels(t: T): readonly ChannelSpec[] {
  return [
    {
      id: "webhook",
      label: t("settings.notifications.channels.webhook.label"),
      description: t("settings.notifications.channels.webhook.description"),
      fields: [
        {
          key: "webhook_url",
          label: t("settings.notifications.channels.webhook.endpointUrlLabel"),
          secret: true,
          placeholder: "https://hooks.example.com/wasm",
        },
      ],
    },
    {
      id: "slack",
      label: t("settings.notifications.channels.slack.label"),
      description: t("settings.notifications.channels.slack.description"),
      fields: [
        {
          key: "webhook_url",
          label: t("settings.notifications.channels.slack.webhookUrlLabel"),
          secret: true,
          placeholder: "https://hooks.slack.com/services/...",
        },
      ],
    },
    {
      id: "discord",
      label: t("settings.notifications.channels.discord.label"),
      description: t("settings.notifications.channels.discord.description"),
      fields: [
        {
          key: "webhook_url",
          label: t("settings.notifications.channels.discord.webhookUrlLabel"),
          secret: true,
          placeholder: "https://discord.com/api/webhooks/...",
        },
      ],
    },
    {
      id: "telegram",
      label: t("settings.notifications.telegram.label"),
      description: t("settings.notifications.telegram.description"),
      fields: [
        { key: "bot_token", label: t("settings.notifications.telegram.botTokenLabel"), secret: true, placeholder: "123456789:AAH..." },
        {
          key: "chat_id",
          label: t("settings.notifications.telegram.chatIdLabel"),
          secret: false,
          placeholder: "-1001234567890",
          description: t("settings.notifications.telegram.chatIdDescription"),
          warn: (value: string) => telegramChatIdWarning(value, t.locale),
        },
      ],
    },
    {
      id: "email",
      label: t("settings.notifications.email.label"),
      description: t("settings.notifications.email.description"),
      fields: [],
    },
  ];
}

export interface EventSpec {
  kind: string;
  label: string;
  description: string;
  /** Set when this version of WASM never sends the event, so the switch changes nothing yet. */
  unsent?: true;
}

/** The event kinds, in the notifier's order (wasm.core.notifier.EVENT_KINDS). */
export const EVENT_KINDS: readonly string[] = [
  "deploy_started",
  "deploy_success",
  "deploy_failed",
  "deploy_rolled_back",
  "cert_expiring",
  "unit_failed",
  "disk_threshold",
  "backup_failed",
];

const UNSENT_EVENTS: ReadonlySet<string> = new Set(["cert_expiring"]);

/** The events, translated, in the notifier's order, described by who sends them. */
export function events(t: T): readonly EventSpec[] {
  const specs: readonly Omit<EventSpec, "unsent">[] = [
    { kind: "deploy_started", label: t("settings.notifications.events.deployStarted.label"), description: t("settings.notifications.events.deployStarted.description") },
    { kind: "deploy_success", label: t("settings.notifications.events.deploySuccess.label"), description: t("settings.notifications.events.deploySuccess.description") },
    { kind: "deploy_failed", label: t("settings.notifications.events.deployFailed.label"), description: t("settings.notifications.events.deployFailed.description") },
    { kind: "deploy_rolled_back", label: t("settings.notifications.events.deployRolledBack.label"), description: t("settings.notifications.events.deployRolledBack.description") },
    { kind: "cert_expiring", label: t("settings.notifications.events.certExpiring.label"), description: t("settings.notifications.events.certExpiring.description") },
    { kind: "unit_failed", label: t("settings.notifications.events.unitFailed.label"), description: t("settings.notifications.events.unitFailed.description") },
    { kind: "disk_threshold", label: t("settings.notifications.events.diskThreshold.label"), description: t("settings.notifications.events.diskThreshold.description") },
    { kind: "backup_failed", label: t("settings.notifications.events.backupFailed.label"), description: t("settings.notifications.events.backupFailed.description") },
  ];
  return specs.map((spec) => (UNSENT_EVENTS.has(spec.kind) ? { ...spec, unsent: true } : spec));
}

export interface SmtpFacts {
  host: string;
  port: number | null;
  from: string;
  recipients: readonly string[];
}

export interface NotificationSettings {
  enabled: boolean;
  events: Record<string, boolean>;
  /** Field values per channel; secrets arrive as "***". */
  channels: Record<ChannelId, Record<string, string>>;
  emailEnabled: boolean;
  allowPrivateHosts: readonly string[];
  smtp: SmtpFacts;
  /** The language WASM writes its own notification text in ("en" or "es"). */
  language: Locale;
}

type Tree = Record<string, unknown>;

function isTree(value: unknown): value is Tree {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function branch(tree: unknown, key: string): Tree {
  if (!isTree(tree)) return {};
  const value = tree[key];
  return isTree(value) ? value : {};
}

function text(tree: Tree, key: string): string {
  const value = tree[key];
  return typeof value === "string" ? value : typeof value === "number" ? String(value) : "";
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

/** Reads the notification settings, and the SMTP account email goes through, out of the configuration. */
export function readNotificationSettings(config: ConsoleConfig["config"]): NotificationSettings {
  const block = branch(config, "notifications");
  const channelsBlock = branch(block, "channels");
  const eventsBlock = branch(block, "events");
  const monitor = branch(config, "monitor");
  const smtp = branch(monitor, "smtp");

  const channelValues = {} as Record<ChannelId, Record<string, string>>;
  for (const id of CHANNEL_IDS) {
    const stored = branch(channelsBlock, id);
    channelValues[id] = Object.fromEntries(CHANNEL_FIELD_KEYS[id].map((key) => [key, text(stored, key)]));
  }
  const eventValues: Record<string, boolean> = {};
  for (const kind of EVENT_KINDS) {
    // The notifier treats an event missing from the file as on (events.get(kind, True)).
    eventValues[kind] = eventsBlock[kind] !== false;
  }
  const port = smtp["port"];
  const language = text(block, "language");
  return {
    enabled: block["enabled"] === true,
    events: eventValues,
    channels: channelValues,
    emailEnabled: branch(channelsBlock, "email")["enabled"] === true,
    allowPrivateHosts: strings(block["allow_private_hosts"]),
    smtp: {
      host: text(smtp, "host"),
      port: typeof port === "number" ? port : null,
      from: text(smtp, "from_address"),
      recipients: strings(monitor["email_recipients"]),
    },
    language: language === "es" ? "es" : "en",
  };
}

/** Whether a channel has a destination: one of its secret fields is a stored "***". */
export function isChannelConfigured(spec: ChannelSpec, stored: Readonly<Record<string, string>>): boolean {
  return spec.fields.some((field) => field.secret && stored[field.key] === REDACTED);
}

/**
 * The value to write at `notifications.channels.<channel>` for a form's fields: what the
 * operator typed, or, for whatever they left alone, exactly what is already stored - so saving
 * one field of a multi-field channel (Telegram's chat ID beside its bot token) never sends the
 * other back blank. A secret left untouched is sent as "***", which the server resolves to the
 * stored value (wasm.core.config.restore_redacted) instead of the literal three characters.
 *
 * @param stored What the channel holds now, as `readNotificationSettings` read it: secrets
 *   already redacted, everything else in clear.
 * @param cleared Secret fields the operator chose to remove: written as "", which turns the
 *   channel off.
 */
export function channelValue(
  spec: ChannelSpec,
  stored: Readonly<Record<string, string>>,
  draft: Readonly<Record<string, string>>,
  cleared: ReadonlySet<string> = new Set(),
): Record<string, string> {
  return Object.fromEntries(
    spec.fields.map((field) => {
      if (cleared.has(field.key)) return [field.key, ""];
      if (field.secret) {
        const typed = (draft[field.key] ?? "").trim();
        return [field.key, typed === "" ? REDACTED : typed];
      }
      const typed = draft[field.key]?.trim();
      return [field.key, typed ?? stored[field.key] ?? ""];
    }),
  );
}

// ---------------------------------------------------------------------------------------
// The console's own address (web.public_url): read here only to build the link a deployment
// notification carries back to the console; it does not move the console itself.

/** `web.public_url`, out of the whole configuration tree - "" when the operator never set one. */
export function publicUrlOf(config: ConsoleConfig["config"]): string {
  return text(branch(config, "web"), "public_url");
}

/** Private hosts as typed, one per line or separated by commas, without blanks or repeats. */
export function parseHostList(value: string): string[] {
  const hosts = value
    .split(/[\s,]+/)
    .map((host) => host.trim().toLowerCase())
    .filter((host) => host !== "");
  return [...new Set(hosts)];
}

// ---------------------------------------------------------------------------------------
// Email: the monitor's SMTP account (GET/PUT /api/config/smtp)

/**
 * How the connection to the SMTP server is secured. The configuration holds two switches,
 * `use_ssl` and `use_tls`, that must never both be on, so the form offers the three
 * combinations that describe a real connection as one choice.
 */
export type SmtpSecurity = "ssl" | "starttls" | "none";

const SMTP_SECURITY_PORTS: Readonly<Record<SmtpSecurity, number>> = { ssl: 465, starttls: 587, none: 25 };

export interface SmtpSecurityOption {
  value: SmtpSecurity;
  label: string;
  port: number;
  description: string;
}

/** The three TLS combinations, translated, each naming its usual port. */
export function smtpSecurityOptions(t: T): readonly SmtpSecurityOption[] {
  return [
    {
      value: "ssl",
      label: t("settings.notifications.email.security.ssl.label"),
      port: SMTP_SECURITY_PORTS.ssl,
      description: t("settings.notifications.email.security.ssl.description"),
    },
    {
      value: "starttls",
      label: t("settings.notifications.email.security.starttls.label"),
      port: SMTP_SECURITY_PORTS.starttls,
      description: t("settings.notifications.email.security.starttls.description"),
    },
    {
      value: "none",
      label: t("settings.notifications.email.security.none.label"),
      port: SMTP_SECURITY_PORTS.none,
      description: t("settings.notifications.email.security.none.description"),
    },
  ];
}

export interface SmtpForm {
  host: string;
  /** As typed: the server rules on whether it is a port. */
  port: string;
  security: SmtpSecurity;
  username: string;
  /** Write-only: empty keeps the stored password. */
  password: string;
  from_address: string;
  recipients: readonly string[];
}

export type SmtpField = "host" | "port" | "security" | "username" | "password" | "from_address" | "recipients";

export const SMTP_FIELDS: readonly SmtpField[] = ["host", "port", "security", "username", "password", "from_address", "recipients"];

/**
 * The model field each configuration key is validated under (wasm.core.config._KEY_VALIDATORS),
 * so a refusal naming the key lands beside the field that holds it.
 */
export const SMTP_CONFIG_KEYS: Readonly<Record<string, SmtpField>> = {
  "monitor.smtp.host": "host",
  "monitor.smtp.port": "port",
  "monitor.smtp.use_ssl": "security",
  "monitor.smtp.use_tls": "security",
  "monitor.smtp.username": "username",
  "monitor.smtp.password": "password",
  "monitor.smtp.from_address": "from_address",
  "monitor.email_recipients": "recipients",
};

export function smtpSecurityOf(settings: Pick<SmtpSettings, "use_ssl" | "use_tls">): SmtpSecurity {
  if (settings.use_ssl) return "ssl";
  return settings.use_tls ? "starttls" : "none";
}

/** The form's starting values: what the server holds, with the password field empty. */
export function smtpFormFrom(settings: SmtpSettings): SmtpForm {
  return {
    host: settings.host,
    port: String(settings.port),
    security: smtpSecurityOf(settings),
    username: settings.username,
    password: "",
    from_address: settings.from_address,
    recipients: settings.recipients,
  };
}

/**
 * Whether a form differs from what is stored. A typed password is a change; an empty one keeps
 * the stored password, so it is not.
 */
export function smtpFormDirty(form: SmtpForm, stored: SmtpForm): boolean {
  return (
    form.host !== stored.host ||
    form.port.trim() !== stored.port ||
    form.security !== stored.security ||
    form.username !== stored.username ||
    form.password !== "" ||
    form.from_address !== stored.from_address ||
    form.recipients.length !== stored.recipients.length ||
    form.recipients.some((address, index) => address !== stored.recipients[index])
  );
}

/**
 * The PUT body for a form. The port goes as a number when it is one; anything else is sent as
 * typed, so the server's own message about it lands beside the field instead of the console
 * inventing one.
 */
export function smtpBody(form: SmtpForm): SmtpBody {
  const typedPort = form.port.trim();
  const port = /^\d+$/.test(typedPort) ? Number(typedPort) : (typedPort as unknown as number);
  return {
    host: form.host.trim(),
    port,
    use_ssl: form.security === "ssl",
    use_tls: form.security === "starttls",
    username: form.username.trim(),
    password: form.password,
    from_address: form.from_address.trim(),
    recipients: [...form.recipients],
  };
}

/**
 * When the security choice changes and the port is still the old choice's usual one, the new
 * choice's usual port; otherwise the port as it is, since the operator chose it on purpose.
 */
export function portForSecurity(port: string, from: SmtpSecurity, to: SmtpSecurity): string {
  const previous = SMTP_SECURITY_PORTS[from];
  const next = SMTP_SECURITY_PORTS[to];
  return port.trim() === String(previous) ? String(next) : port;
}

/** The same shape `wasm.core.config._EMAIL_PATTERN` accepts: something@something.tld, no spaces. */
const EMAIL_SHAPE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export function looksLikeEmail(value: string): boolean {
  return EMAIL_SHAPE.test(value);
}

/** Addresses typed into the recipients box: one or several, separated by commas, semicolons or spaces. */
export function splitAddresses(value: string): string[] {
  return value
    .split(/[\s,;]+/)
    .map((address) => address.trim())
    .filter((address) => address !== "");
}

/**
 * The addresses a refusal of `monitor.email_recipients` names: the server lists the invalid
 * ones after the colon ("... contains an invalid email address: a, b").
 */
export function refusedRecipients(message: string | undefined, recipients: readonly string[]): ReadonlySet<string> {
  if (message === undefined) return new Set();
  const colon = message.indexOf(": ");
  if (!message.startsWith("monitor.email_recipients") || colon === -1) return new Set();
  // The hint follows the list after a sentence break; only exact addresses of the form count.
  const named = message.slice(colon + 2).split(/,\s*|\s+/);
  return new Set(recipients.filter((address) => named.includes(address)));
}

// ---------------------------------------------------------------------------------------
// Telegram: the chats the bot has seen

/** A chat's type in words. */
export function telegramChatType(type: string, locale: Locale = getLocale()): string {
  switch (type) {
    case "private":
      return translate(locale, "settings.notifications.telegram.chatType.private");
    case "group":
      return translate(locale, "settings.notifications.telegram.chatType.group");
    case "supergroup":
      return translate(locale, "settings.notifications.telegram.chatType.supergroup");
    case "channel":
      return translate(locale, "settings.notifications.telegram.chatType.channel");
    default:
      return type;
  }
}

/** What a chat is called: its title, else its @username, else its type. */
export function telegramChatName(chat: TelegramChat, locale: Locale = getLocale()): string {
  if (chat.title) return chat.title;
  if (chat.username) return `@${chat.username}`;
  return telegramChatType(chat.type, locale);
}
