import { describe, expect, it } from "vitest";

import type { AlertRuleList } from "../../../api/queries/alerts";
import { acknowledgement, selectFiringAlerts } from "./data";

const NOW = 1_790_000_000;

type RuleStates = NonNullable<AlertRuleList["items"][number]["states"]>;

function rule(name: string, extra: Record<string, unknown> = {}, states: RuleStates = []): AlertRuleList["items"][number] {
  return {
    name,
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
    states,
    ...extra,
  };
}

function firing(subject: string, extra: Record<string, unknown> = {}): RuleStates[number] {
  return { subject, state: "firing", since: NOW - 600, last_value: 95, announced: true, acknowledged_at: null, acknowledged_by: null, ...extra };
}

function list(items: AlertRuleList["items"]): AlertRuleList {
  return { items, metrics: [], firing: 0 };
}

describe("the alerts that are firing", () => {
  it("is nothing before the rules are read, and nothing when no rule fires", () => {
    expect(selectFiringAlerts(undefined)).toEqual([]);
    expect(selectFiringAlerts(list([rule("cpu-high"), rule("disk", {}, [{ ...firing(""), state: "ok" }])]))).toEqual([]);
  });

  it("lists each subject that fires, with what it reads and since when", () => {
    const found = selectFiringAlerts(list([rule("shop-memory", { metric: "app_memory", scope: "app", unit: "bytes", severity: "failed" }, [firing("shop.example.com", { last_value: 620_000_000 }), firing("blog.example.com")])]));
    expect(found.map((alert) => [alert.rule, alert.subject])).toEqual([
      ["shop-memory", "shop.example.com"],
      ["shop-memory", "blog.example.com"],
    ]);
    expect(found[0]).toMatchObject({ severity: "failed", value: 620_000_000, unit: "bytes", since: NOW - 600, acknowledgement: null });
  });

  it("leaves out a rule that is switched off, whatever it last decided", () => {
    expect(selectFiringAlerts(list([rule("cpu-high", { enabled: false }, [firing("")])]))).toEqual([]);
  });

  it("puts failures first, then the ones nobody has acknowledged, then the oldest", () => {
    const found = selectFiringAlerts(
      list([
        rule("a-warning", {}, [firing("", { since: NOW - 100 })]),
        rule("b-failure", { severity: "failed" }, [firing("", { since: NOW - 50 })]),
        rule("c-acknowledged", {}, [firing("", { since: NOW - 900, acknowledged_by: "alice", acknowledged_at: NOW - 60 })]),
        rule("d-old", {}, [firing("", { since: NOW - 5000 })]),
      ]),
    );
    expect(found.map((alert) => alert.rule)).toEqual(["b-failure", "d-old", "a-warning", "c-acknowledged"]);
  });

  it("says who acknowledged an alert and when", () => {
    const [alert] = selectFiringAlerts(list([rule("cpu-high", {}, [firing("", { acknowledged_by: "alice", acknowledged_at: NOW - 30 })])]));
    expect(alert?.acknowledgement).toEqual({ by: "alice", at: NOW - 30 });
    expect(acknowledgement(firing("", { acknowledged_by: "alice", acknowledged_at: NOW - 30 }))).toEqual({ by: "alice", at: NOW - 30 });
  });

  it("does not take half an acknowledgement for one", () => {
    expect(acknowledgement(firing("", { acknowledged_by: "alice", acknowledged_at: null }))).toBeNull();
    expect(acknowledgement(firing("", { acknowledged_by: null, acknowledged_at: NOW }))).toBeNull();
    expect(acknowledgement(firing(""))).toBeNull();
  });
});
