import { describe, expect, it } from "vitest";

import type { Overview } from "../../api/queries/overview";
import type { AppInfo } from "../apps/data";
import { overviewFixture } from "./testFixtures";
import { appsTotal, attentionItems, attentionTotal, isEmptyServer, unmanagedApps } from "./overviewData";

const app = (domain: string, status: string): AppInfo => ({ domain, name: domain, status, active: true, enabled: true, layout: "inplace" }) as AppInfo;

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
