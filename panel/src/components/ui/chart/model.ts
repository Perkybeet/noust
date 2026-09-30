/**
 * The arithmetic of a time-series chart, kept apart from the drawing so every rule is tested
 * without a canvas: where a moment falls on the grid, which readings cannot be joined by a line,
 * where the history has holes, and what a series amounts to over a window.
 *
 * A chart's data is a regular grid (the metrics API returns one): one timestamp per cell,
 * `step` seconds apart, and in every series a value or `null` per cell. `null` is "no reading",
 * never zero: the line breaks there.
 */

/** A stretch of the time axis, Unix seconds, [from, to]. */
export type ChartWindow = readonly [number, number];

/**
 * The typical spacing of a series of timestamps: the median gap, so one missing cell or a
 * shorter first cell does not decide it. One minute when there is nothing to measure.
 */
export function inferStep(timestamps: readonly number[]): number {
  const gaps: number[] = [];
  for (let i = 1; i < timestamps.length; i += 1) {
    const gap = (timestamps[i] ?? 0) - (timestamps[i - 1] ?? 0);
    if (gap > 0) gaps.push(gap);
  }
  if (gaps.length === 0) return 60;
  gaps.sort((a, b) => a - b);
  return gaps[Math.floor(gaps.length / 2)] ?? 60;
}

/** The index of the cell nearest to `at`, or null for an empty grid. */
export function nearestIndex(timestamps: readonly number[], at: number): number | null {
  const count = timestamps.length;
  if (count === 0) return null;
  let lo = 0;
  let hi = count - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if ((timestamps[mid] ?? 0) <= at) lo = mid;
    else hi = mid;
  }
  const before = timestamps[lo] ?? 0;
  const after = timestamps[hi] ?? 0;
  return Math.abs(at - before) <= Math.abs(after - at) ? lo : hi;
}

function present(value: number | null | undefined): value is number {
  return value !== null && value !== undefined && Number.isFinite(value);
}

/**
 * How many empty cells in a row the line still crosses: one missing reading is a hiccup (a
 * tick that came late, a store that consolidated a moment later), not a hole in the history.
 * Two or more break the line, and the chart hatches them.
 */
export const JOINED_CELLS = 1;

/**
 * The readings with nothing near enough on either side to draw a line to: drawn as dots, or
 * they would not be drawn at all (a server that has recorded one minute of a day has exactly
 * one such reading).
 */
export function isolatedIndices(values: readonly (number | null)[], from = 0, to = values.length - 1): number[] {
  const out: number[] = [];
  const reach = JOINED_CELLS + 1;
  for (let i = Math.max(0, from); i <= Math.min(values.length - 1, to); i += 1) {
    if (!present(values[i])) continue;
    let neighbour = false;
    for (let d = 1; d <= reach && !neighbour; d += 1) neighbour = present(values[i - d]) || present(values[i + d]);
    if (!neighbour) out.push(i);
  }
  return out;
}

/** The first and last moment any series has a reading at, or null when none has one. */
export function readingSpan(timestamps: readonly number[], series: readonly { values: readonly (number | null)[] }[]): ChartWindow | null {
  const found: number[] = [];
  timestamps.forEach((at, i) => {
    if (series.some((s) => present(s.values[i]))) found.push(at);
  });
  const first = found[0];
  const last = found.at(-1);
  return first === undefined || last === undefined ? null : [first, last];
}

/** A stretch of the axis with no history, and why: before recording began, a hole, or since it stopped. */
export interface ChartBand {
  from: number;
  to: number;
  kind: "before" | "gap" | "after";
}

/**
 * The stretches of the domain with no reading in any series, the way the chart hatches them:
 * before the first reading (or before `firstSampleAt`, when history started earlier than this
 * window shows), holes of at least `minCells` cells between readings, and after the last one
 * when it is older than `minCells` cells: the same holes the line breaks at. A domain with no
 * reading at all is one "before" band.
 */
export function historyBands(
  timestamps: readonly number[],
  series: readonly { values: readonly (number | null)[] }[],
  domain: ChartWindow,
  step: number,
  minCells = JOINED_CELLS + 1,
): ChartBand[] {
  const [from, to] = domain;
  const span = readingSpan(timestamps, series);
  if (span === null) return [{ from, to, kind: "before" }];
  const bands: ChartBand[] = [];
  const [first, last] = span;
  const half = step / 2;
  if (first - from >= minCells * step) bands.push({ from, to: first - half, kind: "before" });
  let runStart: number | null = null;
  let runCells = 0;
  timestamps.forEach((at, i) => {
    if (at <= first || at >= last) return;
    const empty = !series.some((s) => present(s.values[i]));
    if (empty) {
      runStart ??= at;
      runCells += 1;
      return;
    }
    if (runStart !== null && runCells >= minCells) bands.push({ from: runStart - half, to: at - half, kind: "gap" });
    runStart = null;
    runCells = 0;
  });
  if (to - last >= minCells * step) bands.push({ from: last + half, to, kind: "after" });
  return bands;
}

/** What a series amounts to over a window. */
export interface SeriesStats {
  /** The mean of the readings (each cell counts once). */
  mean: number;
  /** The highest reading: a cell's maximum when the series has them, else its value. */
  peak: number;
  /** When the peak was read, Unix seconds. */
  peakAt: number;
  low: number;
  /** The newest reading and when it was read. */
  latest: number;
  latestAt: number;
  /** How many cells had a reading. */
  count: number;
}

/** The statistics of one series over `window` (all of it without one), or null with no reading. */
export function seriesStats(
  timestamps: readonly number[],
  values: readonly (number | null)[],
  peaks?: readonly (number | null)[],
  window?: ChartWindow | null,
): SeriesStats | null {
  let total = 0;
  let count = 0;
  let low = Infinity;
  let peak = -Infinity;
  let peakAt = 0;
  let latest = 0;
  let latestAt = 0;
  timestamps.forEach((at, i) => {
    if (window && (at < window[0] || at > window[1])) return;
    const value = values[i];
    if (!present(value)) return;
    total += value;
    count += 1;
    low = Math.min(low, value);
    const high = peaks?.[i];
    const top = present(high) ? Math.max(high, value) : value;
    if (top > peak) {
      peak = top;
      peakAt = at;
    }
    latest = value;
    latestAt = at;
  });
  if (count === 0) return null;
  return { mean: total / count, peak, peakAt, low, latest, latestAt, count };
}

/**
 * The window after zooming in (half the span) or out (twice it) around the middle of what is
 * shown, kept inside `domain`; null once it covers the whole domain again. Zooming in stops at
 * `minSpan` seconds, where there is nothing more to see.
 */
export function zoomStep(domain: ChartWindow, current: ChartWindow | null, direction: "in" | "out", minSpan: number): ChartWindow | null {
  const [first, last] = domain;
  if (last <= first) return current;
  const [start, end] = current ?? domain;
  const middle = (start + end) / 2;
  const span = direction === "in" ? Math.max(minSpan, (end - start) / 2) : (end - start) * 2;
  if (span >= last - first) return null;
  if (direction === "in" && span >= end - start) return current;
  let next: [number, number] = [middle - span / 2, middle + span / 2];
  if (next[0] < first) next = [first, first + span];
  if (next[1] > last) next = [last - span, last];
  return [Math.round(next[0]), Math.round(next[1])];
}

/** The markers whose moment falls within `[from, to]`; the rest are not drawn anywhere. */
export function markersInRange<M extends { at: number }>(markers: readonly M[], from: number, to: number): M[] {
  return markers.filter((marker) => marker.at >= from && marker.at <= to);
}

/**
 * The value axis of a chart without a fixed range: from zero to a little above the highest
 * reading (or the limit, when it is drawn), so a quiet series is not magnified into noise; or,
 * with `fit`, around the readings themselves (a disk that is 60% full and barely moves).
 */
export function valueRange(min: number | null, max: number | null, fit: boolean, include?: number | null): [number, number] {
  const hasData = min !== null && max !== null && Number.isFinite(min) && Number.isFinite(max);
  let top = hasData ? max : 0;
  if (include !== null && include !== undefined) top = Math.max(top, include);
  if (!fit || !hasData) return [0, top > 0 ? top * 1.15 : 1];
  const spread = Math.max(max - min, Math.abs(max) * 0.04, 1e-9);
  const low = Math.max(0, min - spread * 0.6);
  return [low, Math.max(top, max) + spread * 0.6];
}
