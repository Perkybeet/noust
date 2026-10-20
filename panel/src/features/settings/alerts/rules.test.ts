import { describe, expect, it } from "vitest";

import type { AlertMetric, AlertRule } from "../../../api/queries/alerts";
import { bindT } from "../../../i18n";
import { appliesTo, emptyRule, formatReading, limitHint, limitText, metricLabel, ruleBody, ruleFormFrom, ruleSentence } from "./rules";

const en = bindT("en");

const METRICS: AlertMetric[] = [
  { id: "cpu", scope: "server", unit: "percent" },
  { id: "app_memory", scope: "app", unit: "bytes" },
  { id: "net_rx", scope: "server", unit: "bytes_s" },
];

function rule(extra: Partial<AlertRule> = {}): AlertRule {
  return {
    name: "cpu-high",
    metric: "cpu",
    scope: "server",
    unit: "percent",
    comparator: ">",
    threshold: 90,
    app_domain: null,
    duration_s: 300,
    hysteresis: 0,
    cooldown_s: 3600,
    severity: "warning",
    enabled: true,
    remind: false,
    note: "",
    states: [],
    ...extra,
  };
}

describe("an alert rule's limit", () => {
  it("is written back the way it would be typed, in the shortest form the server reads exactly", () => {
    expect(limitText("percent", 90)).toBe("90");
    expect(limitText("bytes", 536_870_912)).toBe("512MB");
    expect(limitText("bytes", 2 * 1024 ** 3)).toBe("2GB");
    expect(limitText("bytes_s", 2 * 1024 ** 2)).toBe("2MB/s");
    // Not a whole number of any unit: bytes, which the server reads too.
    expect(limitText("bytes", 1_000_000)).toBe("1000000");
    expect(limitText("per_min", 20)).toBe("20");
  });

  it("is read the way the metric is measured", () => {
    expect(formatReading("percent", 93.4, "en")).toBe("93%");
    expect(formatReading("bytes", 536_870_912, "en")).toBe("512 MB");
    expect(formatReading("bytes_s", 2_097_152, "en")).toBe("2.0 MB/s");
    expect(formatReading("per_min", 119.6, "en")).toBe("120/min");
    expect(formatReading("number", 1.5, "en")).toBe("1.5");
  });

  it("reads a response time in milliseconds, and in seconds once it is a second or more", () => {
    expect(formatReading("ms", 0.4, "en")).toBe("<1 ms");
    expect(formatReading("ms", 250.4, "en")).toBe("250 ms");
    expect(formatReading("ms", 2500, "en")).toMatch(/2\.5\s*s/);
    // A plain number of milliseconds is how it is written back, and the server reads 500ms and 2s too.
    expect(limitText("ms", 500)).toBe("500");
    expect(metricLabel(en, "app_latency_p95")).toBe("Application response time (p95)");
    expect(limitHint(en, "ms")).toMatch(/500ms/);
  });
});

describe("an alert rule's form", () => {
  it("starts from the server's own defaults: five minutes, an hour between messages", () => {
    const form = emptyRule("cpu");
    expect(form).toMatchObject({ metric: "cpu", comparator: ">", minutes: "5", cooldownMinutes: "60", severity: "warning", remind: false });
    expect(ruleBody({ ...form, name: "x", limit: "90%" }, true)).toMatchObject({ duration_s: 300, cooldown_s: 3600, threshold: "90%", app_domain: null });
  });

  it("holds a rule that exists in minutes and in the limit's own spelling, and writes it back in seconds", () => {
    const form = ruleFormFrom(rule({ metric: "app_memory", scope: "app", unit: "bytes", threshold: 536_870_912, app_domain: "shop.example.com", duration_s: 600, cooldown_s: 7200, severity: "failed", remind: true, hysteresis: 5 }), METRICS);
    expect(form).toMatchObject({ limit: "512MB", app: "shop.example.com", minutes: "10", cooldownMinutes: "120", severity: "failed", remind: true, hysteresis: "5" });
    expect(ruleBody(form, false)).toEqual({
      metric: "app_memory",
      comparator: ">",
      threshold: "512MB",
      app_domain: "shop.example.com",
      duration_s: 600,
      hysteresis: 5,
      cooldown_s: 7200,
      severity: "failed",
      enabled: false,
      remind: true,
      note: "",
    });
  });

  it("sends text that is not a number as a value the server refuses, so its message lands beside the field", () => {
    const body = ruleBody({ ...emptyRule("cpu"), name: "x", limit: "plenty", minutes: "soon", cooldownMinutes: "" }, true);
    expect(body.threshold).toBe("plenty");
    // Out of the range the server accepts, whatever it was meant to be.
    expect(body.duration_s).toBe(0);
    expect(body.cooldown_s).toBe(-1);
  });
});

describe("an alert rule in words", () => {
  it("says what it watches in one sentence, and where it applies", () => {
    expect(ruleSentence(en, rule(), "percent")).toBe("CPU above 90% for 5m 00s");
    expect(ruleSentence(en, rule({ metric: "net_rx", comparator: "<=", threshold: 1_048_576, duration_s: 3600 }), "bytes_s")).toBe("Network received at or below 1.0 MB/s for 1h 0m");
    expect(appliesTo(en, rule(), "server")).toBe("On the server.");
    expect(appliesTo(en, rule({ metric: "app_memory" }), "app")).toBe("On every application.");
    expect(appliesTo(en, rule({ metric: "app_memory", app_domain: "shop.example.com" }), "app")).toBe("On shop.example.com.");
  });

  it("names a metric it does not know by the server's own word for it", () => {
    expect(metricLabel(en, "cpu")).toBe("CPU");
    expect(metricLabel(en, "app_disk_read")).toBe("Application disk read");
    expect(metricLabel(en, "gpu_heat")).toBe("gpu_heat");
  });
});
