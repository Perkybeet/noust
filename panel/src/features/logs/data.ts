/**
 * The log search's question as it lives in the URL, and how a line is drawn.
 *
 * The server owns the query language (noust.managers.log_search): the console sends the words as
 * typed and shows the server's refusal as it wrote it.
 */

import type { LogSearchLine, LogSearchParams } from "../../api/queries/logSearch";
import type { LogLine } from "../../components/ui/LogViewer";

/** How far back a search looks: the server reads `15m`, `2h`, `7d`. */
export const WINDOWS = ["15m", "1h", "6h", "24h", "7d"] as const;
export type LogWindow = (typeof WINDOWS)[number];
export const DEFAULT_WINDOW = "1h" satisfies LogWindow;

/** The worst level kept; nothing means every level. */
export const LEVELS = ["err", "warning", "info"] as const;
export type LogLevel = (typeof LEVELS)[number];

/** Most lines one search returns; the server's own ceiling is 1000. */
export const LINES = 300;

/** `fleet` searches every server a central manages instead of the one in view. */
export type LogsScope = "fleet";

export interface LogsSearch {
  q?: string;
  app?: string;
  scope?: LogsScope;
  since?: Exclude<LogWindow, "1h">;
  level?: LogLevel;
}

function text(value: unknown, max: number): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed.slice(0, max);
}

/** Reads the search params, dropping anything malformed instead of failing. */
export function validateLogsSearch(search: Record<string, unknown>): LogsSearch {
  const q = text(search["q"], 1000);
  const app = text(search["app"], 253);
  const since = search["since"];
  const level = search["level"];
  return {
    ...(q !== undefined ? { q } : {}),
    ...(app !== undefined ? { app } : {}),
    ...(search["scope"] === "fleet" ? { scope: "fleet" as const } : {}),
    ...(typeof since === "string" && since !== DEFAULT_WINDOW && (WINDOWS as readonly string[]).includes(since) ? { since: since as Exclude<LogWindow, "1h"> } : {}),
    ...(typeof level === "string" && (LEVELS as readonly string[]).includes(level) ? { level: level as LogLevel } : {}),
  };
}

/** The API's question for what the URL asks. */
export function paramsOf(search: LogsSearch): LogSearchParams {
  return {
    q: search.q ?? "",
    apps: search.app === undefined ? [] : [search.app],
    since: search.since ?? DEFAULT_WINDOW,
    level: search.level,
    limit: LINES,
  };
}

/** The command that asks the same thing from a terminal. */
export function searchCommand(search: LogsSearch): string {
  const quote = (value: string): string => (/^[\w.@%+=:,/-]+$/.test(value) ? value : `'${value.replaceAll("'", `'\\''`)}'`);
  const fleet = search.scope === "fleet";
  const parts = [fleet ? "noust fleet logs" : "noust log search"];
  if (search.q !== undefined) parts.push(quote(search.q));
  if (search.app !== undefined && !fleet) parts.push(`--app ${quote(search.app)}`);
  parts.push(`--since ${search.since ?? DEFAULT_WINDOW}`);
  if (search.level !== undefined) parts.push(`--level ${search.level}`);
  return parts.join(" ");
}

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
