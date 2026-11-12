import { afterEach, describe, expect, it } from "vitest";

import { FRESH_MS, forgetLaunches, launchedFrom, noteArrivals } from "./launches";

const NOW = Date.parse("2026-11-10T12:00:00Z");
const fresh = (key: string) => ({ key, createdAt: NOW - 2_000, state: "running" as const });

afterEach(() => {
  forgetLaunches();
});

describe("where a task was launched from", () => {
  it("is the page the person was on when the console first heard of the task, and the first page only", () => {
    noteArrivals([fresh("|job:a")], "/databases/postgresql/shop/backups", NOW);
    noteArrivals([fresh("|job:a")], "/apps", NOW);
    expect(launchedFrom("|job:a")).toBe("/databases/postgresql/shop/backups");
    expect(launchedFrom("|job:never-heard-of")).toBeNull();
  });

  it("is noted for a task that arrives with the rest of a read, but not for the ones the console already held", () => {
    noteArrivals([fresh("|job:a"), fresh("|job:b")], "/network/firewall", NOW);
    noteArrivals([fresh("|job:a"), fresh("|job:b"), fresh("|job:c")], "/security", NOW);
    expect(launchedFrom("|job:b")).toBe("/network/firewall");
    expect(launchedFrom("|job:c")).toBe("/security");
  });

  it("is not noted for a task the console meets old: it was launched earlier, from a page nobody knows", () => {
    noteArrivals([{ key: "|job:old", createdAt: NOW - FRESH_MS - 1, state: "running" }], "/apps", NOW);
    noteArrivals([{ key: "|job:unknown", createdAt: null, state: "running" }], "/apps", NOW);
    expect(launchedFrom("|job:old")).toBeNull();
    expect(launchedFrom("|job:unknown")).toBeNull();
  });

  it("is not noted for a task that has already ended when it is first met", () => {
    noteArrivals([{ key: "|job:done", createdAt: NOW - 1_000, state: "succeeded" }], "/apps", NOW);
    expect(launchedFrom("|job:done")).toBeNull();
  });

  it("keeps a bounded number of them: a long session does not grow without end, and the oldest go first", () => {
    for (let index = 0; index < 400; index += 1) noteArrivals([fresh(`|job:${String(index)}`)], "/apps", NOW);
    expect(launchedFrom("|job:399")).toBe("/apps");
    expect(launchedFrom("|job:0")).toBeNull();
  });
});
