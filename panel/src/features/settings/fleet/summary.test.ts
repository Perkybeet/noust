import { describe, expect, it } from "vitest";

import { summarize } from "./summary";

describe("what a setting of the fleet holds, in one line", () => {
  it("says a channel by its type and where it goes, never by a credential", () => {
    expect(summarize("channel", { type: "slack", settings: { channel: "#deploys", webhook_url: "***" } })).toBe("slack, #deploys");
    expect(summarize("channel", { type: "http", settings: { url: "https://hooks.example.net/guardia?token=abc" } })).toBe("http, hooks.example.net");
    expect(summarize("channel", { type: "email" })).toBe("email");
  });

  it("says a rule as the comparison it makes", () => {
    expect(summarize("alert_rule", { metric: "disk", comparator: ">", threshold: 85 })).toBe("disk > 85");
    expect(summarize("alert_rule", { metric: "memory", threshold: 90, duration_s: 600 })).toBe("memory > 90, 10 min");
  });

  it("says a destination by its backend and where it keeps the copies", () => {
    expect(summarize("backup_destination", { backend: "s3", fields: { bucket: "copias-acme", endpoint: "s3.example.net" } })).toBe("s3, copias-acme");
    expect(summarize("backup_destination", { backend: "local", fields: { path: "/var/backups/noust" } })).toBe("local, /var/backups/noust");
  });

  it("says a registry by its kind and its account, and never the password", () => {
    expect(summarize("registry", { kind: "ghcr", username: "acme-bot", password: "***" })).toBe("ghcr, acme-bot");
    expect(summarize("registry", { kind: "custom", server: "registry.example.net", username: "ci", password: "***" })).toBe("registry.example.net, ci");
  });

  it("keeps a secret variable hidden and shows the rest as it is", () => {
    expect(summarize("variable", { value: "***", secret: true })).toBe("***");
    expect(summarize("variable", { value: "Europe/Madrid", secret: false })).toBe("Europe/Madrid");
    expect(summarize("variable", { value: "x".repeat(200) }).length).toBeLessThanOrEqual(81);
  });

  it("says the console address as it is", () => {
    expect(summarize("console_link", { url: "https://consola.example.com" })).toBe("https://consola.example.com");
  });

  it("is empty for a kind it does not know, rather than guessing", () => {
    expect(summarize("something_new", { a: 1 })).toBe("");
  });
});
