import { describe, expect, it } from "vitest";

import { appCondition } from "./AppBanner";

const RUNNING = { status: "running", port: 3000, layout: "releases" };

describe("what is wrong with an app, from its newest deploy", () => {
  it("is a failed deploy while the application still answers", () => {
    const newest = { id: 7, status: "failed", finished_at: "2026-10-06T10:00:00Z", error: "Type error" };
    expect(appCondition(RUNNING, newest)).toEqual({ kind: "deployFailed", deploy: newest, serving: "answering", inPlace: false });
  });

  it("does not say it answers when it is stopped: the history says a deploy failed, and nothing else", () => {
    const newest = { id: 7, status: "failed", finished_at: "2026-10-06T10:00:00Z", error: "Type error" };
    expect(appCondition({ ...RUNNING, status: "stopped" }, newest)).toEqual({ kind: "deployFailed", deploy: newest, serving: "stopped", inPlace: false });
    expect(appCondition({ ...RUNNING, status: "inactive" }, newest)).toMatchObject({ serving: "stopped" });
  });

  it("says when the application is deployed in its own folder: a restart starts what the folder holds, which may be what failed", () => {
    const newest = { id: 7, status: "failed", finished_at: "2026-10-06T10:00:00Z" };
    expect(appCondition({ ...RUNNING, layout: "inplace" }, newest)).toMatchObject({ kind: "deployFailed", inPlace: true });
    // A layout the console was not told is treated as the cautious one: it claims nothing about the version.
    expect(appCondition({ status: "running", port: 3000 }, newest)).toMatchObject({ inPlace: true });
  });

  it("says the port that answers when nothing does, which in zero-downtime mode is not the recorded one", () => {
    expect(appCondition({ status: "no_answer", port: 3001, serving_port: 3002 }, null)).toEqual({ kind: "noAnswer", port: 3002 });
    expect(appCondition({ status: "no_answer", port: 3001 }, null)).toEqual({ kind: "noAnswer", port: 3001 });
  });

  it("is nothing when an operator cancelled the deploy: it is stored as failed, but nothing went wrong", () => {
    const newest = { id: 7, status: "failed", finished_at: "2026-10-06T10:00:00Z", cancelled: true };
    expect(appCondition(RUNNING, newest)).toBeNull();
  });

  it("still names a service that has failed after a deploy that was cancelled", () => {
    const newest = { id: 7, status: "failed", finished_at: "2026-10-06T10:00:00Z", cancelled: true };
    expect(appCondition({ status: "failed", port: 3000 }, newest)).toMatchObject({ kind: "failed" });
  });
});
