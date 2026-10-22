/**
 * The question of the Logs page as it lives in the URL, what each source makes of it, and how a
 * line is drawn.
 *
 * Logs is one page with two sources: the applications' (the journal's lines and the containers'
 * output, `/api/logs/search`) and the system's (the whole journal, `/api/server/logs/search`).
 * Both read the same query language (`./query.ts`) and the same range, so a link means the same
 * thing on either; each source turns the question into the parameters of its own endpoint here.
 * The server owns the language: the console sends the words as typed and shows its refusal as it
 * wrote it.
 */

import type { LogSearchLine, LogSearchParams } from "../../api/queries/logSearch";
import type { LogLine } from "../../components/ui/LogViewer";
import type { JournalSearchFilters } from "../server/queries";
import { composeQuery } from "./query";

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

/** Most lines one search returns; the server's own ceiling is 1000. */
export const LINES = 300;
/** Lines asked of the system at first, and the most the API returns in one search. */
export const SYSTEM_LINES = 300;
export const SYSTEM_MORE_LINES = 1000;

/** The most entries the journal examines for a search at first, and when asked to look further. */
export const SCAN = 5_000;
export const SCAN_FURTHER = 20_000;

/** How often a live source is read again, and how far back it looks while it is live. */
export const LIVE_MS = 5_000;
export const LIVE_WINDOW = "15m";

/** `fleet` searches every server a central manages instead of the one in view. */
export type LogsScope = "fleet";

/** Where the lines come from: the applications' (the default), or the whole system's journal. */
export type LogsSource = "system";

/** The pseudo unit that reads the kernel's messages. */
export const KERNEL = "kernel";

export interface LogsSearch {
  /** Absent means the applications. */
  source?: LogsSource;
  q?: string;
  /** Applications: which one. */
  app?: string;
  /** System: which service (or `kernel`). */
  unit?: string;
  scope?: LogsScope;
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
    ...(search["scope"] === "fleet" ? { scope: "fleet" as const } : {}),
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

// -- The applications' search ---------------------------------------------------------------

/** The API's question for what the URL asks of the applications. */
export function paramsOf(search: LogsSearch): LogSearchParams {
  const range = rangeOf(search);
  return {
    q: search.q ?? "",
    apps: search.app === undefined ? [] : [search.app],
    since: range === "live" ? LIVE_WINDOW : range,
    level: search.level,
    limit: LINES,
  };
}

// -- The system's journal -------------------------------------------------------------------

/** The relative times the journal reads, for the ranges that are a window. */
const JOURNAL_SINCE: Readonly<Record<string, string>> = { live: "-15min", "15m": "-15min", "1h": "-1h", "6h": "-6h", "24h": "-24h", "7d": "-7d" };

/**
 * The query the system's source asks: the service and the level as the fields of the language,
 * then what was typed. The kernel is not a unit but a source of its own (`--dmesg`), asked for by a
 * parameter, so it is not a field.
 */
export function effectiveQuery(search: LogsSearch): string {
  return composeQuery(
    { ...(search.unit !== undefined && search.unit !== KERNEL ? { unit: search.unit } : {}), ...(search.level !== undefined ? { level: search.level } : {}) },
    search.q ?? "",
  );
}

/** The journal API's parameters for what the URL asks of the system. */
export function journalFilters(search: LogsSearch, lines: number, scan: number): JournalSearchFilters {
  const range = rangeOf(search);
  const stretch = search.from !== undefined && search.to !== undefined;
  return {
    query: effectiveQuery(search),
    lines,
    scan,
    ...(search.unit === KERNEL ? { kernel: true } : {}),
    ...(stretch ? { since: `@${String(search.from)}`, until: `@${String(search.to)}` } : {}),
    ...(!stretch && JOURNAL_SINCE[range] !== undefined ? { since: JOURNAL_SINCE[range] } : {}),
    ...(!stretch && range === "boot" ? { boot: 0 } : {}),
    ...(!stretch && range === "previous" ? { boot: -1 } : {}),
  };
}

/**
 * The stretch the page is looking at, for "what happened here": the one it was given, or the hours
 * the range names ending now. A boot is not a stretch of time the page knows.
 *
 * @param now - The moment the range ends, Unix seconds.
 */
export function stretchOf(search: LogsSearch, now: number): [number, number] | null {
  if (search.from !== undefined && search.to !== undefined) return [search.from, search.to];
  const hours: Partial<Record<LogRange, number>> = { "15m": 0.25, "1h": 1, "6h": 6, "24h": 24, "7d": 168, live: 0.25 };
  const span = hours[rangeOf(search)];
  return span === undefined ? null : [now - span * 3600, now];
}

// -- The terminal's way of asking the same ---------------------------------------------------

const quote = (value: string): string => (/^[\w.@%+=:,/-]+$/.test(value) ? value : `'${value.replaceAll("'", `'\\''`)}'`);

/** The command that asks the same thing from a terminal. */
export function searchCommand(search: LogsSearch): string {
  if (search.source === "system") {
    const parts = ["noust server logs"];
    if (search.unit === KERNEL) parts.push("-k");
    const query = effectiveQuery(search);
    if (query !== "") parts.push(`-Q '${query.replaceAll("'", "'\\''")}'`);
    const range = rangeOf(search);
    if (search.from !== undefined && search.to !== undefined) parts.push(`--since @${String(search.from)} --until @${String(search.to)}`);
    else if (range === "boot") parts.push("-b 0");
    else if (range === "previous") parts.push("-b -1");
    else parts.push(`--since ${JOURNAL_SINCE[range] ?? "-1h"}`);
    return parts.join(" ");
  }
  const fleet = search.scope === "fleet";
  const parts = [fleet ? "noust fleet logs" : "noust log search"];
  if (search.q !== undefined) parts.push(quote(search.q));
  if (search.app !== undefined && !fleet) parts.push(`--app ${quote(search.app)}`);
  const range = rangeOf(search);
  parts.push(`--since ${range === "live" ? LIVE_WINDOW : range}`);
  if (search.level !== undefined) parts.push(`--level ${search.level}`);
  return parts.join(" ");
}

// -- Lines ----------------------------------------------------------------------------------

/** The journal's levels as the viewer draws them: errors in red, warnings in amber. */
export function viewerLevel(level: string | null | undefined): LogLine["level"] | undefined {
  switch (level) {
    case "emerg":
    case "alert":
    case "crit":
    case "err":
      return "error";
    case "warning":
      return "warn";
    case "notice":
    case "info":
      return "info";
    case "debug":
      return "debug";
    default:
      return undefined;
  }
}

/** Two digits. */
function two(value: number): string {
  return String(value).padStart(2, "0");
}

/** The moment to the second, in the browser's time: `2026-10-16 08:14:03`. */
export function stamp(ts: number): string {
  const moment = new Date(ts * 1000);
  return `${String(moment.getFullYear())}-${two(moment.getMonth() + 1)}-${two(moment.getDate())} ${two(moment.getHours())}:${two(moment.getMinutes())}:${two(moment.getSeconds())}`;
}

/** Whatever a fleet row holds, as the text or number it is, or nothing. */
function textOf(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

/**
 * The lines of a search over every server: each row is one line a server found, labelled with the
 * server, and the viewer reads them oldest first like a single server's. Notes are not lines.
 */
export function fleetViewerLines(rows: readonly Record<string, unknown>[]): LogLine[] {
  const lines = rows.filter((row) => row["type"] === "line" && typeof row["ts"] === "number");
  return lines
    .map((row) => ({
      ts: row["ts"] as number,
      node: textOf(row["node"]) ?? "",
      app: textOf(row["app"]) ?? "",
      source: textOf(row["source"]) ?? "journal",
      unit: textOf(row["unit"]) ?? "",
      level: textOf(row["level"]) ?? null,
      message: textOf(row["message"]) ?? "",
    }))
    .sort((a, b) => a.ts - b.ts)
    .map((line, id) => {
      const who = line.app !== "" ? (line.source === "container" ? `${line.app}/${line.unit}` : line.app) : line.unit;
      const level = viewerLevel(line.level);
      return { id, text: `${stamp(line.ts)} [${line.node}] ${who}: ${line.message}`, ...(level !== undefined ? { level } : {}) };
    });
}

/**
 * One line for the viewer: when, whose, then the message verbatim. A system unit has no
 * application, so its unit name stands in; a container's line says which service wrote it.
 */
export function viewerLines(lines: readonly LogSearchLine[]): LogLine[] {
  return lines.map((line, id) => {
    const who = line.app !== "" ? (line.source === "container" ? `${line.app}/${line.unit}` : line.app) : line.unit;
    const level = viewerLevel(line.level);
    return { id, text: `${stamp(line.ts)} ${who}: ${line.message}`, ...(level !== undefined ? { level } : {}) };
  });
}
