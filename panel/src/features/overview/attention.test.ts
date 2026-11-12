import { describe, expect, it } from "vitest";

import type { OverviewAttentionItem } from "../../api/queries/overview";
import type { ApprovalList } from "../approvals/api";
import { approvalItems, attentionCounters, mergeAttention, noustItems, recipeItems } from "./attention";
import { attentionAction, attentionTarget } from "./attentionTarget";

const SINCE = 1_790_000_000;

function item(id: string, kind: string, severity: "fail" | "warn", title: string, reasons: object[] = []): OverviewAttentionItem {
  return { id, subject: { kind, domain: title, name: title }, title, severity, reasons: reasons as OverviewAttentionItem["reasons"] };
}

describe("an alert in the server's list", () => {
  // The server says which alerts fire (`managers/overview.py`); the console only offers what to do.
  const firing = (code: string, actions: string[], extra: object = {}) =>
    item("alert:disk-high:", "alert", "warn", "disk-high", [{ kind: "alert", severity: "warn", code, params: { rule: "disk-high", subject: "", since: SINCE, ...extra }, actions }]);

  it("offers to take an alert that fires", () => {
    expect(attentionAction({ ...firing("alert_firing", ["acknowledge_alert"]), subject: { kind: "alert", rule: "disk-high", subject: "" } })).toMatchObject({ mode: "alert", kind: "acknowledge", rule: "disk-high" });
  });

  it("offers to hand back one somebody took, because it is still firing", () => {
    const taken = { ...firing("alert_taken", ["reopen_alert"], { by: "alice", at: SINCE + 60 }), subject: { kind: "alert", rule: "disk-high", subject: "" } };
    expect(attentionAction(taken)).toMatchObject({ mode: "alert", kind: "reopen", rule: "disk-high" });
  });
});

describe("approvalItems", () => {
  const list = {
    approvals: [
      { id: 7, state: "requested", can_decide: true, requester: { name: "bob" }, created_at: SINCE },
      { id: 8, state: "requested", can_decide: false, requester: { name: "alice" }, created_at: SINCE },
      { id: 9, state: "approved", can_decide: true, requester: { name: "bob" }, created_at: SINCE },
    ],
    pending: 2,
  } as unknown as ApprovalList;

  it("lists only the requests waiting for a decision this person may give", () => {
    expect(approvalItems(list).map((entry) => entry.id)).toEqual(["approval:7"]);
    expect(approvalItems(list)[0]?.reasons[0]).toMatchObject({ code: "approval_waiting", params: { by: "bob" } });
  });
});

describe("noustItems and recipeItems", () => {
  it("says a newer Noust is there, and when it fixes a security problem", () => {
    expect(noustItems({ has_update: false, current_version: "4.0.0" })).toEqual([]);
    expect(noustItems({ has_update: true, latest_version: "4.0.1", current_version: "4.0.0" })[0]?.reasons[0]).toMatchObject({ code: "noust_update", params: { version: "4.0.1" } });
    expect(noustItems({ has_update: true, latest_version: "4.0.1", security_update: true, current_version: "4.0.0" })[0]?.reasons[0]?.code).toBe("noust_security_update");
  });

  it("lists the applications whose recipe has a newer version", () => {
    const installed = {
      items: [
        { domain: "blog.example.com", recipe: "ghost", update_available: true, installed_version: "5.1", latest_version: "5.2" },
        { domain: "n8n.example.com", recipe: "n8n", update_available: false, installed_version: "1", latest_version: "1" },
      ],
    };
    expect(recipeItems(installed).map((entry) => entry.id)).toEqual(["recipe:blog.example.com"]);
  });
});

describe("mergeAttention", () => {
  it("puts the worst first and then orders by name, whoever said it", () => {
    const merged = mergeAttention(
      [item("app:b", "app", "warn", "b.example.com"), item("app:a", "app", "fail", "a.example.com")],
      [item("alert:x", "alert", "fail", "x-rule"), item("approval:1", "approval", "warn", "#1")],
    );
    expect(merged.map((entry) => entry.id)).toEqual(["app:a", "alert:x", "approval:1", "app:b"]);
  });

  it("keeps an item both lists name once, with every reason and the worse severity", () => {
    const state = { kind: "state", severity: "warn", code: "service_restarting" };
    const cert = { kind: "certificate", severity: "fail", code: "certificate_expired" };
    const merged = mergeAttention([item("app:shop", "app", "warn", "shop", [state])], [item("app:shop", "app", "fail", "shop", [cert])]);
    expect(merged).toHaveLength(1);
    expect(merged[0]).toMatchObject({ severity: "fail" });
    expect(merged[0]?.reasons).toHaveLength(2);
  });
});

describe("attentionCounters", () => {
  it("counts what the sidebar shows beside Applications, Domains and Server from the one list", () => {
    const counters = attentionCounters([
      item("app:a", "app", "fail", "a.example.com", [{ kind: "state", severity: "fail", code: "service_failed" }]),
      item("app:b", "app", "fail", "b.example.com", [{ kind: "deploy", severity: "fail", code: "deploy_failed" }]),
      item("app:c", "app", "warn", "c.example.com", [{ kind: "certificate", severity: "warn", code: "certificate_expires_in" }]),
      item("certificate:d", "certificate", "fail", "d.example.com", [{ kind: "certificate", severity: "fail", code: "certificate_expired" }]),
      item("unit:noust-web", "unit", "fail", "noust-web", [{ kind: "unit", severity: "fail", code: "unit_failed" }]),
      item("unit:noust-cron", "unit", "warn", "noust-cron", [{ kind: "unit", severity: "warn", code: "unit_restarting" }]),
      item("alert:x", "alert", "fail", "x"),
    ]);
    expect(counters).toEqual({ appsFailed: 2, certificates: 2, certificatesExpired: 1, unitsFailed: 1, alerts: 1 });
  });
});

describe("attentionTarget and attentionAction", () => {
  it("opens where each kind of item is dealt with, and offers the first thing to do there", () => {
    const failedDeploy = item("app:shop", "app", "fail", "shop.example.com", [{ kind: "deploy", severity: "fail", code: "deploy_failed", deployment_id: 12 }]);
    expect(attentionTarget(failedDeploy)).toEqual({ to: "/apps/$domain/deployments/$id", params: { domain: "shop.example.com", id: "12" } });
    expect(attentionAction(failedDeploy)).toMatchObject({ mode: "open", kind: "viewLog" });

    // A service that is down because its last deploy failed: the deploy's log is where it leads, not the diagnosis.
    const downAfterDeploy = item("app:shop", "app", "fail", "shop.example.com", [
      { kind: "state", severity: "fail", code: "service_failed" },
      { kind: "deploy", severity: "fail", code: "deploy_failed", deployment_id: 12 },
    ]);
    expect(attentionTarget(downAfterDeploy)).toEqual({ to: "/apps/$domain/deployments/$id", params: { domain: "shop.example.com", id: "12" } });
    expect(attentionAction(downAfterDeploy)).toMatchObject({ kind: "viewLog" });

    const down = item("app:shop", "app", "fail", "shop.example.com", [{ kind: "state", severity: "fail", code: "service_failed" }]);
    expect(attentionTarget(down)).toEqual({ to: "/apps/$domain/diagnose", params: { domain: "shop.example.com" } });
    expect(attentionAction(down)).toMatchObject({ kind: "diagnose" });

    // A first deploy that failed has no application, so no deployment to open: the page of its domain says what happened.
    const firstDeploy = item("app:shop", "app", "fail", "shop.example.com", [{ kind: "first_deploy", severity: "fail", code: "first_deploy_failed", deployment_id: null }]);
    expect(attentionTarget(firstDeploy)).toEqual({ to: "/apps/$domain", params: { domain: "shop.example.com" } });
    expect(attentionAction(firstDeploy)).toMatchObject({ mode: "open", kind: "viewFailure", name: "shop.example.com" });

    const cert = item("certificate:d", "certificate", "warn", "d.example.com", [{ kind: "certificate", severity: "warn", code: "certificate_expires_in" }]);
    expect(attentionTarget(cert)).toEqual({ to: "/domains", search: { q: "d.example.com" } });

    expect(attentionTarget(item("unit:noust-web", "unit", "fail", "noust-web"))).toEqual({ to: "/server/services/$name", params: { name: "noust-web" } });
    expect(attentionTarget(item("probe:blog", "probe", "fail", "blog"))?.to).toBe("/alerts/probes");
    expect(attentionTarget(item("approval:7", "approval", "warn", "#7"))?.to).toBe("/settings/approvals");
    expect(attentionTarget(item("noust:update", "noust", "warn", "Noust"))?.to).toBe("/settings/about");
    expect(attentionTarget(item("recipe:x", "recipe", "warn", "x"))?.to).toBe("/catalog/installed");
  });

  it("offers nothing for an item it does not know", () => {
    const strange = item("x:1", "mystery", "warn", "x", [{ kind: "x", severity: "warn", code: "x" }]);
    expect(attentionTarget(strange)).toBeNull();
    expect(attentionAction(strange)).toBeNull();
  });
});
