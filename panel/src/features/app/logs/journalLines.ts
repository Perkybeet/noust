import type { LogLine } from "../../../components/ui/LogViewer";
import { levelOf } from "../deployments/buildLog";

/**
 * A journal line in `short-iso` form: "2026-09-25T21:45:52+0100 web-01 shop[41234]: message".
 * The stamp and the host are systemd's, not the app's: the time goes to the time column, the
 * host (always this machine) is dropped, and the rest stays verbatim from the unit's name on.
 */
const JOURNAL = /^\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})(?:[+-]\d{2}:?\d{2}|Z)? \S+ (.*)$/;

export function journalLine(line: LogLine): LogLine {
  const match = JOURNAL.exec(line.text);
  const text = match?.[2] ?? line.text;
  const level = line.level ?? levelOf(text);
  if (match === null && level === line.level) return line;
  return { ...line, text, ...(match ? { ts: match[1] ?? "" } : {}), ...(level === undefined ? {} : { level }) };
}

// A line keeps its identity once read, so the viewer's per-line cache keeps working.
const read = new WeakMap<LogLine, LogLine>();

/** The line with its time moved to the time column and its level read from its words. */
export function withLevel(line: LogLine): LogLine {
  let found = read.get(line);
  if (found === undefined) {
    found = journalLine(line);
    read.set(line, found);
  }
  return found;
}
