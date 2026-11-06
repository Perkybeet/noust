import { describe, expect, it } from "vitest";

import { answers, lifeOf } from "./engineOf";

describe("what an engine's life is, as the list of engines says it", () => {
  it("is running when it answers, stopped when it was stopped, changing while it is being made, moved or removed", () => {
    expect(lifeOf({ running: true, state: "running" })).toBe("running");
    expect(lifeOf({ running: false, state: "stopped" })).toBe("stopped");
    for (const state of ["creating", "upgrading", "removing", "exited"]) expect(lifeOf({ running: false, state })).toBe("changing");
  });

  it("is unknown for an engine the list does not hold", () => {
    expect(lifeOf(undefined)).toBe("unknown");
  });
});

describe("whether it is worth asking an engine anything", () => {
  it("is, once the list is read and the engine runs or is not known", () => {
    expect(answers({ life: "running", reading: false })).toBe(true);
    // Not known is a reason to ask: the list may be of a node that has none, and the engine's own answer is the one there is.
    expect(answers({ life: "unknown", reading: false })).toBe(true);
  });

  it("is not while the list is being read, nor for an engine that is stopped or changing: it would answer with an error", () => {
    expect(answers({ life: "running", reading: true })).toBe(false);
    expect(answers({ life: "stopped", reading: false })).toBe(false);
    expect(answers({ life: "changing", reading: false })).toBe(false);
  });
});
