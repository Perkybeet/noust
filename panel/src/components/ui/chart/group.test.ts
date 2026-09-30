import { describe, expect, it, vi } from "vitest";

import { createChartGroup } from "./group";

describe("chart group", () => {
  it("shares one moment, says which chart put it there, and forgets it", () => {
    const group = createChartGroup();
    const listener = vi.fn();
    group.subscribe(listener);
    group.setCursor(1_000, "cpu");
    expect(group.cursor()).toEqual({ at: 1_000, source: "cpu" });
    expect(listener).toHaveBeenCalledTimes(1);
    group.setCursor(1_000, "cpu");
    expect(listener).toHaveBeenCalledTimes(1);
    group.setCursor(null, "cpu");
    expect(group.cursor()).toEqual({ at: null, source: null });
  });

  it("draws every value axis as wide as the widest, and relays out the others when it grows", async () => {
    const group = createChartGroup();
    const relayout = vi.fn();
    group.onAxisChange(relayout);
    expect(group.reportAxis("cpu", 40)).toBe(40);
    expect(group.reportAxis("network", 64)).toBe(64);
    expect(group.reportAxis("cpu", 40)).toBe(64);
    await Promise.resolve();
    expect(relayout).toHaveBeenCalledTimes(2);
    group.leave("network");
    await Promise.resolve();
    expect(group.reportAxis("cpu", 40)).toBe(40);
  });
});
