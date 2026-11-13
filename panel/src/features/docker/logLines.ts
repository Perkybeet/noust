/**
 * A container's output as the viewer draws it. Docker prints each line after the time it was written (`--timestamps`): the time
 * goes to the viewer's time column and the rest stays verbatim, whatever the program said. The level is read from the words, as
 * for every other log of the console.
 */

import type { LogLine } from "../../components/ui/LogViewer";
import { levelOf } from "../app/deployments/buildLog";

/** `2026-11-12T10:15:02.123456789Z message`: Docker's time, then the line itself. */
const STAMPED = /^\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})? (.*)$/s;

// A line keeps its identity once read, so the viewer's per-line cache keeps working.
const read = new WeakMap<LogLine, LogLine>();

/** The line with its time moved to the time column and its level read from its words. */
export function dockerLine(line: LogLine): LogLine {
  let found = read.get(line);
  if (found === undefined) {
    const match = STAMPED.exec(line.text);
    const text = match?.[2] ?? line.text;
    const level = line.level ?? levelOf(text);
    found = match === null && level === line.level ? line : { ...line, text, ...(match ? { ts: match[1] ?? "" } : {}), ...(level === undefined ? {} : { level }) };
    read.set(line, found);
  }
  return found;
}
