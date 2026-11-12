import { describe, expect, it } from "vitest";

import type { OverviewAttentionItem } from "../../api/queries/overview";
import { attentionCounters } from "./attention";
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
    expect(counters).toEqual({ appsFailed: 2, certificates: 2, certificatesExpired: 1, unitsFailed: 1, machineFailed: 0, findingsFailed: 0, alerts: 1 });
  });

  it("counts the machine's facts that failed beside Server and the findings that failed beside Security, from the same list", () => {
    const counters = attentionCounters([
      item("machine:disk", "machine", "fail", "Disk", [{ kind: "machine", severity: "fail", code: "disk_full" }]),
      item("machine:reboot_required", "machine", "warn", "Reboot", [{ kind: "machine", severity: "warn", code: "reboot_required" }]),
      item("security:fw.docker_bypass", "finding", "fail", "Docker publishes ports", [{ kind: "security", severity: "fail", code: "security_finding" }]),
      item("security:ssh.password_auth", "finding", "warn", "SSH accepts passwords", [{ kind: "security", severity: "warn", code: "security_finding" }]),
    ]);
    expect(counters).toMatchObject({ machineFailed: 1, findingsFailed: 1, unitsFailed: 0 });
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

  it("opens the page of the machine that deals with each of its facts, with the word of the thing to do there", () => {
    const fact = (name: string, code: string) => ({ ...item(`machine:${name}`, "machine", "warn", name, [{ kind: "machine", severity: "warn", code }]), subject: { kind: "machine", fact: name } });
    expect(attentionTarget(fact("security_updates", "security_updates"))?.to).toBe("/server/updates");
    expect(attentionAction(fact("security_updates", "security_updates"))).toMatchObject({ mode: "open", kind: "installUpdates" });
    expect(attentionAction(fact("packages_broken", "packages_broken"))).toMatchObject({ kind: "repairPackages", target: { to: "/server/updates" } });
    expect(attentionAction(fact("os_support", "os_ending"))).toMatchObject({ kind: "openUpdates", target: { to: "/server/updates" } });
    // The reboot is scheduled from the updates, where the packages that ask for it are listed.
    expect(attentionAction(fact("reboot_required", "reboot_required"))).toMatchObject({ kind: "scheduleReboot", target: { to: "/server/updates" } });
    expect(attentionAction(fact("disk", "disk_full"))).toMatchObject({ kind: "freeSpace", target: { to: "/server/files" } });
    expect(attentionAction(fact("clock", "clock_unsynced"))).toMatchObject({ kind: "fixClock", target: { to: "/server/machine" } });
    expect(attentionAction(fact("swap", "no_swap"))).toMatchObject({ kind: "addSwap", target: { to: "/server/files" } });
    expect(attentionAction(fact("failed_units", "failed_units"))).toMatchObject({ kind: "viewServices", target: { to: "/server/services", search: { state: "failed", all: true } } });
    expect(attentionTarget({ ...fact("mystery", "x"), subject: { kind: "machine", fact: "mystery" } })).toEqual({ to: "/server" });
  });

  it("opens the page where a finding of the checks is fixed, by its group", () => {
    const finding = (group: string) => ({ ...item(`security:x.${group}`, "finding", "fail", "A finding", [{ kind: "security", severity: "fail", code: "security_finding" }]), subject: { kind: "finding", check: `x.${group}`, group } });
    expect(attentionTarget(finding("ssh"))?.to).toBe("/security/ssh");
    expect(attentionTarget(finding("fail2ban"))?.to).toBe("/security/blocks");
    // The firewall is Network and ports' since 4.1.
    expect(attentionTarget(finding("firewall"))?.to).toBe("/network/firewall");
    expect(attentionTarget(finding("updates"))?.to).toBe("/security");
    expect(attentionAction(finding("ssh"))).toMatchObject({ mode: "open", kind: "reviewFinding", name: "A finding" });
  });

  it("offers nothing for an item it does not know", () => {
    const strange = item("x:1", "mystery", "warn", "x", [{ kind: "x", severity: "warn", code: "x" }]);
    expect(attentionTarget(strange)).toBeNull();
    expect(attentionAction(strange)).toBeNull();
  });
});
