import { describe, expect, it } from "vitest";

import { LONG_TASK_MS } from "./browserNotice";
import { endedTask, endingOf, isLeavingLongTask, noticeStateOf } from "./endings";
import { taskOfJob } from "./model";
import type { Task } from "./model";
import { job } from "./taskFixtures";

const NOW = Date.parse("2026-11-04T12:00:00Z");
const me = { username: "ana", session: null };
const task = (patch: Partial<Task> = {}, jobPatch = {}): Task => ({ ...taskOfJob(job({ actor: "ana", ...jobPatch }), null, NOW), ...patch });

describe("the end of a task", () => {
  it("is the change from running or waiting to ended, and nothing else", () => {
    expect(endedTask(task({ state: "running" }), task({ state: "succeeded" }))?.state).toBe("succeeded");
    expect(endedTask(task({ state: "queued" }), task({ state: "cancelled" }))?.state).toBe("cancelled");
    expect(endedTask(task({ state: "running" }), task({ state: "running" }))).toBeNull();
    expect(endedTask(task({ state: "succeeded" }), task({ state: "succeeded" }))).toBeNull();
    // Heard of only as ended: the console was not there to see it run.
    expect(endedTask(undefined, task({ state: "failed" }))).toBeNull();
  });
});

describe("telling the person", () => {
  it("is a toast for a job of theirs that ends on another page", () => {
    expect(endingOf(task({ state: "succeeded" }), me, "/databases")).toBe("toast");
    expect(endingOf(task({ state: "failed" }), me, "/")).toBe("toast");
  });

  it("is silent on the task's own page and on the page of the application it acts on", () => {
    expect(endingOf(task({ state: "succeeded", id: "ab12" }), me, "/activity/tasks/ab12")).toBe("silent");
    expect(endingOf(task({ state: "succeeded" }), me, "/apps/shop.example.com/deployments")).toBe("silent");
  });

  it("is not the task centre's for what is not the person's, nor for an operation", () => {
    expect(endingOf(task({ state: "succeeded", actor: "ben" }), me, "/databases")).toBe("none");
    expect(endingOf(task({ state: "succeeded", actor: null }), me, "/databases")).toBe("none");
    expect(endingOf(task({ state: "succeeded", kind: "operation" }), me, "/databases")).toBe("none");
  });

  it("names the server's word for how it ended, so its own notice can be told from another's", () => {
    expect(noticeStateOf({ state: "succeeded" })).toBe("active");
    expect(noticeStateOf({ state: "failed" })).toBe("failed");
    expect(noticeStateOf({ state: "cancelled" })).toBe("idle");
  });
});

describe("leaving a long task", () => {
  const long = { startedAt: NOW - LONG_TASK_MS - 1 };
  it("is moving to another page while a task of theirs has run for a while", () => {
    expect(isLeavingLongTask([task(long)], me, "/databases", NOW)).toBe(true);
  });

  it("is not while it is still short, nor on its own page, nor for another person's, nor for one that waits", () => {
    expect(isLeavingLongTask([task({ startedAt: NOW - 1000 })], me, "/databases", NOW)).toBe(false);
    expect(isLeavingLongTask([task(long)], me, "/apps/shop.example.com", NOW)).toBe(false);
    expect(isLeavingLongTask([task({ ...long, actor: "ben" })], me, "/databases", NOW)).toBe(false);
    expect(isLeavingLongTask([task({ ...long, state: "queued" })], me, "/databases", NOW)).toBe(false);
    expect(isLeavingLongTask([], me, "/databases", NOW)).toBe(false);
  });
});
