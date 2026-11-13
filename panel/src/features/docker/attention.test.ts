import { describe, expect, it } from "vitest";

import { diskFacts, loopWhy, stoppedWhy, versionFacts } from "./attention";

describe("what a container that keeps falling says of itself", () => {
  it("says memory ran out, with the limit and how often, when the engine says so", () => {
    expect(loopWhy({ exit_code: 137, oom: true, memory_limit_bytes: 268435456, restarts_recent: 6, window_minutes: 10 })).toEqual({
      kind: "oom",
      code: 137,
      limitBytes: 268435456,
      restarts: 6,
      minutes: 10,
    });
  });

  it("says it was killed when it ended with 137 and memory was not the reason", () => {
    expect(loopWhy({ exit_code: 137, oom: false, restarts_recent: 4 })).toEqual({ kind: "killed", restarts: 4, minutes: 10 });
  });

  it("says it failed with its own code for anything else, and does not invent a code the server did not give", () => {
    expect(loopWhy({ exit_code: 1, restarts_recent: 3, window_minutes: 10 })).toEqual({ kind: "error", code: 1, restarts: 3, minutes: 10 });
    expect(loopWhy({})).toEqual({ kind: "error", code: null, restarts: 0, minutes: 10 });
  });
});

describe("what a container that stopped says of itself", () => {
  it("reads how it ended, its restart policy and when", () => {
    expect(stoppedWhy({ kind: "terminated", exit_code: 143, policy: "no", finished_at: "2026-11-09T10:00:00" })).toEqual({
      kind: "terminated",
      code: 143,
      policy: "no",
      finishedAt: "2026-11-09T10:00:00",
    });
  });

  it("takes no policy as no restart policy, which is the one that does not come back by itself", () => {
    expect(stoppedWhy({})).toEqual({ kind: "stopped", code: null, policy: "no", finishedAt: null });
  });
});

describe("what the registries and the disk said", () => {
  it("reads the images with a new version and when they were looked at", () => {
    expect(versionFacts({ images: ["postgres:latest", "redis:7"], checked_at: "2026-11-12T08:00:00" })).toEqual({ images: ["postgres:latest", "redis:7"], checkedAt: "2026-11-12T08:00:00" });
    expect(versionFacts({})).toEqual({ images: [], checkedAt: null });
  });

  it("reads what is left of the disk and what can be taken back", () => {
    expect(diskFacts({ free_bytes: 30064771072, total_bytes: 171798691840, reclaimable_bytes: 1932735283 })).toEqual({ free: 30064771072, total: 171798691840, reclaimable: 1932735283 });
    expect(diskFacts({})).toEqual({ free: null, total: null, reclaimable: null });
  });
});
