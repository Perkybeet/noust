import { describe, expect, it } from "vitest";

import { MACHINE } from "../../test/fakes";
import type { AppInfo, Deployment } from "../apps/data";
import { collectAttention } from "./attention";

function app(domain: string, status: string): AppInfo {
  return { domain, name: domain, status, active: status === "running", enabled: true, app_type: "nextjs", layout: "inplace", webhook_enabled: false, keep_releases: 5, zero_downtime: false };
}

function deploy(id: number, domain: string, status: string, error: string | null = null): Deployment {
  return {
    id,
    domain,
    status,
    triggered_by: "panel",
    git_commit: "c07d5e3",
    git_branch: "main",
    started_at: "2026-09-25T19:20:35",
    finished_at: "2026-09-25T19:21:02",
    duration_s: 27,
    error,
    has_log: true,
    rollback_available: false,
  };
}

const HEALTHY = { ...MACHINE, units: { running: 3, failed: 0, stopped: 1 } };

describe("collectAttention", () => {
  it("is empty on a healthy machine", () => {
    expect(
      collectAttention({
        apps: [app("shop.example.net", "running"), app("example.org", "stopped")],
        deployments: [deploy(2, "shop.example.net", "success")],
        certificates: [],
        observations: [],
        machine: HEALTHY,
      }),
    ).toEqual([]);
  });

  it("names an app whose newest deploy failed, with the first line of the error verbatim", () => {
    const items = collectAttention({
      apps: [app("clientes.example.com", "stopped")],
      deployments: [
        deploy(12, "clientes.example.com", "failed", "npm ERR! code ELIFECYCLE\nnpm ERR! errno 1"),
        deploy(11, "clientes.example.com", "success"),
      ],
      machine: HEALTHY,
    });
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      title: "clientes.example.com",
      severity: "fail",
      subject: { kind: "app", domain: "clientes.example.com" },
      reasons: [{ summary: { key: "deployFailed" }, detail: "npm ERR! code ELIFECYCLE", deploymentId: 12 }],
    });
  });

  it("forgets a failure an app has since deployed over", () => {
    const items = collectAttention({
      apps: [app("shop.example.net", "running")],
      deployments: [deploy(13, "shop.example.net", "success"), deploy(12, "shop.example.net", "failed", "boom")],
    });
    expect(items).toEqual([]);
  });

  it("ignores the history of an app that no longer exists", () => {
    expect(collectAttention({ apps: [], deployments: [deploy(5, "gone.example.com", "failed")] })).toEqual([]);
  });

  it("names apps in a problem state, from any of the backend's vocabularies", () => {
    const items = collectAttention({ apps: [app("a.example.com", "Restarting"), app("b.example.com", "failed")] });
    expect(items.map((item) => [item.title, item.severity])).toEqual([
      ["b.example.com", "fail"],
      ["a.example.com", "warn"],
    ]);
  });

  it("groups everything about one domain under it, with the worst severity", () => {
    const items = collectAttention({
      apps: [app("example.com", "running")],
      deployments: [deploy(3, "example.com", "rolled_back")],
      certificates: [{ domain: "example.com", domains: [], days_remaining: 12, expires_on: "2026-10-07", auto_renew: true }],
    });
    expect(items).toHaveLength(1);
    expect(items[0]?.severity).toBe("warn");
    expect(items[0]?.reasons.map((reason) => reason.summary)).toEqual([
      { key: "deployRolledBack" },
      { key: "certExpiresIn", days: 12 },
    ]);
  });

  it("warns 21 days before a certificate expires and fails once it has", () => {
    const items = collectAttention({
      apps: [],
      certificates: [
        { domain: "ok.example.com", domains: [], days_remaining: 21, auto_renew: true },
        { domain: "soon.example.com", domains: [], days_remaining: 1, auto_renew: true },
        { domain: "late.example.com", domains: [], days_remaining: -3, expires_on: "2026-09-22", auto_renew: true },
      ],
    });
    expect(items.map((item) => [item.title, item.severity, item.reasons[0]?.summary])).toEqual([
      ["late.example.com", "fail", { key: "certExpired" }],
      ["soon.example.com", "warn", { key: "certExpiresIn", days: 1 }],
    ]);
    expect(items[1]?.subject).toEqual({ kind: "certificate", domain: "soon.example.com" });
  });

  it("reports failed units the apps' own state does not already name", () => {
    const machine = { ...MACHINE, units: { running: 3, failed: 2, stopped: 0 } };
    const unnamed = collectAttention({ apps: [app("x.example.com", "stopped")], machine });
    expect(unnamed.find((item) => item.subject.kind === "units")?.reasons[0]?.summary).toEqual({
      key: "unitsFailedCount",
      count: 2,
    });
    const named = collectAttention({ apps: [app("x.example.com", "failed"), app("y.example.com", "failed")], machine });
    expect(named.some((item) => item.subject.kind === "units")).toBe(false);
  });

  it("names each failed or crash-looping WASM unit that belongs to no app, and links it", () => {
    const unit = (name: string, active_state: string, sub_state: string, result = "success") => ({
      name,
      status: "stopped",
      active: false,
      enabled: true,
      managed: true,
      active_state,
      sub_state,
      result,
    });
    const items = collectAttention({
      apps: [{ ...app("shop.example.com", "failed"), unit: "shop-example-com" }, app("old.example.com", "running")],
      machine: { ...MACHINE, units: { running: 1, failed: 3, stopped: 0 } },
      units: [
        // The apps' own units: their state already says it, under the app's name.
        unit("shop-example-com", "failed", "failed", "exit-code"),
        unit("wasm-old-example-com", "failed", "failed", "exit-code"),
        unit("queue-worker", "failed", "failed", "exit-code"),
        unit("mailer", "activating", "auto-restart"),
        unit("cron-cleanup", "inactive", "dead"),
        { ...unit("sshd", "failed", "failed"), managed: false },
      ],
    });
    const units = items.filter((item) => item.subject.kind === "unit");
    expect(units.map((item) => [item.title, item.severity, item.reasons[0]?.summary, item.reasons[0]?.detail])).toEqual([
      ["queue-worker", "fail", { key: "unitFailed" }, "Result=exit-code"],
      ["mailer", "warn", { key: "unitRestarting" }, undefined],
    ]);
    expect(units[0]?.subject).toEqual({ kind: "unit", name: "queue-worker" });
    // Named, so the machine's bare count is not repeated.
    expect(items.some((item) => item.subject.kind === "units")).toBe(false);
  });

  it("lists open monitor findings, not acknowledged ones", () => {
    const base = { pid: 4242, process_name: "xmrig", signal: "sustained high CPU", observed_at: "2026-09-25T18:00:00" };
    const items = collectAttention({
      observations: [
        { ...base, id: 1, severity: "warning", acknowledged: false },
        { ...base, id: 2, severity: "notice", acknowledged: true },
      ],
    });
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      title: "xmrig",
      severity: "warn",
      reasons: [{ summary: { key: "monitorFinding", severity: "warning", signal: "sustained high CPU" } }],
    });
  });
});
