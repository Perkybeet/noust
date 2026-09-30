import { describe, expect, it } from "vitest";

import { historyBands, inferStep, isolatedIndices, nearestIndex, readingSpan, seriesStats, valueRange, zoomStep } from "./model";

const T0 = 1_790_000_000;
const grid = (cells: number, step = 60): number[] => Array.from({ length: cells }, (_, i) => T0 + i * step);

describe("inferStep", () => {
  it("is the median spacing, so one missing cell does not decide it", () => {
    expect(inferStep([T0, T0 + 60, T0 + 120, T0 + 240, T0 + 300])).toBe(60);
  });
  it("is a minute when there is nothing to measure", () => {
    expect(inferStep([T0])).toBe(60);
  });
});

describe("nearestIndex", () => {
  it("finds the nearest cell of a regular grid", () => {
    const times = grid(10);
    expect(nearestIndex(times, T0 + 125)).toBe(2);
    expect(nearestIndex(times, T0 + 155)).toBe(3);
    expect(nearestIndex(times, T0 - 999)).toBe(0);
    expect(nearestIndex(times, T0 + 99_999)).toBe(9);
    expect(nearestIndex([], T0)).toBeNull();
  });
});

describe("isolatedIndices", () => {
  it("names the readings no line can reach: one alone in a day is a dot, not nothing", () => {
    expect(isolatedIndices([null, null, 5, null, null, null])).toEqual([2]);
  });
  it("does not count a reading one missing cell away from another as alone: that cell is crossed", () => {
    expect(isolatedIndices([1, null, 3, null, null, null, 7])).toEqual([6]);
  });
});

describe("historyBands", () => {
  const series = (values: (number | null)[]) => [{ values }];

  it("hatches the whole window when nothing was recorded in it", () => {
    const times = grid(5);
    expect(historyBands(times, series([null, null, null, null, null]), [T0, T0 + 240], 60)).toEqual([{ from: T0, to: T0 + 240, kind: "before" }]);
  });

  it("hatches the start of a day whose history began an hour ago", () => {
    // A 24-hour window with one hour of readings: the first 23 hours are "before".
    const times = grid(24, 3_600);
    const values = times.map((_, i) => (i === 23 ? 12 : null));
    const bands = historyBands(times, series(values), [T0, T0 + 23 * 3_600], 3_600);
    expect(bands).toEqual([{ from: T0, to: T0 + 23 * 3_600 - 1_800, kind: "before" }]);
  });

  it("hatches a hole of two cells or more, and crosses a single missing cell", () => {
    const times = grid(9);
    const values = [1, null, 2, 3, null, null, null, 4, 5];
    const bands = historyBands(times, series(values), [T0, T0 + 480], 60);
    expect(bands).toEqual([{ from: T0 + 4 * 60 - 30, to: T0 + 7 * 60 - 30, kind: "gap" }]);
  });

  it("hatches the end of the window when the readings stopped", () => {
    const times = grid(6);
    const bands = historyBands(times, series([1, 2, null, null, null, null]), [T0, T0 + 300], 60);
    expect(bands).toEqual([{ from: T0 + 60 + 30, to: T0 + 300, kind: "after" }]);
  });

  it("counts a moment as recorded when any series has a reading", () => {
    const times = grid(4);
    expect(readingSpan(times, [{ values: [null, 1, null, null] }, { values: [null, null, 2, null] }])).toEqual([T0 + 60, T0 + 120]);
  });
});

describe("seriesStats", () => {
  it("averages the readings, and takes the peak from the cells' maxima", () => {
    const times = grid(4);
    const stats = seriesStats(times, [10, null, 30, 20], [12, null, 55, 21]);
    expect(stats).toEqual({ mean: 20, peak: 55, peakAt: T0 + 120, low: 10, latest: 20, latestAt: T0 + 180, count: 3 });
  });
  it("is null without a reading", () => {
    expect(seriesStats(grid(3), [null, null, null])).toBeNull();
  });
  it("counts only the window it is given", () => {
    const stats = seriesStats(grid(4), [10, 20, 30, 40], undefined, [T0 + 60, T0 + 120]);
    expect(stats?.mean).toBe(25);
  });
});

describe("zoomStep", () => {
  const domain = [T0, T0 + 3_600] as const;
  it("halves around the middle, and never below the smallest stretch", () => {
    expect(zoomStep(domain, null, "in", 60)).toEqual([T0 + 900, T0 + 2_700]);
    expect(zoomStep(domain, [T0 + 1_770, T0 + 1_830], "in", 60)).toEqual([T0 + 1_770, T0 + 1_830]);
  });
  it("doubles back out, and says the whole window once it gets there", () => {
    expect(zoomStep(domain, [T0 + 900, T0 + 2_700], "out", 60)).toBeNull();
    expect(zoomStep(domain, [T0 + 3_000, T0 + 3_300], "out", 60)).toEqual([T0 + 2_850, T0 + 3_450]);
    // Near an end it stays inside the window.
    expect(zoomStep(domain, [T0 + 3_300, T0 + 3_600], "out", 60)).toEqual([T0 + 3_000, T0 + 3_600]);
  });
});

describe("valueRange", () => {
  it("starts at zero with room above the highest reading", () => {
    expect(valueRange(2, 10, false)).toEqual([0, 11.5]);
  });
  it("reaches a drawn limit", () => {
    expect(valueRange(2, 10, false, 40)).toEqual([0, 46]);
  });
  it("fits around readings that barely move, never below zero", () => {
    const [low, high] = valueRange(600, 610, true);
    expect(low).toBeGreaterThan(500);
    expect(high).toBeGreaterThan(610);
    expect(valueRange(0, 1, true)[0]).toBe(0);
  });
  it("has a range even with nothing to show", () => {
    expect(valueRange(null, null, false)).toEqual([0, 1]);
  });
});
