import { describe, expect, it } from "vitest";

import { lookResultOf } from "./lookResult";

const node = (name: string, item: Record<string, unknown> | null, more: Record<string, unknown> = {}) => ({
  node: name,
  state: "succeeded",
  items: item === null ? [] : [item],
  ...more,
});

describe("what each server found when the fleet looked for new versions", () => {
  it("says, for each server that looked, how many images it looked at and how many have a new version, with which", () => {
    const result = {
      nodes: [
        node("web-02", { state: "succeeded", looked: 12, newer: 2, unknown: 1, skipped: 0, checked_at: "2026-11-16T10:00:00Z", newer_references: ["nginx:1.27", "postgres:16"] }),
        node("web-03", { state: "succeeded", looked: 4, newer: 0, unknown: 0, skipped: 0, checked_at: "2026-11-16T10:00:01Z", newer_references: [] }),
      ],
    };
    expect(lookResultOf(result)).toEqual([
      { server: "web-02", looked: 12, newer: 2, references: ["nginx:1.27", "postgres:16"] },
      { server: "web-03", looked: 4, newer: 0, references: [] },
    ]);
  });

  it("leaves out a server that did not look: one that has no Docker or runs a Noust before 4.3 is not a failure and has nothing to say", () => {
    const result = { nodes: [node("web-02", { state: "succeeded", looked: 3, newer: 1, newer_references: ["redis:7"] }), node("old-01", null, { state: "skipped", reason: "unsupported" }), node("nas", null, { state: "skipped", reason: "not_needed" })] };
    expect(lookResultOf(result).map((row) => row.server)).toEqual(["web-02"]);
  });

  it("reads nothing out of a result that is not the shape of one, instead of failing a page", () => {
    expect(lookResultOf(undefined)).toEqual([]);
    expect(lookResultOf(null)).toEqual([]);
    expect(lookResultOf({})).toEqual([]);
    expect(lookResultOf({ nodes: "none" })).toEqual([]);
    expect(lookResultOf({ nodes: [{ node: "web-02", items: [{ looked: "many" }] }] })).toEqual([]);
  });
});
