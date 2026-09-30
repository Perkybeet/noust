import { describe, expect, it } from "vitest";

import { countdown } from "./PendingChanges";

describe("countdown", () => {
  it("counts minutes and seconds down to the change's end, never below zero", () => {
    const now = 1_000_000_000_000;
    expect(countdown(now / 1000 + 102, now)).toBe("1:42");
    expect(countdown(now / 1000 + 9, now)).toBe("0:09");
    expect(countdown(now / 1000 - 5, now)).toBe("0:00");
  });
});
