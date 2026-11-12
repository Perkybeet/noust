import { describe, expect, it } from "vitest";

import type { CollectingNode } from "../../../api/queries/centralBackups";
import { bodyOf, followChoice, followValue, formOf, knobsOf, nodeView, problemsOf, roomOf } from "./central";

function node(over: Partial<CollectingNode> = {}): CollectingNode {
  return {
    node: "web-1",
    follow: null,
    following: true,
    last_pass_at: "2026-11-10T02:05:00+00:00",
    last_ok_at: "2026-11-10T02:05:00+00:00",
    last_error: null,
    last_error_code: null,
    failures: 0,
    support: { mode: "full" },
    held: { count: 2, bytes: 4096 },
    newest: null,
    moving: null,
    ...over,
  };
}

describe("the knobs and the room, read from a status", () => {
  it("reads the knobs the API sent, and none for a caller that is not shown them", () => {
    const knobs = knobsOf({
      settings: { enabled: true, kinds: ["app", "noust"], rate_limit_kbps: 2048, retention_count: 30, retention_days: null, retention_gb: 1.5, min_free_gb: 2 },
    });

    expect(knobs).toEqual({ enabled: true, kinds: ["app", "noust"], rateLimitKbps: 2048, retentionCount: 30, retentionDays: null, retentionGb: 1.5, minFreeGb: 2 });
    expect(knobsOf({ settings: null })).toBeNull();
    expect(knobsOf(undefined)).toBeNull();
  });

  it("takes every kind when the answer names none the console knows, instead of drawing a form that sends none", () => {
    expect(knobsOf({ settings: { enabled: false, kinds: ["telepathy"] } })?.kinds).toEqual(["app", "database", "volume", "noust"]);
  });

  it("reads the room, and none when the disk could not be read", () => {
    expect(roomOf({ space: { total: 100, free: 40, used: 30, min_free: 5, available: 35, low: false } })).toEqual({ total: 100, free: 40, used: 30, minFree: 5, low: false });
    expect(roomOf({ space: { total: 0, free: 0 } })).toBeNull();
    expect(roomOf({ space: null })).toBeNull();
  });
});

describe("the settings form", () => {
  it("is empty where there is no limit, and gives the same knobs back", () => {
    const knobs = knobsOf({ settings: { enabled: true, kinds: ["app"], rate_limit_kbps: null, retention_count: 30, retention_days: null, retention_gb: null, min_free_gb: 1 } });
    const form = formOf(knobs);

    expect(form).toEqual({ enabled: true, kinds: ["app"], speed: "", count: "30", days: "", gb: "", minFree: "1" });
    expect(bodyOf(form)).toEqual({ enabled: true, kinds: ["app"], rate_limit_kbps: 0, retention_count: 30, retention_days: 0, retention_gb: 0, min_free_gb: 1 });
  });

  it("sends 0 for an emptied limit: leaving it out would keep what was there", () => {
    const form = { ...formOf(null), speed: "", count: "", days: "", gb: "" };

    expect(bodyOf(form)).toMatchObject({ rate_limit_kbps: 0, retention_count: 0, retention_days: 0, retention_gb: 0 });
  });

  it("sends the kinds in the API's order whatever order they were ticked in", () => {
    expect(bodyOf({ ...formOf(null), kinds: ["noust", "app"] }).kinds).toEqual(["app", "noust"]);
  });

  it("accepts a decimal with a comma, as it is typed in Spanish", () => {
    expect(bodyOf({ ...formOf(null), gb: "1,5", minFree: "0,5" })).toMatchObject({ retention_gb: 1.5, min_free_gb: 0.5 });
  });

  it("names the field that is wrong, and nothing for a form that is right", () => {
    expect(problemsOf(formOf(null))).toEqual({});
    expect(problemsOf({ ...formOf(null), speed: "8" }).speed).toBe("centralBackups.settings.speedInvalid");
    expect(problemsOf({ ...formOf(null), speed: "20000000" }).speed).toBe("centralBackups.settings.speedInvalid");
    expect(problemsOf({ ...formOf(null), speed: "fast" }).speed).toBe("centralBackups.settings.speedInvalid");
    expect(problemsOf({ ...formOf(null), count: "0" }).count).toBe("centralBackups.settings.notNumber");
    expect(problemsOf({ ...formOf(null), days: "1.5" }).days).toBe("centralBackups.settings.notNumber");
    expect(problemsOf({ ...formOf(null), gb: "-1" }).gb).toBe("centralBackups.settings.notAmount");
    expect(problemsOf({ ...formOf(null), minFree: "lots" }).minFree).toBe("centralBackups.settings.notAmount");
    expect(problemsOf({ ...formOf(null), kinds: [] }).kinds).toBe("centralBackups.settings.kindsRequired");
  });

  it("allows no room to be kept free, which is an amount of zero", () => {
    expect(problemsOf({ ...formOf(null), minFree: "0" })).toEqual({});
    expect(problemsOf({ ...formOf(null), minFree: "" })).toEqual({});
  });
});

describe("how a server reads in the list", () => {
  it("says a server that is not collected before anything else", () => {
    expect(nodeView(node({ following: false, last_error_code: "unreachable" }))).toMatchObject({ status: "stopped", label: "centralBackups.servers.state.off" });
  });

  it("says which copy is being taken", () => {
    expect(nodeView(node({ moving: { item: "app: shop.example.com", file: "a.tar.gz", done: 10, size: 100 } }))).toEqual({
      status: "deploying",
      label: null,
      moving: "app: shop.example.com",
    });
  });

  it.each([
    ["unreachable", "unreachable"],
    ["refused", "refused"],
    ["ceiling", "ceiling"],
    ["disk_full", "diskFull"],
    ["mismatch", "mismatch"],
  ])("tells %s with its own words and its fix", (code, key) => {
    expect(nodeView(node({ last_error: "x", last_error_code: code }))).toMatchObject({
      status: "failed",
      label: `centralBackups.servers.state.${key}`,
      hint: `centralBackups.servers.hint.${key}`,
    });
  });

  it("says a failure it has no words for as a failure, never as up to date", () => {
    expect(nodeView(node({ last_error: "something new", last_error_code: "brand_new" }))).toMatchObject({ status: "failed", label: "centralBackups.servers.state.failed" });
    expect(nodeView(node({ last_error: "something", last_error_code: null }))).toMatchObject({ status: "failed" });
  });

  it("says a server before 4.3 gives only its database dumps, with how to change that", () => {
    expect(nodeView(node({ support: { mode: "databases_only" } }))).toEqual({
      status: "warning",
      label: "centralBackups.servers.state.databasesOnly",
      hint: "centralBackups.servers.hint.databasesOnly",
    });
  });

  it("waits for the first look, and then is up to date", () => {
    expect(nodeView(node({ last_ok_at: null, last_pass_at: null, support: {} })).label).toBe("centralBackups.servers.state.waiting");
    expect(nodeView(node())).toMatchObject({ status: "running", label: "centralBackups.servers.state.upToDate" });
  });
});

describe("a server's own answer", () => {
  it("is on, off or the fleet's, and goes back to the API as true, false or null", () => {
    expect(followChoice({ follow: true })).toBe("on");
    expect(followChoice({ follow: false })).toBe("off");
    expect(followChoice({ follow: null })).toBe("fleet");
    expect([followValue("on"), followValue("off"), followValue("fleet")]).toEqual([true, false, null]);
  });
});
