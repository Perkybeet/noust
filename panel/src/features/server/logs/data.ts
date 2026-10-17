/**
 * The Logs tab's filters, as they live in the URL, the query they become, and how a journal entry
 * is printed. The unit and level pickers are shortcuts for the fields of the query language
 * (`@unit:`, `@level:`): the server judges one query, written back to the operator as it was
 * understood, so the pickers and the search box can never disagree about what is being asked.
 */

import type { LogLine } from "../../../components/ui/LogViewer";
import type { JournalContext, JournalSearch, JournalSearchFilters } from "../queries";

export const RANGES = ["1h", "24h", "7d", "boot", "previous"] as const;
export type LogRange = (typeof RANGES)[number];

export const PRIORITIES = ["err", "warning", "notice", "info"] as const;
export type LogPriority = (typeof PRIORITIES)[number];

/** The pseudo unit that reads the kernel's messages. */
export const KERNEL = "kernel";

/** The most entries examined by a search at first, and by "look further". */
export const SCAN = 5_000;
export const SCAN_FURTHER = 20_000;

export interface LogsSearch {
  unit?: string;
  priority?: LogPriority;
  range?: Exclude<LogRange, "1h">;
  /** The query: words, `@unit:`, `@app:`, `@level:`, `-`, `AND`, `OR`, quotes, groups. */
  q?: string;
  /** A stretch picked elsewhere (a chart, the timeline), Unix seconds: it replaces the range. */
  from?: number;
  to?: number;
}

function text(value: unknown, max = 500): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed.slice(0, max);
}

function seconds(value: unknown): number | undefined {
  const parsed = typeof value === "number" ? value : typeof value === "string" && /^\d{1,11}$/.test(value) ? Number(value) : Number.NaN;
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
}

/** Reads the search params, dropping anything malformed instead of failing. */
export function validateLogsSearch(search: Record<string, unknown>): LogsSearch {
  const unit = text(search["unit"], 200);
  const q = text(search["q"]);
  const priority = search["priority"];
  const range = search["range"];
  const from = seconds(search["from"]);
  const to = seconds(search["to"]);
  // A stretch is both ends or neither, and it runs forward.
  const stretch = from !== undefined && to !== undefined && to > from;
  return {
    ...(unit !== undefined ? { unit } : {}),
    ...(typeof priority === "string" && (PRIORITIES as readonly string[]).includes(priority) ? { priority: priority as LogPriority } : {}),
    ...(typeof range === "string" && range !== "1h" && (RANGES as readonly string[]).includes(range) ? { range: range as Exclude<LogRange, "1h"> } : {}),
    ...(q !== undefined ? { q } : {}),
    ...(stretch ? { from, to } : {}),
  };
}

/** A unit name or a query word, as the language writes it: quoted when it has a space or a quote. */
function fieldValue(value: string): string {
  return /[\s"()]/.test(value) ? `"${value.replaceAll('"', "")}"` : value;
}

/**
 * The query the page asks: the pickers as fields, then what was typed. The kernel is not a unit
 * but a source of its own (`--dmesg`), asked for by a parameter.
 */
export function effectiveQuery(search: LogsSearch): string {
  return [
    search.unit !== undefined && search.unit !== KERNEL ? `@unit:${fieldValue(search.unit)}` : "",
    search.priority !== undefined ? `@level:${search.priority}` : "",
    search.q ?? "",
  ]
    .filter((part) => part !== "")
    .join(" ");
}

/** The API's parameters for what the URL asks: the default range is the last hour. */
export function searchFilters(search: LogsSearch, lines: number, scan: number): JournalSearchFilters {
  const range: LogRange = search.range ?? "1h";
  const stretch = search.from !== undefined && search.to !== undefined;
  return {
    query: effectiveQuery(search),
    lines,
    scan,
    ...(search.unit === KERNEL ? { kernel: true } : {}),
    ...(stretch ? { since: `@${String(search.from)}`, until: `@${String(search.to)}` } : {}),
    ...(!stretch && range === "1h" ? { since: "-1h" } : {}),
    ...(!stretch && range === "24h" ? { since: "-24h" } : {}),
    ...(!stretch && range === "7d" ? { since: "-7d" } : {}),
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
  const hours: Record<string, number | undefined> = { "1h": 1, "24h": 24, "7d": 168 };
  const span = hours[search.range ?? "1h"];
  return span === undefined ? null : [now - span * 3600, now];
}

/** The command that reads the same thing from a terminal. */
export function logsCommand(search: LogsSearch): string {
  const parts = ["noust server logs"];
  if (search.unit === KERNEL) parts.push("-k");
  const query = effectiveQuery(search);
  if (query !== "") parts.push(`-Q '${query.replaceAll("'", "'\\''")}'`);
  if (search.from !== undefined && search.to !== undefined) parts.push(`--since @${String(search.from)} --until @${String(search.to)}`);
  else if ((search.range ?? "1h") === "1h") parts.push("--since -1h");
  else if (search.range === "24h") parts.push("--since -24h");
  else if (search.range === "7d") parts.push("--since -7d");
  else if (search.range === "boot") parts.push("-b 0");
  else if (search.range === "previous") parts.push("-b -1");
  return parts.join(" ");
}

type Entry = JournalSearch["entries"][number];

/** A priority as the viewer's levels: failures and worse are errors, warnings are warnings. */
function levelOf(priority: number): LogLine["level"] | undefined {
  if (priority <= 3) return "error";
  if (priority === 4) return "warn";
  return priority >= 7 ? "debug" : undefined;
}

/** journalctl's short form: the moment, the unit and its process, then the message verbatim. */
export function journalLines(journal: { entries: readonly Entry[] } | JournalContext): LogLine[] {
  return journal.entries.map((entry, id) => {
    const level = levelOf(entry.priority);
    return {
      id,
      // To the second, as journalctl -o short-iso prints it: microseconds are noise to a reader.
      text: `${entry.timestamp.replace(/\.\d+/, "")} ${entry.unit}${entry.pid != null ? `[${String(entry.pid)}]` : ""}: ${entry.message}`,
      ref: entry.cursor,
      ...(level !== undefined ? { level } : {}),
    };
  });
}
