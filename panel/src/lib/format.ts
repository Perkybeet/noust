/**
 * How the console writes numbers, sizes, durations and times. One implementation, so a
 * memory figure reads the same in the apps table, the app page and a chart axis.
 *
 * Every function takes the language to write in and defaults to the active one, so decimal
 * marks, grouping, month names and "ago" follow the console's language ("1.5 KB" and
 * "1,5 KB"). A component re-renders on a language change when it calls `useT()` or
 * `useLocale()`; one that only formats should pass `useLocale()[0]` through. Sizes are binary
 * (1 KB = 1024 B), the way `df -h`, `free -h` and systemd's MemoryMax count them; unit
 * symbols are the same in every language.
 */

import { getLocale } from "../app/locale";
import type { Locale } from "../app/locale";
import { translate } from "../i18n/translate";

// Building an Intl formatter is expensive and a table formats hundreds of cells: one per
// language and set of options.
const numberFormats = new Map<string, Intl.NumberFormat>();
const dateFormats = new Map<string, Intl.DateTimeFormat>();
const relativeFormats = new Map<Locale, Intl.RelativeTimeFormat>();

function numberFormat(locale: Locale, options: Intl.NumberFormatOptions = {}): Intl.NumberFormat {
  const key = `${locale}|${JSON.stringify(options)}`;
  let format = numberFormats.get(key);
  if (format === undefined) {
    format = new Intl.NumberFormat(locale, options);
    numberFormats.set(key, format);
  }
  return format;
}

function dateFormat(locale: Locale, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = `${locale}|${JSON.stringify(options)}`;
  let format = dateFormats.get(key);
  if (format === undefined) {
    format = new Intl.DateTimeFormat(locale, options);
    dateFormats.set(key, format);
  }
  return format;
}

function relativeFormat(locale: Locale): Intl.RelativeTimeFormat {
  let format = relativeFormats.get(locale);
  if (format === undefined) {
    // Narrow is the compact form the tables have room for: "3m ago", "hace 3 min".
    format = new Intl.RelativeTimeFormat(locale, { style: "narrow", numeric: "always" });
    relativeFormats.set(locale, format);
  }
  return format;
}

/** A number with exactly `digits` decimals, in the language's notation. */
function fixed(value: number, digits: number, locale: Locale): string {
  return numberFormat(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: false }).format(value);
}

const BYTE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"] as const;

/** Few significant figures: two below 1, one decimal below 10, whole numbers above. */
function significant(value: number, locale: Locale): string {
  const abs = Math.abs(value);
  const digits = abs > 0 && abs < 1 ? 2 : abs < 10 ? 1 : 0;
  return fixed(value, digits, locale);
}

/**
 * A size in bytes: "512 B", "1.5 KB", "96 MB", "6.2 GB". A unit is left for the next one up
 * once it would print four digits, so 1006 GB reads "0.98 TB".
 */
export function formatBytes(bytes: number, locale: Locale = getLocale()): string {
  if (!Number.isFinite(bytes)) return "-";
  const sign = bytes < 0 ? "-" : "";
  let value = Math.abs(bytes);
  let unit = 0;
  while (value >= 1000 && unit < BYTE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const text = unit === 0 ? String(Math.round(value)) : significant(value, locale);
  return `${sign}${text} ${BYTE_UNITS[unit] ?? "B"}`;
}

/** A transfer rate: "1.2 MB/s". */
export function formatBytesRate(bytesPerSecond: number, locale: Locale = getLocale()): string {
  return `${formatBytes(bytesPerSecond, locale)}/s`;
}

/**
 * A percentage already on the 0-100 scale: "5.7%", "32%" ("5,7 %" in Spanish). One decimal
 * below ten, where the decimal is the difference between idle and busy; whole numbers above,
 * where it is noise.
 */
export function formatPercent(value: number, locale: Locale = getLocale()): string {
  if (!Number.isFinite(value)) return "-";
  const abs = Math.abs(value);
  const digits = abs !== 0 && abs < 10 ? 1 : 0;
  return numberFormat(locale, { style: "percent", minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value / 100);
}

/** A count: "1,284" in full up to ten thousand, then compact ("12.9K", "4.2M"). */
export function formatCount(value: number, locale: Locale = getLocale()): string {
  if (!Number.isFinite(value)) return "-";
  return Math.abs(value) < 10_000
    ? numberFormat(locale).format(value)
    : numberFormat(locale, { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

/** A plain number with at most one decimal: "1,284.5" ("1284,5" in Spanish). */
export function formatDecimal(value: number, locale: Locale = getLocale()): string {
  if (!Number.isFinite(value)) return "-";
  return numberFormat(locale, { maximumFractionDigits: 1 }).format(value);
}

/** A load average, to two decimals like `uptime` prints it: "0.52" ("0,52" in Spanish). */
export function formatLoad(value: number, locale: Locale = getLocale()): string {
  if (!Number.isFinite(value)) return "-";
  return fixed(value, 2, locale);
}

/**
 * A span of time in seconds as the two largest units: "3 ms", "2.4s", "14s", "2m 05s",
 * "1h 12m", "3d 4h" ("2 min 05 s", "1 h 12 min" in Spanish: the unit words are the
 * catalog's, under time.duration).
 */
export function formatDuration(seconds: number, locale: Locale = getLocale()): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "-";
  if (seconds < 1) return translate(locale, "time.duration.milliseconds", { value: Math.max(1, Math.round(seconds * 1000)) });
  if (seconds < 10) return translate(locale, "time.duration.seconds", { value: fixed(Math.floor(seconds * 10) / 10, 1, locale) });
  const whole = Math.floor(seconds);
  if (whole < 60) return translate(locale, "time.duration.seconds", { value: whole });
  const days = Math.floor(whole / 86_400);
  const hours = Math.floor((whole % 86_400) / 3_600);
  const minutes = Math.floor((whole % 3_600) / 60);
  const secs = whole % 60;
  if (days > 0) return translate(locale, "time.duration.daysHours", { days, hours });
  if (hours > 0) return translate(locale, "time.duration.hoursMinutes", { hours, minutes });
  return translate(locale, "time.duration.minutesSeconds", { minutes, seconds: String(secs).padStart(2, "0") });
}

// ---------------------------------------------------------------------------------------
// Time

/** "2026-09-25T19:20:35.378313", with or without an offset, microseconds allowed. */
const ISO = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$/;

/** systemd's own timestamps: "Fri 2026-09-25 13:06:35 UTC", "Fri 2026-09-25 13:06:35 +0200". */
const SYSTEMD = /^(?:[A-Z][a-z]{2} )?(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})(?: (UTC|GMT|[+-]\d{2}:?\d{2}))$/;

function offsetOf(zone: string): string {
  if (zone === "UTC" || zone === "GMT" || zone === "Z") return "Z";
  return zone.includes(":") ? zone : `${zone.slice(0, 3)}:${zone.slice(3)}`;
}

/**
 * Reads the timestamps the backend sends into a Date, or null when the text is not one the
 * console can place in time.
 *
 * - ISO 8601 with an offset is exact. Without one (the store writes naive local times) it
 *   is read as local time, which is right when the browser and the server share a zone.
 * - systemd prints its timestamps in the server's zone by abbreviation; only UTC/GMT and
 *   numeric offsets are unambiguous, so "CEST" and friends return null and the caller shows
 *   the text verbatim instead of guessing.
 * - Numbers are Unix time, in seconds below 10^12 and in milliseconds above.
 */
export function parseTimestamp(value: string | number | Date | null | undefined): Date | null {
  if (value === null || value === undefined) return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return null;
    return new Date(value < 1e12 ? value * 1000 : value);
  }
  const text = value.trim();
  const iso = ISO.exec(text);
  if (iso) {
    const [, date, time, fraction, zone] = iso;
    // Engines disagree on more than three fractional digits; milliseconds are plenty.
    const millis = fraction === undefined ? "" : `.${fraction.slice(0, 3).padEnd(3, "0")}`;
    const parsed = new Date(`${date ?? ""}T${time ?? ""}${millis}${zone === undefined ? "" : offsetOf(zone)}`);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }
  const systemd = SYSTEMD.exec(text);
  if (systemd) {
    const [, date, time, zone] = systemd;
    const parsed = new Date(`${date ?? ""}T${time ?? ""}${offsetOf(zone ?? "Z")}`);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }
  return null;
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

/**
 * A day: "Dec 24, 2026" ("24 dic 2026" in Spanish), or without the year: "Sep 12".
 */
export function formatDate(date: Date, { year = true }: { year?: boolean } = {}, locale: Locale = getLocale()): string {
  return dateFormat(locale, year ? { year: "numeric", month: "short", day: "numeric" } : { month: "short", day: "numeric" }).format(date);
}

/** A 24-hour clock, "14:05": the way server logs print time, and short enough for an axis. */
export function formatClock(date: Date, locale: Locale = getLocale()): string {
  return dateFormat(locale, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(date);
}

/** The same clock to the second, "14:05:32": for readings a few seconds apart. */
export function formatClockSeconds(date: Date, locale: Locale = getLocale()): string {
  return dateFormat(locale, { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).format(date);
}

/**
 * A moment in words, to the second: "Sep 25, 2026, 14:32:05" ("25 sept 2026, 14:32:05"), for
 * assistive technology. Explicit fields rather than `dateStyle`/`timeStyle`, which cannot be
 * combined with other fields (and throw with `timeZoneName` on the ICU build this ships with).
 */
export function formatMoment(date: Date, locale: Locale = getLocale()): string {
  return dateFormat(locale, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(date);
}

/**
 * How long ago (or how far ahead) a moment is, compactly: "just now", "42s ago", "3m ago",
 * "5h ago", "4d ago", then the date ("Sep 12", or "Sep 12, 2025" in another year). The
 * units come from `Intl.RelativeTimeFormat` ("hace 3 min"); "just now" from the catalog.
 */
export function formatRelative(date: Date, now: Date = new Date(), locale: Locale = getLocale()): string {
  const delta = Math.round((now.getTime() - date.getTime()) / 1000);
  const sign = delta < 0 ? 1 : -1;
  const seconds = Math.abs(delta);
  const say = (amount: number, unit: Intl.RelativeTimeFormatUnit): string => relativeFormat(locale).format(sign * amount, unit);
  // Either side of now by a few seconds is the same moment: clocks and ticks drift that much.
  if (seconds < 10) return translate(locale, "time.justNow");
  if (seconds < 60) return say(seconds, "second");
  if (seconds < 3_600) return say(Math.floor(seconds / 60), "minute");
  if (seconds < 86_400) return say(Math.floor(seconds / 3_600), "hour");
  if (seconds < 7 * 86_400) return say(Math.floor(seconds / 86_400), "day");
  return formatDate(date, { year: date.getFullYear() !== now.getFullYear() }, locale);
}

/** The zone's short name where the browser knows one ("WEST", "UTC", "GMT+2"). */
function zoneName(date: Date, locale: Locale): string {
  const part = dateFormat(locale, { timeZoneName: "short" })
    .formatToParts(date)
    .find((p) => p.type === "timeZoneName");
  return part?.value ?? "";
}

/**
 * A moment in full, the way server logs print it: "2026-09-25 20:36:10 WEST". Unambiguous in
 * every locale and directly comparable with journal and nginx lines.
 */
export function formatDateTime(date: Date, locale: Locale = getLocale()): string {
  const day = `${String(date.getFullYear())}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const time = `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  const zone = zoneName(date, locale);
  return zone === "" ? `${day} ${time}` : `${day} ${time} ${zone}`;
}

/**
 * Milliseconds until `formatRelative` of this moment next changes its text, so a live
 * label re-renders exactly when it would read differently and never in between.
 */
export function relativeRefreshMs(date: Date, now: Date = new Date()): number {
  const age = Math.abs(now.getTime() - date.getTime());
  if (age < 60_000) return 1_000;
  if (age < 3_600_000) return 30_000;
  if (age < 86_400_000) return 5 * 60_000;
  return 60 * 60_000;
}
