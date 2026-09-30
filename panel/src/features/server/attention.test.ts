import { describe, expect, it } from "vitest";

import { attentionItems, checkSeverity, checkView, verdictOf } from "./attention";
import { SECURITY, SUMMARY } from "./testing";

describe("attentionItems", () => {
  it("lists the summary's facts and the open checks, the most serious first", () => {
    const items = attentionItems(SUMMARY, SECURITY);
    expect(items.map((item) => item.kind)).toEqual(["securityUpdates", "check", "rebootRequired", "noSwap", "check"]);
    expect(items.map((item) => item.severity)).toEqual(["critical", "critical", "warning", "warning", "warning"]);
  });

  it("leaves a check to the summary fact that says the same with numbers", () => {
    const ids = attentionItems(SUMMARY, SECURITY).map((item) => item.id);
    expect(ids).not.toContain("check:upd.security_pending");
    expect(ids).toContain("security-updates");
    expect(ids).toContain("check:fw.docker_bypass");
  });

  it("says nothing about a server that is fine", () => {
    const fine = {
      ...SUMMARY,
      updates: { ...SUMMARY.updates, pending: 0, security: 0 },
      reboot: { required: false },
      swap: { total_bytes: 2_147_483_648, used_bytes: 0, recommended: false },
    };
    expect(attentionItems(fine, { ...SECURITY, attention: [] })).toEqual([]);
    expect(verdictOf([])).toEqual({ state: "running", label: "healthy" });
  });

  it("reads a disk, the end of support, the clock and failed units", () => {
    const worse = {
      ...SUMMARY,
      os: { ...SUMMARY.os, eol: { status: "expired", end_date: "2024-06-30", source: "table" } },
      disk: { ...SUMMARY.disk, worst_mount: "/var", worst_percent: 96, status: "critical" },
      time: { ...SUMMARY.time, synchronized: false },
      system: { state: "degraded", failed_units: ["mysql.service"] },
    };
    const kinds = attentionItems(worse, undefined).map((item) => item.kind);
    expect(kinds).toEqual(["securityUpdates", "osUnsupported", "diskFull", "rebootRequired", "clockUnsynced", "noSwap", "failedUnits"]);
    expect(verdictOf(attentionItems(worse, undefined)).label).toBe("critical");
  });

  it("judges only failing critical and warning checks worth a line", () => {
    expect(checkSeverity({ status: "fail", severity: "critical" })).toBe("critical");
    expect(checkSeverity({ status: "warn", severity: "warning" })).toBe("warning");
    expect(checkSeverity({ status: "warn", severity: "info" })).toBeNull();
    expect(checkSeverity({ status: "pass", severity: "critical" })).toBeNull();
    expect(checkView("fail2ban")).toBe("bans");
    expect(checkView("updates")).toBe("checks");
  });
});
