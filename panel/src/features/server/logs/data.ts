/**
 * How a journal entry is printed, and the address the system's logs had before they became a
 * source of Logs (`/server/logs?unit=nginx.service&priority=err&range=24h`), still read so that an
 * old link, a bookmark and the buttons of other pages that name it land on the same lines.
 */

import type { LogLine } from "../../../components/ui/LogViewer";
import { validateLogsSearch } from "../../logs/data";
import type { LogsSearch } from "../../logs/data";
import type { JournalContext, JournalSearch } from "../queries";

/** What the old address could carry. */
export interface LegacyJournalSearch {
  unit?: string;
  priority?: string;
  range?: string;
  /** Never part of the old address's own form, but written in its documentation as a journal time (`-1h`). */
  since?: string;
  q?: string;
  from?: number;
  to?: number;
}

/** Reads the old address's params without judging them: `toLogsSearch` does. */
export function validateLegacySearch(search: Record<string, unknown>): LegacyJournalSearch {
  const pick = (key: string): string | undefined => (typeof search[key] === "string" ? search[key] : undefined);
  const unit = pick("unit");
  const priority = pick("priority");
  const range = pick("range");
  const since = pick("since");
  const q = pick("q");
  const stretch = validateLogsSearch({ from: search["from"], to: search["to"] });
  return {
    ...(unit !== undefined ? { unit } : {}),
    ...(priority !== undefined ? { priority } : {}),
    ...(range !== undefined ? { range } : {}),
    ...(since !== undefined ? { since } : {}),
    ...(q !== undefined ? { q } : {}),
    ...(stretch.from !== undefined && stretch.to !== undefined ? { from: stretch.from, to: stretch.to } : {}),
  };
}

/** The journal times the old address documented, as the ranges of Logs. */
const JOURNAL_TIMES: Readonly<Record<string, string>> = { "-15min": "15m", "-1h": "1h", "-6h": "6h", "-24h": "24h", "-7d": "7d" };

/**
 * What Logs asks for the same lines: the system as the source, `range` as `since`, `priority` as
 * `level`; the rest keeps its name. Anything the old address said that Logs does not read is dropped
 * by the same validation the page itself uses.
 */
export function toLogsSearch(old: LegacyJournalSearch): LogsSearch {
  const since = old.range ?? (old.since !== undefined ? JOURNAL_TIMES[old.since] : undefined);
  return validateLogsSearch({
    source: "system",
    q: old.q,
    unit: old.unit,
    level: old.priority,
    since,
    from: old.from,
    to: old.to,
  });
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
