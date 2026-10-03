import { describe, expect, it } from "vitest";

import type { Overview } from "../../api/queries/overview";
import type { AppInfo } from "../apps/data";
import { overviewFixture } from "./testFixtures";
import { summaryOf } from "./useFleetSummary";
import { activityDays, appsTotal, attentionItems, attentionTotal, backupsTone, certificatesTone, deploysTone, diskTone, isEmptyServer, unmanagedApps, updatesTone } from "./overviewData";

const app = (domain: string, status: string): AppInfo => ({ domain, name: domain, status, active: true, enabled: true, layout: "inplace" }) as AppInfo;

describe("figure tones", () => {
  it("colours a figure only when it is a problem", () => {
    const calm = overviewFixture();
    expect(diskTone(calm.disk)).toBe("neutral");
    expect(deploysTone(calm.deploys)).toBe("neutral");
    expect(certificatesTone(calm.certificates)).toBe("neutral");
    expect(backupsTone(calm.backups)).toBe("neutral");
    expect(updatesTone(calm.updates)).toBe("neutral");
  });

  it("warns under 20% free or a month to full, fails under 10% or a week", () => {
    const disk = overviewFixture().disk;
    expect(diskTone({ ...disk, free_percent: 15 })).toBe("warn");
    expect(diskTone({ ...disk, forecast_full_days: 20 })).toBe("warn");
    expect(diskTone({ ...disk, free_percent: 8 })).toBe("fail");
    expect(diskTone({ ...disk, forecast_full_days: 3 })).toBe("fail");
  });

  it("fails today's deploys while the newest one failed, and only warns about an earlier failure", () => {
    const deploys = overviewFixture().deploys;
    expect(deploysTone({ ...deploys, failed: 1 })).toBe("warn");
    expect(deploysTone({ ...deploys, failed: 1, last_status: "failed" })).toBe("fail");
  });

  it("names certificates, backups and updates in their states", () => {
    const o = overviewFixture();
    expect(certificatesTone({ ...o.certificates, expiring: 1 })).toBe("warn");
    expect(certificatesTone({ ...o.certificates, expired: 1 })).toBe("fail");
    expect(backupsTone({ ...o.backups, unprotected_scheduled: 1 })).toBe("warn");
    expect(backupsTone({ ...o.backups, failed_24h: 1 })).toBe("fail");
    expect(updatesTone({ ...o.updates, security: 2 })).toBe("warn");
    expect(updatesTone({ ...o.updates, reboot_required: true })).toBe("warn");
  });

  it("stays calm about a figure that could not be read: it says so in words instead", () => {
    expect(diskTone({ ...overviewFixture().disk, free_percent: 1, error: "df: /var/www: No such file" })).toBe("neutral");
  });
});

describe("isEmptyServer", () => {
  it("is a server that runs nothing yet", () => {
    expect(isEmptyServer(overviewFixture({ apps: { running: 0, failed: 0, stopped: 0, static: 0, unmanaged: 0, error: null } }))).toBe(true);
    expect(isEmptyServer(overviewFixture())).toBe(false);
    expect(isEmptyServer(overviewFixture({ apps: { running: 0, failed: 0, stopped: 0, static: 0, unmanaged: 0, error: "systemctl: not found" } }))).toBe(false);
  });
});

describe("an overview from a node before 3.3", () => {
  // A central reads a node's overview through the proxy; 3.2.1 sends no `unmanaged`.
  const fromOld = (apps: Record<string, unknown>) => overviewFixture({ apps: apps as unknown as Overview["apps"] });

  it("counts no stack outside its unit instead of NaN", () => {
    const apps = fromOld({ running: 3, failed: 0, stopped: 1, static: 1, error: null }).apps;
    expect(unmanagedApps(apps)).toBe(0);
    expect(appsTotal(apps)).toBe(5);
  });

  it("is still an empty server when it runs nothing", () => {
    expect(isEmptyServer(fromOld({ running: 0, failed: 0, stopped: 0, static: 0, error: null }))).toBe(true);
  });
});

describe("attentionItems", () => {
  const failed = {
    id: "app:admin.example.com",
    subject: { kind: "app", domain: "admin.example.com" },
    title: "admin.example.com",
    severity: "fail",
    reasons: [{ kind: "state", severity: "fail", code: "service_failed", params: {}, detail: null, when: null, deployment_id: null, actions: ["view_log", "diagnose"] }],
  };
  const expiring = {
    id: "app:shop.example.com",
    subject: { kind: "app", domain: "shop.example.com" },
    title: "shop.example.com",
    severity: "warn",
    reasons: [{ kind: "certificate", severity: "warn", code: "certificate_expires_in", params: { days: 12 }, detail: null, when: null, deployment_id: null, actions: ["renew_certificate"] }],
  };

  it("keeps the server's list, worst first", () => {
    const items = attentionItems(overviewFixture({ attention: [expiring, failed], attention_total: 2 }), []);
    expect(items.map((item) => item.id)).toEqual(["app:admin.example.com", "app:shop.example.com"]);
  });

  it("adds a service that runs but answers nothing on its port, which the server cannot see", () => {
    const items = attentionItems(overviewFixture({ attention: [failed], attention_total: 1 }), [app("api.example.com", "no_answer"), app("admin.example.com", "failed")]);
    expect(items.map((item) => [item.id, item.reasons.map((r) => r.code)])).toEqual([
      ["app:admin.example.com", ["service_failed"]],
      ["app:api.example.com", ["no_answer"]],
    ]);
  });

  it("puts it on the application's own item, first, when that item exists for another reason", () => {
    const items = attentionItems(overviewFixture({ attention: [expiring], attention_total: 1 }), [app("shop.example.com", "no_answer")]);
    expect(items).toHaveLength(1);
    expect(items[0]?.severity).toBe("fail");
    expect(items[0]?.reasons.map((r) => r.code)).toEqual(["no_answer", "certificate_expires_in"]);
  });

  it("counts what the server cut from its list", () => {
    const o = overviewFixture({ attention: [failed], attention_total: 140 });
    const items = attentionItems(o, [app("api.example.com", "no_answer")]);
    expect(attentionTotal(o, items)).toBe(141);
  });
});

describe("activityDays", () => {
  it("groups the timeline by day, newest first, and keeps to the limit", () => {
    const now = new Date(2026, 8, 29, 21, 40);
    const entry = (id: string, at: string) => ({ id, type: "deploy", title: "", status: "completed", domain: "shop.example.com", actor: null, at });
    const days = activityDays(
      [entry("a", "2026-09-29T20:00:00"), entry("b", "2026-09-29T08:00:00"), entry("c", "2026-09-28T23:00:00"), entry("d", "2026-09-20T10:00:00"), entry("e", "2026-09-19T10:00:00")],
      now,
      4,
    );
    expect(days.map((day) => [typeof day.day === "string" ? day.day : day.day.getDate(), day.entries.map((e) => e.entry.id)])).toEqual([
      ["today", ["a", "b"]],
      ["yesterday", ["c"]],
      [20, ["d"]],
    ]);
  });
});

describe("fleet summary rows", () => {
  const outcome = (name: string, status: string, extra: Record<string, unknown> = {}) => ({ name, local: name === "web-1", status, missing: [], warnings: [], ...extra });

  it("puts this server first, reads each row from its own overview, and says why a server is missing", () => {
    const summary = summaryOf({
      resource: "summary",
      generated_at: "2026-09-29T21:40:00Z",
      partial: true,
      nodes: [
        outcome("web-1", "ok"),
        outcome("db-1", "unreachable", { code: "node_unreachable", error_verbatim: "ssh: connect to host db-1 port 22: Connection timed out" }),
        outcome("old-1", "unsupported", { version: "3.0.0" }),
      ],
      items: [
        { node: "old-1", local: false, name: "old-1", reachability: "reachable", version: "3.0.0", apps: { running: 2, failed: 0 }, overview: null },
        { node: "db-1", local: false, name: "db-1", reachability: "unreachable", version: null, apps: null, overview: null },
        {
          node: "web-1",
          local: true,
          name: "web-1",
          reachability: "reachable",
          version: "3.1.0",
          apps: { running: 5, failed: 1, stopped: 0, static: 0 },
          overview: overviewFixture({ attention: [{ id: "x", subject: {}, title: "x", severity: "fail", reasons: [] }], attention_total: 3 }),
        },
      ],
    });
    expect(summary.rows.map((row) => [row.key, row.reachability])).toEqual([
      ["local", "reachable"],
      ["node:db-1", "unreachable"],
      ["node:old-1", "reachable"],
    ]);
    expect(summary.rows[0]).toMatchObject({ node: null, apps: { running: 5, failed: 1 }, attention: { count: 3, worst: "fail" } });
    expect(summary.rows[1]?.error).toBe("ssh: connect to host db-1 port 22: Connection timed out");
    expect(summary.rows[2]).toMatchObject({ older: true, attention: null, version: "3.0.0" });
    expect(summary).toMatchObject({ servers: 3, reachable: 2, unreachable: 1 });
  });
});
