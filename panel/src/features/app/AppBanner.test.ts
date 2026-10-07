import { describe, expect, it } from "vitest";

import { appCondition } from "./AppBanner";

const RUNNING = { status: "running", port: 3000 };

describe("what is wrong with an app, from its newest deploy", () => {
  it("is a failed deploy while an older version serves", () => {
    const newest = { id: 7, status: "failed", finished_at: "2026-10-06T10:00:00Z", error: "Type error" };
    expect(appCondition(RUNNING, newest)).toEqual({ kind: "deployFailed", deploy: newest });
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
