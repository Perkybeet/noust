import { describe, expect, it } from "vitest";

import type { OverviewAttentionItem } from "../../api/queries/overview";
import { serverItems, verdictOf } from "./attention";

function row(id: string, kind: string, severity: "fail" | "warn"): OverviewAttentionItem {
  return { id, subject: { kind }, title: id, severity, origin: "state", reasons: [] };
}

describe("serverItems", () => {
  it("takes the machine's rows out of the one list: its facts, Noust's own services and the findings of Security", () => {
    const list = [
      row("app:shop", "app", "fail"),
      row("machine:disk", "machine", "fail"),
      row("unit:noust-web", "unit", "fail"),
      row("security:fw.docker_bypass", "finding", "fail"),
      row("alert:cpu", "alert", "warn"),
      row("approval:7", "approval", "warn"),
      row("machine:reboot_required", "machine", "warn"),
    ];
    expect(serverItems(list).map((item) => item.id)).toEqual(["machine:disk", "unit:noust-web", "security:fw.docker_bypass", "machine:reboot_required"]);
  });

  it("keeps the order the server gave: the worst first", () => {
    const list = [row("a", "finding", "fail"), row("b", "machine", "warn")];
    expect(serverItems(list).map((item) => item.id)).toEqual(["a", "b"]);
  });
});

describe("verdictOf", () => {
  it("is critical with a failure, a warning with anything else and healthy with nothing", () => {
    expect(verdictOf([row("a", "machine", "warn"), row("b", "finding", "fail")])).toEqual({ state: "failed", label: "critical" });
    expect(verdictOf([row("a", "machine", "warn")])).toEqual({ state: "warning", label: "attention" });
    expect(verdictOf([])).toEqual({ state: "running", label: "healthy" });
  });
});
