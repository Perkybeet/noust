import { describe, expect, it } from "vitest";

import { bindT, loadCatalog } from "../../i18n";
import {
  EVENT_GROUPS,
  EVENT_KINDS,
  REDACTED,
  channelValue,
  channels,
  eventSpec,
  events,
  isChannelConfigured,
  parseHostList,
  looksLikeEmail,
  portForSecurity,
  publicUrlOf,
  readNotificationSettings,
  refusedRecipients,
  smtpBody,
  smtpFormDirty,
  smtpFormFrom,
  smtpSecurityOptions,
  splitAddresses,
  telegramChatIdWarning,
  telegramChatName,
  telegramChatType,
} from "./notifications";
import type { ChannelSpec } from "./notifications";

const en = bindT("en");

/** GET /api/config's notifications block as the sandboxed server answers it. */
const CONFIG = {
  notifications: {
    enabled: true,
    events: { deploy_success: false, deploy_failed: true },
    channels: {
      webhook: { webhook_url: "***" },
      slack: { webhook_url: "***" },
      discord: { webhook_url: "***" },
      telegram: { bot_token: "***", chat_id: "-1001234" },
      email: { enabled: true },
    },
    allow_private_hosts: ["10.0.0.12"],
  },
  monitor: { smtp: { host: "smtp.example.com", port: 465, from_address: "noust@example.com", password: "***" }, email_recipients: ["ops@example.com"] },
};

function spec(id: string): ChannelSpec {
  const found = channels(en).find((channel) => channel.id === id);
  if (!found) throw new Error(id);
  return found;
}

describe("the notification settings", () => {
  it("reads the block, treating an event missing from the file as the notifier does", () => {
    const settings = readNotificationSettings(CONFIG);
    expect(settings.enabled).toBe(true);
    expect(settings.events).toMatchObject({ deploy_success: false, deploy_failed: true, unit_failed: true, backup_failed: true });
    // Missing from the file: on, except the few that ship off.
    expect(settings.events).toMatchObject({ restore_failed: true, node_unreachable: true, deploy_started: false, backup_success: false });
    expect(readNotificationSettings({ notifications: { events: { backup_success: true } } }).events["backup_success"]).toBe(true);
    expect(settings.channels.telegram).toEqual({ bot_token: REDACTED, chat_id: "-1001234" });
    expect(settings.emailEnabled).toBe(true);
    expect(settings.allowPrivateHosts).toEqual(["10.0.0.12"]);
    expect(settings.smtp).toEqual({ host: "smtp.example.com", port: 465, from: "noust@example.com", recipients: ["ops@example.com"] });
  });

  it("reads an empty configuration without inventing anything", () => {
    const settings = readNotificationSettings({});
    expect(settings.enabled).toBe(false);
    expect(settings.channels.slack).toEqual({ webhook_url: "" });
    expect(settings.smtp.host).toBe("");
    expect(settings.language).toBe("en");
  });

  it("reads the language Noust's own notification text is written in", () => {
    expect(readNotificationSettings({ notifications: { language: "es" } }).language).toBe("es");
    expect(readNotificationSettings({ notifications: { language: "fr" } }).language).toBe("en");
  });

  it("sends a secret left empty back as the placeholder, so the stored one is kept", () => {
    expect(channelValue(spec("slack"), { webhook_url: "" }, {})).toEqual({ webhook_url: REDACTED });
    expect(channelValue(spec("telegram"), { bot_token: REDACTED, chat_id: "-1001234" }, { chat_id: " 42 " })).toEqual({
      bot_token: REDACTED,
      chat_id: "42",
    });
  });

  it("sends what was typed, and clears on request", () => {
    expect(channelValue(spec("webhook"), { webhook_url: "", secret: "" }, { webhook_url: "https://hooks.example.com/x " })).toEqual({
      webhook_url: "https://hooks.example.com/x",
      // Left alone: the stored secret (none here) is kept.
      secret: REDACTED,
    });
    expect(channelValue(spec("webhook"), { webhook_url: REDACTED, secret: REDACTED }, {}, new Set(["secret"]))).toEqual({
      webhook_url: REDACTED,
      secret: "",
    });
    expect(
      channelValue(spec("telegram"), { bot_token: REDACTED, chat_id: "-1001234" }, { chat_id: "42" }, new Set(["bot_token"])),
    ).toEqual({ bot_token: "", chat_id: "42" });
  });

  it("keeps a field the operator left alone, even when a sibling field in the same channel changed", () => {
    const stored = { bot_token: REDACTED, chat_id: "-1001234" };
    // Only the token was touched: the chat ID must round-trip untouched, not blank.
    expect(channelValue(spec("telegram"), stored, { bot_token: "a-fresh-token" })).toEqual({
      bot_token: "a-fresh-token",
      chat_id: "-1001234",
    });
  });

  it("reports whether a channel has a destination configured, from the redacted secret alone", () => {
    expect(isChannelConfigured(spec("slack"), { webhook_url: "" })).toBe(false);
    expect(isChannelConfigured(spec("slack"), { webhook_url: REDACTED })).toBe(true);
    expect(isChannelConfigured(spec("telegram"), { bot_token: "", chat_id: "-1001234" })).toBe(false);
    expect(isChannelConfigured(spec("telegram"), { bot_token: REDACTED, chat_id: "-1001234" })).toBe(true);
    // A signing secret alone is nowhere to send to.
    expect(isChannelConfigured(spec("webhook"), { webhook_url: "", secret: REDACTED })).toBe(false);
    expect(isChannelConfigured(spec("webhook"), { webhook_url: REDACTED, secret: "" })).toBe(true);
  });

  it("reads a list of hosts typed one per line or with commas", () => {
    expect(parseHostList("10.0.0.12\n  Hooks.Internal , 10.0.0.12\n\n")).toEqual(["10.0.0.12", "hooks.internal"]);
    expect(parseHostList("   ")).toEqual([]);
  });

  it("offers every event the notifier sends, each in exactly one group, the ones that ship off marked", () => {
    // noust.core.notifications.model.EVENT_KINDS, in its order.
    expect([...EVENT_KINDS]).toEqual([
      "deploy_started",
      "deploy_success",
      "deploy_failed",
      "deploy_rolled_back",
      "deploy_hook_failed",
      "restore_success",
      "restore_failed",
      "cert_expiring",
      "unit_failed",
      "app_unreachable",
      "app_recovered",
      "disk_threshold",
      "backup_failed",
      "backup_success",
      "node_unreachable",
      "node_recovered",
      "node_host_key_changed",
      "server_rebooted",
      "server_back",
      "approval_requested",
      "approval_decided",
    ]);
    const grouped = EVENT_GROUPS.flatMap((group) => group.kinds);
    expect([...grouped].sort()).toEqual([...EVENT_KINDS].sort());
    expect(new Set(grouped).size).toBe(grouped.length);
    expect(events(en).filter((event) => event.offByDefault === true).map((event) => event.kind)).toEqual(["deploy_started", "backup_success"]);
    expect(eventSpec(en, "node_host_key_changed").label).toBe("Server's identity changed");
    expect(eventSpec(en, "deploy_hook_failed").label).toBe("Deployed with warnings");
  });

  it("translates the events and channels into Spanish, key for key", async () => {
    await loadCatalog("es");
    const es = bindT("es");
    expect(events(es).map((event) => event.kind)).toEqual(events(en).map((event) => event.kind));
    expect(events(es).find((event) => event.kind === "deploy_rolled_back")?.label).toBe("Despliegue revertido");
    expect(channels(es).map((channel) => channel.id)).toEqual(channels(en).map((channel) => channel.id));
    expect(channels(es).find((channel) => channel.id === "telegram")?.label).toBe("Telegram");
  });

  it("reads the console's own public address, unset by default", () => {
    expect(publicUrlOf({})).toBe("");
    expect(publicUrlOf({ web: { public_url: "https://console.example.com" } })).toBe("https://console.example.com");
  });
});

describe("telegramChatIdWarning", () => {
  it("says nothing about a negative ID, the correct shape for a group or supergroup", () => {
    expect(telegramChatIdWarning("-1001234567890")).toBeNull();
  });

  it("says nothing about a short positive ID, the shape of a personal chat", () => {
    expect(telegramChatIdWarning("123456789")).toBeNull();
  });

  it("says nothing about an empty or non-numeric value: a different problem, not this one", () => {
    expect(telegramChatIdWarning("")).toBeNull();
    expect(telegramChatIdWarning("not-a-number")).toBeNull();
  });

  it("warns about a 13+ digit positive number: a group ID typed without its minus sign", () => {
    expect(telegramChatIdWarning("1001234567890")).toBe(
      "This looks like a group's chat ID without its minus sign. Groups and supergroups use a negative ID (a supergroup's starts with -100); try -1001234567890.",
    );
  });

  it("ignores surrounding whitespace", () => {
    expect(telegramChatIdWarning("  1001234567890  ")).not.toBeNull();
  });

  it("says it in Spanish when asked to", async () => {
    await loadCatalog("es");
    expect(telegramChatIdWarning("1001234567890", "es")).toBe(
      "Esto parece el ID de chat de un grupo sin su signo menos. Los grupos y supergrupos usan un ID negativo (el de un supergrupo empieza por -100); prueba con -1001234567890.",
    );
  });
});

describe("telegramChatType and telegramChatName, in Spanish", () => {
  it("names a chat's type in Spanish", async () => {
    await loadCatalog("es");
    expect(telegramChatType("supergroup", "es")).toBe("Supergrupo");
    expect(telegramChatType("something_new", "es")).toBe("something_new");
  });

  it("falls back to the type, translated, when there is neither a title nor a username", async () => {
    await loadCatalog("es");
    expect(telegramChatName({ id: 5, type: "group", title: null, username: null }, "es")).toBe("Grupo");
  });
});

describe("the SMTP form", () => {
  const STORED = {
    host: "smtp.example.com",
    port: 587,
    use_ssl: false,
    use_tls: true,
    username: "noust@example.com",
    from_address: "",
    recipients: ["ops@example.com"],
    password_set: true,
  };

  it("reads the two TLS switches as one choice, and never holds the password", () => {
    const form = smtpFormFrom(STORED);
    expect(form).toEqual({
      host: "smtp.example.com",
      port: "587",
      security: "starttls",
      username: "noust@example.com",
      password: "",
      from_address: "",
      recipients: ["ops@example.com"],
    });
    expect(smtpFormFrom({ ...STORED, use_ssl: true, use_tls: false }).security).toBe("ssl");
    expect(smtpFormFrom({ ...STORED, use_ssl: false, use_tls: false }).security).toBe("none");
  });

  it("is dirty for a typed password or any other change, not for an empty password", () => {
    const stored = smtpFormFrom(STORED);
    expect(smtpFormDirty(stored, stored)).toBe(false);
    expect(smtpFormDirty({ ...stored, password: "x" }, stored)).toBe(true);
    expect(smtpFormDirty({ ...stored, port: " 587 " }, stored)).toBe(false);
    expect(smtpFormDirty({ ...stored, recipients: ["ops@example.com", "dev@example.com"] }, stored)).toBe(true);
    expect(smtpFormDirty({ ...stored, security: "ssl" }, stored)).toBe(true);
  });

  it("writes the choice back as the two switches, a port as a number, and anything else as typed", () => {
    const form = { ...smtpFormFrom(STORED), host: " smtp.example.com ", security: "ssl" as const, port: "465" };
    expect(smtpBody(form)).toEqual({
      host: "smtp.example.com",
      port: 465,
      use_ssl: true,
      use_tls: false,
      username: "noust@example.com",
      password: "",
      from_address: "",
      recipients: ["ops@example.com"],
    });
    // The server rules on what is not a port; the console does not invent a message for it.
    expect(smtpBody({ ...form, port: "abc" }).port).toBe("abc");
  });

  it("moves the port with the choice only while it is the old choice's usual one", () => {
    expect(portForSecurity("465", "ssl", "starttls")).toBe("587");
    expect(portForSecurity("587", "starttls", "none")).toBe("25");
    expect(portForSecurity("2525", "ssl", "starttls")).toBe("2525");
  });

  it("translates the encryption choices, keeping their usual ports", async () => {
    await loadCatalog("es");
    const es = bindT("es");
    expect(smtpSecurityOptions(es).map((option) => option.port)).toEqual(smtpSecurityOptions(en).map((option) => option.port));
    expect(smtpSecurityOptions(es).find((option) => option.value === "starttls")?.label).toBe("STARTTLS");
  });

  it("checks an address's shape as noust.core.config does, and splits a pasted list", () => {
    expect(looksLikeEmail("ops@example.com")).toBe(true);
    expect(looksLikeEmail("ops@example")).toBe(false);
    expect(looksLikeEmail("o ps@example.com")).toBe(false);
    expect(splitAddresses(" a@x.io, b@y.io;c@z.io  d@w.io ")).toEqual(["a@x.io", "b@y.io", "c@z.io", "d@w.io"]);
  });

  it("finds the addresses the server named in its refusal of the list", () => {
    const message = "monitor.email_recipients contains an invalid email address: a@b, c@d Every recipient must be an address such as ops@example.com.";
    expect([...refusedRecipients(message, ["a@b", "ops@example.com", "c@d"])]).toEqual(["a@b", "c@d"]);
    expect(refusedRecipients(undefined, ["a@b"]).size).toBe(0);
    expect(refusedRecipients("monitor.smtp.host is not a valid hostname: a@b", ["a@b"]).size).toBe(0);
  });
});

describe("Telegram chats", () => {
  it("names a chat by its title, else its username, else its type", () => {
    expect(telegramChatName({ id: -100, type: "supergroup", title: "Alerts", username: "alerts" })).toBe("Alerts");
    expect(telegramChatName({ id: 5, type: "private", title: null, username: "yago" })).toBe("@yago");
    expect(telegramChatName({ id: 5, type: "private", title: null, username: null })).toBe("Private chat");
    expect(telegramChatType("channel")).toBe("Channel");
    expect(telegramChatType("something_new")).toBe("something_new");
  });
});
