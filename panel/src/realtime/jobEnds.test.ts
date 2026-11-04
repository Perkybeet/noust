import { beforeEach, describe, expect, it } from "vitest";

import { HANDLED_FOR_MS, markJobEndHandled, resetHandled, takeJobEndHandled } from "./jobEnds";

beforeEach(resetHandled);

describe("the end of a job the task centre has said", () => {
  it("is taken by the notice it was left for, once", () => {
    markJobEndHandled("active", 1000);
    expect(takeJobEndHandled("active", 1100)).toBe(true);
    expect(takeJobEndHandled("active", 1200)).toBe(false);
  });

  it("is not taken by a notice about something else", () => {
    markJobEndHandled("active", 1000);
    expect(takeJobEndHandled("failed", 1100)).toBe(false);
    expect(takeJobEndHandled("busy", 1100)).toBe(false);
    expect(takeJobEndHandled("active", 1100)).toBe(true);
  });

  it("is forgotten if its notice never comes", () => {
    markJobEndHandled("failed", 1000);
    expect(takeJobEndHandled("failed", 1000 + HANDLED_FOR_MS + 1)).toBe(false);
  });

  it("is nothing when nothing was marked", () => {
    expect(takeJobEndHandled("active")).toBe(false);
  });
});
