/**
 * How a chart writes time: on its axis, in its readout and in the sentence that says what it
 * shows. Every moment goes through lib/format.ts; this only decides how much of it to say.
 */

import { getLocale } from "../../../app/locale";
import type { Locale, T } from "../../../i18n";
import { formatClock, formatClockSeconds, formatDate } from "../../../lib/format";
import type { ChartWindow } from "./model";

/** The span, in seconds, past which an axis or table needs a date rather than a bare clock. */
const TWO_DAYS = 2 * 86_400;
/** Below this, a short span still gets a date if it happens to cross midnight. */
const HALF_DAY = 43_200;
/** A visible stretch this short needs seconds on its axis, or its ticks repeat the minute. */
export const SECONDS_SPAN = 600;

function isMidnightLocal(date: Date): boolean {
  return date.getHours() === 0 && date.getMinutes() === 0 && date.getSeconds() === 0;
}

/**
 * A moment as its span calls for: a bare 24-hour clock under about two days, the date and time
 * beyond it ("Sep 25 14:00", "25 sept 14:00"), the bare date at a tick exactly on midnight, and
 * the seconds when readings are seconds apart ("14:00:05").
 */
export function formatChartTime(seconds: number, withDate: boolean, locale: Locale = getLocale(), withSeconds = false): string {
  const date = new Date(seconds * 1000);
  const clock = withSeconds ? formatClockSeconds(date, locale) : formatClock(date, locale);
  if (!withDate) return clock;
  const day = formatDate(date, { year: false }, locale);
  return !withSeconds && isMidnightLocal(date) ? day : `${day} ${clock}`;
}

/**
 * Whether a stretch needs dates, not just a clock, on its axis: more than about two days (the 7
 * and 30 day windows), or much less than a day that happens to cross local midnight (23:30 to
 * 00:30 is two different days). The 24 hour window crosses one midnight by construction and
 * keeps a bare clock: every hour of it is named once.
 */
export function spanNeedsDate([from, to]: ChartWindow): boolean {
  const span = to - from;
  if (span > TWO_DAYS) return true;
  if (span >= HALF_DAY) return false;
  return new Date(from * 1000).toDateString() !== new Date(to * 1000).toDateString();
}

/** The same rule for a list of timestamps (their first and last). */
export function needsDateFormat(timestamps: readonly number[]): boolean {
  const first = timestamps[0];
  const last = timestamps.at(-1);
  if (first === undefined || last === undefined) return false;
  return spanNeedsDate([first, last]);
}

/**
 * Whether one reading needs its date to be unambiguous: in a window of a day or more, "21:40"
 * happened twice.
 */
export function momentNeedsDate([from, to]: ChartWindow): boolean {
  return to - from >= 86_400 - 1 || new Date(from * 1000).toDateString() !== new Date(to * 1000).toDateString();
}

/** The raw tier's spacing: a cell this wide or narrower is one reading, not a mean. */
export const RAW_STEP = 5;

type StepUnit = "seconds" | "minutes" | "hours" | "days";

function stepUnit(step: number): { unit: StepUnit; count: number } {
  if (step >= 86_400 && step % 86_400 === 0) return { unit: "days", count: step / 86_400 };
  if (step >= 3_600 && step % 3_600 === 0) return { unit: "hours", count: step / 3_600 };
  if (step >= 60 && step % 60 === 0) return { unit: "minutes", count: step / 60 };
  return { unit: "seconds", count: Math.round(step) };
}

/**
 * What each cell of the grid is, in words to go inside a sentence: "a reading every 5
 * seconds" for the raw tier, "1-minute averages" for anything wider. Said from the data read,
 * never from the window's width.
 */
export function resolutionWords(t: T, resolution: string | null | undefined, step: number): string {
  if (resolution === "raw" && step <= RAW_STEP) return t("common.chart.readingEvery", { count: Math.max(1, Math.round(step)) });
  const { unit, count } = stepUnit(step);
  return t(`common.chart.averages.${unit}`, { count });
}

/** What one cell is, for the readout of a single moment: "1-minute average"; null for a single reading. */
export function cellWords(t: T, resolution: string | null | undefined, step: number): string | null {
  if (resolution === "raw" && step <= RAW_STEP) return null;
  const { unit, count } = stepUnit(step);
  return t(`common.chart.average.${unit}`, { count });
}
