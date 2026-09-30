/**
 * The Logs tab's filters, as they live in the URL, and how a journal entry is printed.
 */

import type { LogLine } from "../../../components/ui/LogViewer";
import type { Journal, JournalFilters } from "../queries";

export const RANGES = ["1h", "24h", "7d", "boot", "previous"] as const;
export type LogRange = (typeof RANGES)[number];

export const PRIORITIES = ["err", "warning", "notice", "info"] as const;
export type LogPriority = (typeof PRIORITIES)[number];

/** The pseudo unit that reads the kernel's messages. */
export const KERNEL = "kernel";

export interface LogsSearch {
  unit?: string;
  priority?: LogPriority;
  range?: Exclude<LogRange, "1h">;
  q?: string;
}

function text(value: unknown, max = 200): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed.slice(0, max);
}

/** Reads the search params, dropping anything malformed instead of failing. */
export function validateLogsSearch(search: Record<string, unknown>): LogsSearch {
  const unit = text(search["unit"]);
  const q = text(search["q"]);
  const priority = search["priority"];
  const range = search["range"];
  return {
    ...(unit !== undefined ? { unit } : {}),
    ...(typeof priority === "string" && (PRIORITIES as readonly string[]).includes(priority) ? { priority: priority as LogPriority } : {}),
    ...(typeof range === "string" && range !== "1h" && (RANGES as readonly string[]).includes(range) ? { range: range as Exclude<LogRange, "1h"> } : {}),
    ...(q !== undefined ? { q } : {}),
  };
}

/** The API's filters for what the URL asks: the default range is the last hour. */
export function journalFilters(search: LogsSearch, lines: number): JournalFilters {
  const range: LogRange = search.range ?? "1h";
  return {
    lines,
    ...(search.unit !== undefined && search.unit !== KERNEL ? { unit: search.unit } : {}),
    ...(search.unit === KERNEL ? { kernel: true } : {}),
    ...(search.priority !== undefined ? { priority: search.priority } : {}),
    ...(search.q !== undefined ? { q: search.q } : {}),
    ...(range === "1h" ? { since: "-1h" } : range === "24h" ? { since: "-24h" } : range === "7d" ? { since: "-7d" } : {}),
    ...(range === "boot" ? { boot: 0 } : range === "previous" ? { boot: -1 } : {}),
  };
}

/** The command that reads the same thing from a terminal. */
export function logsCommand(search: LogsSearch): string {
  const parts = ["noust server logs"];
  if (search.unit !== undefined && search.unit !== KERNEL) parts.push(search.unit);
  if (search.unit === KERNEL) parts.push("-k");
  if (search.priority !== undefined) parts.push(`-p ${search.priority}`);
  const range: LogRange = search.range ?? "1h";
  if (range === "1h") parts.push("--since -1h");
  if (range === "24h") parts.push("--since -24h");
  if (range === "7d") parts.push("--since -7d");
  if (range === "boot") parts.push("-b 0");
  if (range === "previous") parts.push("-b -1");
  if (search.q !== undefined) parts.push(`-g '${search.q.replaceAll("'", "'\\''")}'`);
  return parts.join(" ");
}

/** journalctl's short form: the moment, the unit and its process, then the message verbatim. */
export function journalLines(journal: Journal): LogLine[] {
  return journal.entries.map((entry, id) => ({
    id,
    // To the second, as journalctl -o short-iso prints it: microseconds are noise to a reader.
    text: `${entry.timestamp.replace(/\.\d+/, "")} ${entry.unit}${entry.pid != null ? `[${String(entry.pid)}]` : ""}: ${entry.message}`,
  }));
}
