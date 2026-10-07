import { describe, expect, it } from "vitest";

import { cancelReasonWords, cancelState, entryDomain, entryTitle, isWaiting, mergedRequests, placeOf } from "./words";

const base = { type: "update", status: "running", cancel_requested: false, metadata: { domain: "shop.example.com" } } as const;

describe("what the queue says about a job", () => {
  it("names a job by what it does to which application, and by the server's name when it acts on none", () => {
    expect(entryTitle({ type: "update", name: "Update shop.example.com", metadata: { domain: "shop.example.com" } })).toBe("Updating shop.example.com");
    expect(entryTitle({ type: "self_update", name: "Update Noust", metadata: {} })).toBe("Update Noust");
    expect(entryDomain({ metadata: { domain: "" } })).toBeNull();
    expect(entryDomain({ metadata: { domain: 7 } })).toBeNull();
  });

  it("counts the requests merged into a waiting job, and none for what is not a count", () => {
    expect(mergedRequests({ metadata: { merged_requests: 3 } })).toBe(3);
    expect(mergedRequests({ metadata: {} })).toBe(0);
    expect(mergedRequests({ metadata: { merged_requests: "2" } })).toBe(0);
    expect(mergedRequests({ metadata: { merged_requests: -1 } })).toBe(0);
  });

  it("places a waiting job, and only a waiting one", () => {
    expect(isWaiting({ status: "pending" })).toBe(true);
    expect(placeOf({ status: "pending", queue_position: 1 })).toEqual({ next: true, position: 1 });
    expect(placeOf({ status: "pending", queue_position: 4 })).toEqual({ next: false, position: 4 });
    expect(placeOf({ status: "pending", queue_position: null })).toBeNull();
    expect(placeOf({ status: "running", queue_position: null })).toBeNull();
  });
});

describe("what Cancel does", () => {
  it("takes a waiting job out of the line whatever it is", () => {
    expect(cancelState({ ...base, type: "backup", status: "pending", cancellable: true }, undefined)).toEqual({ kind: "can", stage: "queued" });
    // Its own event does not say whether it can be stopped, and a job that waits always can.
    expect(cancelState({ ...base, status: "pending" }, undefined)).toEqual({ kind: "can", stage: "queued" });
  });

  it("stops a build the server says can still be stopped", () => {
    expect(cancelState({ ...base, cancellable: true }, undefined)).toEqual({ kind: "can", stage: "running" });
  });

  it("says a stop was asked and is winding down, before anything else", () => {
    expect(cancelState({ ...base, cancellable: false, cancel_requested: true }, undefined)).toEqual({ kind: "stopping" });
  });

  it("does not guess for a running job whose event carries no answer", () => {
    expect(cancelState({ ...base }, undefined)).toEqual({ kind: "unknown" });
  });

  it("says why a build that cannot be stopped cannot, by what it builds", () => {
    const apps = [
      { domain: "shop.example.com", layout: "releases", app_type: "nextjs" },
      { domain: "old.example.com", layout: "inplace", app_type: "nextjs" },
      { domain: "stack.example.com", layout: "releases", app_type: "docker-compose" },
    ] as never;
    expect(cancelState({ ...base, cancellable: false }, apps)).toEqual({ kind: "cannot", reason: "activating" });
    expect(cancelState({ ...base, cancellable: false, metadata: { domain: "old.example.com" } }, apps)).toEqual({ kind: "cannot", reason: "buildsInPlace" });
    expect(cancelState({ ...base, cancellable: false, metadata: { domain: "stack.example.com" } }, apps)).toEqual({ kind: "cannot", reason: "buildsInPlace" });
    // Before the applications are read, a release build is assumed: the likelier reason.
    expect(cancelState({ ...base, cancellable: false }, undefined)).toEqual({ kind: "cannot", reason: "activating" });
  });

  it("says a job that is not a build is halfway through changing the system", () => {
    expect(cancelState({ ...base, type: "restore", cancellable: false }, undefined)).toEqual({ kind: "cannot", reason: "unsupported" });
    expect(cancelReasonWords("unsupported")).toMatch(/halfway through changing the system/);
  });
});
