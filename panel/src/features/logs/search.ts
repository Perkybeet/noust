/**
 * The question of the Logs page as it lives in the URL, in a module of its own: a route's options
 * are loaded with the first page, so the address must be read without importing what turns the
 * question into requests and lines (the query language among it).
 */

/**
 * How far back a search looks, with "live" as one of the choices: the newest lines, read again
 * every few seconds. A boot is a stretch of the journal and only the system has one.
 */
export const RANGES = ["live", "15m", "1h", "6h", "24h", "7d", "boot", "previous"] as const;
export type LogRange = (typeof RANGES)[number];
export const DEFAULT_RANGE = "1h" satisfies LogRange;

/** The ranges the applications' search offers: a boot is not a window of an application's lines. */
export const APP_RANGES: readonly LogRange[] = ["live", "15m", "1h", "6h", "24h", "7d"];

/** The worst level kept; nothing means every level. */
export const LEVELS = ["err", "warning", "notice", "info"] as const;
export type LogLevel = (typeof LEVELS)[number];

/** `fleet` searches every server a central manages instead of the one in view. */
export type LogsScope = "fleet";

/** Where the lines come from: the applications' (the default), or the whole system's journal. */
export type LogsSource = "system";

export interface LogsSearch {
  /** Absent means the applications. */
  source?: LogsSource;
  q?: string;
  /** Applications: which one. */
  app?: string;
  /** System: which service (or `kernel`). */
  unit?: string;
  scope?: LogsScope;
  /** With `scope` set: only this server's lines (`@central` is this central itself); absent, every server's. */
  server?: string;
  /** Absent means the last hour. */
  since?: Exclude<LogRange, "1h">;
  level?: LogLevel;
  /** A stretch picked elsewhere (a chart, the timeline), Unix seconds: it replaces the range. */
  from?: number;
  to?: number;
  /** The journal entry a link points at: its cursor. The page opens it in context. */
  line?: string;
}

function text(value: unknown, max: number): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed.slice(0, max);
}

/**
 * What was typed in the search box, as it was typed: a space at the end is the start of the next
 * word, and a box that trimmed it would swallow it before the operator could type the word.
 */
function typed(value: unknown, max: number): string | undefined {
  return typeof value === "string" && value.trim() !== "" ? value.slice(0, max) : undefined;
}

function seconds(value: unknown): number | undefined {
  const parsed = typeof value === "number" ? value : typeof value === "string" && /^\d{1,11}$/.test(value) ? Number(value) : Number.NaN;
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
}

/** Reads the search params, dropping anything malformed instead of failing. */
export function validateLogsSearch(search: Record<string, unknown>): LogsSearch {
  const q = typed(search["q"], 1000);
  const app = text(search["app"], 253);
  const unit = text(search["unit"], 200);
  const line = text(search["line"], 400);
  const server = text(search["server"], 64);
  const since = search["since"];
  const level = search["level"];
  const from = seconds(search["from"]);
  const to = seconds(search["to"]);
  // A stretch is both ends or neither, and it runs forward.
  const stretch = from !== undefined && to !== undefined && to > from;
  return {
    ...(search["source"] === "system" ? { source: "system" as const } : {}),
    ...(q !== undefined ? { q } : {}),
    ...(app !== undefined ? { app } : {}),
    ...(unit !== undefined ? { unit } : {}),
    ...(search["scope"] === "fleet" ? { scope: "fleet" as const, ...(server !== undefined ? { server } : {}) } : {}),
    ...(typeof since === "string" && since !== DEFAULT_RANGE && (RANGES as readonly string[]).includes(since) ? { since: since as Exclude<LogRange, "1h"> } : {}),
    ...(typeof level === "string" && (LEVELS as readonly string[]).includes(level) ? { level: level as LogLevel } : {}),
    ...(stretch ? { from, to } : {}),
    ...(line !== undefined ? { line } : {}),
  };
}

/** The range a search is on, once the one its source cannot offer is set aside. */
export function rangeOf(search: LogsSearch): LogRange {
  const range = search.since ?? DEFAULT_RANGE;
  return search.source === "system" || APP_RANGES.includes(range) ? range : DEFAULT_RANGE;
}

/** Whether the range is one that keeps reading: the newest lines, again and again. */
export function isLive(search: LogsSearch): boolean {
  return rangeOf(search) === "live" && search.from === undefined;
}
