/**
 * A job's captured log as the lines a command printed.
 *
 * The job manager writes every line as `[2026-10-06 09:12:03] [INFO] text`: the stamp and the
 * level are the queue's, the text is the command's. A one-off command's output is shown without
 * the queue's decoration, so it reads as the program printed it, and a line the queue logged at
 * a level that means trouble keeps that level in the viewer.
 */

import type { LogLine } from "../../components/ui/LogViewer";

const QUEUE_PREFIX = /^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \[([A-Z]+)\] ?/;

function levelOf(word: string): LogLine["level"] | undefined {
  switch (word) {
    case "ERROR":
    case "CRITICAL":
      return "error";
    case "WARNING":
    case "WARN":
      return "warn";
    case "DEBUG":
      return "debug";
    default:
      return undefined;
  }
}

/** The lines of a job's log file, each without the queue's stamp and level. */
export function outputLines(content: string): LogLine[] {
  if (content === "") return [];
  const body = content.endsWith("\n") ? content.slice(0, -1) : content;
  return body.split("\n").map((raw, id) => {
    const prefix = QUEUE_PREFIX.exec(raw);
    if (prefix === null) return { id, text: raw };
    const level = levelOf(prefix[1] ?? "");
    return { id, text: raw.slice(prefix[0].length), ...(level !== undefined ? { level } : {}) };
  });
}
