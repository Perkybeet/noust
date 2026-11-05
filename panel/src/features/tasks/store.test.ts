import { describe, expect, it, vi } from "vitest";

import { createTaskStore, EMPTY, KEEP, pin, readPinned, unpin, withActiveJobs, withFleetItems, withJob, withJobs, withOperationEvent, withOperations } from "./store";
import { taskKey } from "./model";
import { job, operation } from "./taskFixtures";

const T0 = 1_000_000;

describe("jobs", () => {
  it("are held by the event that reports them, newest word wins", () => {
    let state = withJob(EMPTY, job({ progress: 10 }), null, T0);
    state = withJob(state, job({ progress: 60, current_step: "Installing" }), null, T0 + 1);
    const task = state.tasks.get(taskKey(null, "job", "ab12cd34"));
    expect(task?.progress).toBe(60);
    expect(task?.step).toBe("Installing");
    expect(state.tasks.size).toBe(1);
  });

  it("never go back to running once they have ended, whichever writer is late", () => {
    let state = withJob(EMPTY, job({ status: "completed", state: "succeeded" }), null, T0);
    state = withJob(state, job({ status: "running", state: "running" }), null, T0 + 1);
    expect(state.tasks.get(taskKey(null, "job", "ab12cd34"))?.state).toBe("succeeded");
  });

  it("keep what a read said of whether they can be stopped when an event does not", () => {
    let state = withActiveJobs(EMPTY, [job({ cancellable: true })], null, T0, T0 - 5);
    state = withJob(state, job({ progress: 70 }), null, T0 + 10);
    expect(state.tasks.get(taskKey(null, "job", "ab12cd34"))?.cancellable).toBe(true);
  });

  it("from a read do not replace what an event said after the read began", () => {
    let state = withJob(EMPTY, job({ progress: 80 }), null, T0 + 100);
    state = withJobs(state, [job({ progress: 20 })], null, T0 + 200, T0 + 50);
    expect(state.tasks.get(taskKey(null, "job", "ab12cd34"))?.progress).toBe(80);
  });
});

describe("what runs now, as a whole list", () => {
  it("lets go of a job it held as running that the list no longer has, when the console had not heard of it since the read began", () => {
    let state = withJob(EMPTY, job({ id: "gone" }), null, T0);
    state = withActiveJobs(state, [job({ id: "here" })], null, T0 + 500, T0 + 100);
    expect([...state.tasks.keys()]).toEqual([taskKey(null, "job", "here")]);
  });

  it("keeps a job whose event came after the read began, which the read could not know of", () => {
    let state = withJob(EMPTY, job({ id: "new" }), null, T0 + 200);
    state = withActiveJobs(state, [], null, T0 + 500, T0 + 100);
    expect(state.tasks.has(taskKey(null, "job", "new"))).toBe(true);
  });

  it("never lets go of a job that ended, nor of another server's", () => {
    let state = withJob(EMPTY, job({ id: "done", status: "completed", state: "succeeded" }), null, T0);
    state = withJob(state, job({ id: "elsewhere" }), "web-02", T0);
    state = withActiveJobs(state, [], null, T0 + 500, T0 + 100);
    expect(state.tasks.has(taskKey(null, "job", "done"))).toBe(true);
    expect(state.tasks.has(taskKey("web-02", "job", "elsewhere"))).toBe(true);
  });
});

describe("operations", () => {
  const start = { id: "op-1", phase: "start", title: { code: "operations.app_restart", params: { target: "api.example.com" } }, target: "api.example.com", domain: "api.example.com", actor: "ben", at: "2026-11-04T10:00:00+00:00" } as const;

  it("run from their start event and end with their result, kept for what ended lately", () => {
    let state = withOperationEvent(EMPTY, start, null, T0);
    expect(state.tasks.get(taskKey(null, "operation", "op-1"))?.state).toBe("running");
    state = withOperationEvent(state, { ...start, phase: "end", at: "2026-11-04T10:00:03+00:00", result: "failed" }, null, T0 + 5);
    const ended = state.tasks.get(taskKey(null, "operation", "op-1"));
    expect(ended?.state).toBe("failed");
    expect(ended?.completedAt).toBe(Date.parse("2026-11-04T10:00:03+00:00"));
  });

  it("are held when only their end was heard (the page was opened while they ran)", () => {
    const state = withOperationEvent(EMPTY, { ...start, phase: "end", result: "ok" }, null, T0);
    expect(state.tasks.get(taskKey(null, "operation", "op-1"))?.state).toBe("succeeded");
  });

  it("are replaced by the list of what runs, which drops one whose end was missed", () => {
    let state = withOperationEvent(EMPTY, start, null, T0);
    state = withOperations(state, [operation({ id: "op-2" })], null, T0 + 500, T0 + 100);
    expect([...state.tasks.keys()]).toEqual([taskKey(null, "operation", "op-2")]);
  });

  it("starting twice is one", () => {
    let state = withOperationEvent(EMPTY, start, null, T0);
    state = withOperationEvent(state, start, null, T0 + 1);
    expect(state.tasks.size).toBe(1);
  });
});

describe("a central's fleet", () => {
  const rows = [
    { node: "central-01", local: true, status: "ok", jobs: [job({ id: "c1" })], operations: [] },
    { node: "web-02", local: false, status: "unsupported", jobs: [], operations: [] },
    { node: "web-03", local: false, status: "ok", jobs: [job({ id: "w3" })], operations: [operation({ id: "op-w3" })] },
    { node: "db-02", local: false, status: "unreachable", jobs: [], operations: [] },
  ];

  it("takes each server's tasks under its own name, and the central's under none", () => {
    const state = withFleetItems(EMPTY, rows, [], T0, T0 - 10);
    expect([...state.tasks.keys()].sort()).toEqual([taskKey(null, "job", "c1"), taskKey("web-03", "job", "w3"), taskKey("web-03", "operation", "op-w3")].sort());
  });

  it("remembers how each server answered", () => {
    const state = withFleetItems(EMPTY, rows, [{ name: "web-02", version: "4.1.0" }], T0, T0 - 10);
    expect([...state.servers.entries()].map(([id, status]) => [id, status.name, status.answer, status.version])).toEqual([
      ["", "central-01", "ok", null],
      ["web-02", "web-02", "unsupported", "4.1.0"],
      ["web-03", "web-03", "ok", null],
      ["db-02", "db-02", "unreachable", null],
    ]);
  });

  it("does not say a server that stopped answering is still running something", () => {
    let state = withFleetItems(EMPTY, rows, [], T0, T0 - 10);
    state = withJob(state, job({ id: "db1" }), "db-02", T0);
    state = withFleetItems(state, rows, [], T0 + 500, T0 + 100);
    expect(state.tasks.has(taskKey("db-02", "job", "db1"))).toBe(false);
  });
});

describe("what is kept", () => {
  it("lets the oldest that ended go past the limit and never what runs", () => {
    let state = withJob(EMPTY, job({ id: "running" }), null, 0);
    for (let index = 0; index < KEEP + 20; index += 1) {
      state = withJob(state, job({ id: `done-${String(index)}`, status: "completed", state: "succeeded" }), null, index + 1);
    }
    expect(state.tasks.size).toBe(KEEP);
    expect(state.tasks.has(taskKey(null, "job", "running"))).toBe(true);
    expect(state.tasks.has(taskKey(null, "job", "done-0"))).toBe(false);
    expect(state.tasks.has(taskKey(null, "job", `done-${String(KEEP + 19)}`))).toBe(true);
  });
});

describe("the cell", () => {
  it("tells its readers when the state changed, and nobody when it did not", () => {
    const store = createTaskStore();
    const listener = vi.fn();
    const stop = store.subscribe(listener);
    store.update((state) => withJob(state, job(), null, T0));
    expect(listener).toHaveBeenCalledTimes(1);
    store.update((state) => state);
    expect(listener).toHaveBeenCalledTimes(1);
    stop();
    store.update((state) => withJob(state, job({ progress: 90 }), null, T0 + 1));
    expect(listener).toHaveBeenCalledTimes(1);
  });
});

describe("pins", () => {
  it("are a choice: pinning adds one, letting go removes it, and doing either twice changes nothing", () => {
    const store = createTaskStore();
    pin("a", store);
    pin("a", store);
    pin("b", store);
    expect(store.get().pinned).toEqual(["a", "b"]);
    unpin("a", store);
    unpin("a", store);
    expect(store.get().pinned).toEqual(["b"]);
  });

  it("are read back from the browser's storage, and a value that is not a list is no pins", () => {
    const stored = (value: string | null) => ({ getItem: () => value });
    expect(readPinned(stored('["a","b"]'))).toEqual(["a", "b"]);
    expect(readPinned(stored('["a",3,null]'))).toEqual(["a"]);
    expect(readPinned(stored('{"a":1}'))).toEqual([]);
    expect(readPinned(stored("not json"))).toEqual([]);
    expect(readPinned(stored(null))).toEqual([]);
  });
});
