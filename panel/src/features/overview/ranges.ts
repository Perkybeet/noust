/**
 * The time ranges every chart page offers (the Overview's machine charts, an application's
 * Metrics tab): one set, one default, one set of words. Just values and keys, so a route's
 * `validateSearch` can read them without pulling a chart (and uPlot) into the eagerly loaded
 * route-options chunk.
 */

import type { MetricWindow } from "../../api/queries/metrics";
import type { PlainKey } from "../../i18n";

export type MetricRange = Extract<MetricWindow, "1h" | "24h" | "7d" | "30d">;

export interface RangeSpec {
  value: MetricRange;
  /** The compact code shown on the range control itself: not translated, like a unit symbol. */
  label: string;
  seconds: number;
  /** The window in words, for a chart's description: "Last 7 days". */
  words: PlainKey;
}

const SPECS: Readonly<Record<MetricRange, RangeSpec>> = {
  "1h": { value: "1h", label: "1h", seconds: 3_600, words: "overview.range.1h" },
  "24h": { value: "24h", label: "24h", seconds: 86_400, words: "overview.range.24h" },
  "7d": { value: "7d", label: "7d", seconds: 7 * 86_400, words: "overview.range.7d" },
  "30d": { value: "30d", label: "30d", seconds: 30 * 86_400, words: "overview.range.30d" },
};

export const RANGES: readonly RangeSpec[] = [SPECS["1h"], SPECS["24h"], SPECS["7d"], SPECS["30d"]];

/** A day: long enough to show a pattern, short enough to read minute by minute. */
export const DEFAULT_RANGE: MetricRange = "24h";

export function isRange(value: unknown): value is MetricRange {
  return typeof value === "string" && RANGES.some((spec) => spec.value === value);
}

export function rangeSpec(range: MetricRange): RangeSpec {
  return SPECS[range];
}
