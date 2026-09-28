/**
 * The time ranges of an app's charts, and what the charts say in words.
 *
 * The history endpoint reads each range as a window of its own, from the store's tiers: an hour
 * of raw samples, a day of minute means, a week and a month of hour means. It says which in
 * `resolution`, and the charts say it in words.
 */

import type { MetricWindow } from "../../../api/queries/metrics";
import { getLocale } from "../../../app/locale";
import type { Locale } from "../../../app/locale";
import { translate } from "../../../i18n";
import type { MessageKey } from "../../../i18n";

export type MetricRange = "1h" | "24h" | "7d" | "30d";

export interface RangeSpec {
  value: MetricRange;
  /** The compact code shown on the range control itself: not translated, like a unit symbol. */
  label: string;
  seconds: number;
  /** The window the history endpoint is asked for. */
  window: MetricWindow;
}

const SPECS: Readonly<Record<MetricRange, RangeSpec>> = {
  "1h": { value: "1h", label: "1h", seconds: 3_600, window: "1h" },
  "24h": { value: "24h", label: "24h", seconds: 86_400, window: "24h" },
  "7d": { value: "7d", label: "7d", seconds: 7 * 86_400, window: "7d" },
  "30d": { value: "30d", label: "30d", seconds: 30 * 86_400, window: "30d" },
};

export const RANGES: readonly RangeSpec[] = [SPECS["1h"], SPECS["24h"], SPECS["7d"], SPECS["30d"]];

export const DEFAULT_RANGE: MetricRange = "24h";

export function rangeSpec(range: MetricRange): RangeSpec {
  return SPECS[range];
}

const RANGE_KEYS: Readonly<Record<MetricRange, MessageKey>> = {
  "1h": "appPages.metrics.range.1h",
  "24h": "appPages.metrics.range.24h",
  "7d": "appPages.metrics.range.7d",
  "30d": "appPages.metrics.range.30d",
};

/** The range in words, for a chart's description: "Last 7 days". */
export function rangeWords(range: MetricRange, locale: Locale = getLocale()): string {
  return translate(locale, RANGE_KEYS[range]);
}

export function isRange(value: unknown): value is MetricRange {
  return typeof value === "string" && RANGES.some((spec) => spec.value === value);
}

export type Points = readonly (readonly [number, number])[];

/** The points of a read that fall in the range ending now (Unix seconds). */
export function clip(points: Points | undefined, range: MetricRange, now: number): Points {
  if (points === undefined) return [];
  const from = now - rangeSpec(range).seconds;
  return points.filter(([time]) => time > from);
}

export interface Summary {
  latest: number;
  average: number;
  peak: number;
  /** When the peak was read, Unix seconds. */
  peakAt: number;
}

export function summarise(points: Points): Summary | null {
  const last = points.at(-1);
  if (last === undefined) return null;
  let total = 0;
  let peak = points[0] ?? last;
  for (const point of points) {
    total += point[1];
    if (point[1] > peak[1]) peak = point;
  }
  return { latest: last[1], average: total / points.length, peak: peak[1], peakAt: peak[0] };
}

const timeFormats = new Map<Locale, Intl.DateTimeFormat>();
const dayFormats = new Map<Locale, Intl.DateTimeFormat>();

function timeFormat(locale: Locale): Intl.DateTimeFormat {
  let format = timeFormats.get(locale);
  if (format === undefined) {
    format = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
    timeFormats.set(locale, format);
  }
  return format;
}

function dayFormat(locale: Locale): Intl.DateTimeFormat {
  let format = dayFormats.get(locale);
  if (format === undefined) {
    format = new Intl.DateTimeFormat(locale, { month: "short", day: "numeric" });
    dayFormats.set(locale, format);
  }
  return format;
}

/** A moment as short as the range allows: "14:05" within a day, "Sep 22, 14:05" beyond. */
export function momentWords(seconds: number, range: MetricRange, locale: Locale = getLocale()): string {
  const date = new Date(seconds * 1000);
  const time = timeFormat(locale).format(date);
  if (range === "1h" || range === "24h") return time;
  return `${dayFormat(locale).format(date)}, ${time}`;
}

/** One sentence a person reads instead of the chart: "Average 3.1%, peak 41% at 14:05, now 2.4%." */
export function sentence(summary: Summary, range: MetricRange, format: (value: number) => string, limit: number | null, locale: Locale = getLocale()): string {
  const when = momentWords(summary.peakAt, range, locale);
  return limit !== null
    ? translate(locale, "appPages.metrics.sentenceWithLimit", { average: format(summary.average), peak: format(summary.peak), when, latest: format(summary.latest), limit: format(limit) })
    : translate(locale, "appPages.metrics.sentenceNoLimit", { average: format(summary.average), peak: format(summary.peak), when, latest: format(summary.latest) });
}
