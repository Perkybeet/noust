import { describe, expect, it } from "vitest";

import { bindT } from "../../i18n/useT";
import { certificateMention, checkName, checkView, healthReasons, verdictText, verdictView } from "./data";

const t = bindT("en");

describe("verdictView", () => {
  it("draws the three verdicts collect_health_report can answer", () => {
    expect(verdictView("healthy")).toEqual({ state: "running", label: { key: "healthy" } });
    expect(verdictView("warning")).toEqual({ state: "warning", label: { key: "needsAttention" } });
    expect(verdictView("error")).toEqual({ state: "failed", label: { key: "critical" } });
  });

  it("shows an unrecognised verdict verbatim rather than guessing", () => {
    expect(verdictView("mystery")).toEqual({ state: "unknown", label: { key: "unknown", raw: "mystery" } });
    expect(verdictText(t, verdictView("mystery").label)).toBe("mystery");
  });
});

describe("checkView", () => {
  it("draws each HealthCheck status", () => {
    expect(checkView("ok")).toEqual({ state: "running", label: { key: "ok" } });
    expect(checkView("warning")).toEqual({ state: "warning", label: { key: "warning" } });
    expect(checkView("error")).toEqual({ state: "failed", label: { key: "error" } });
    expect(checkView("info")).toEqual({ state: "unknown", label: { key: "info" } });
  });
});

describe("verdictText", () => {
  it("translates every verdict and check label", () => {
    expect(verdictText(t, { key: "healthy" })).toBe("Healthy");
    expect(verdictText(t, { key: "needsAttention" })).toBe("Needs attention");
    expect(verdictText(t, { key: "critical" })).toBe("Critical");
    expect(verdictText(t, { key: "ok" })).toBe("OK");
    expect(verdictText(t, { key: "warning" })).toBe("Warning");
    expect(verdictText(t, { key: "error" })).toBe("Error");
    expect(verdictText(t, { key: "info" })).toBe("Info");
  });
});

describe("checkName", () => {
  it("writes the CLI's check names in sentence case, and leaves others as sent", () => {
    expect(checkName(t, "Disk Space")).toBe("Disk space");
    expect(checkName(t, "SSL Certificates")).toBe("SSL certificates");
    expect(checkName(t, "Nginx")).toBe("Nginx");
  });
});

describe("healthReasons", () => {
  it("lists the issues, then the warnings, in the report's own words", () => {
    const reasons = healthReasons({
      issues: ["Nginx is installed but not running"],
      warnings: ["App 'shop.example.com' - the unit is not running"],
    });
    expect(reasons).toEqual([
      { level: "issue", message: "Nginx is installed but not running", certificate: null },
      { level: "warning", message: "App 'shop.example.com' - the unit is not running", certificate: null },
    ]);
    expect(healthReasons({ issues: [], warnings: [] })).toEqual([]);
  });

  it("finds the certificate a message names, keeping the sentence around it intact", () => {
    expect(certificateMention("Certificate for shop.example.com expired 3 days ago")).toEqual({
      before: "Certificate for ",
      name: "shop.example.com",
      after: " expired 3 days ago",
    });
    expect(certificateMention("Certificate for shop.example.com-0001 expires in 5 days")?.name).toBe("shop.example.com-0001");
    expect(certificateMention("Certificate for x.io has an unreadable expiry date")?.after).toBe(" has an unreadable expiry date");
    expect(certificateMention("Low disk space: 0.5GB free")).toBeNull();
  });
});
