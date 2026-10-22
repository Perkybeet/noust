/**
 * What the two sources of Logs share: how a change to the question is written back to the URL,
 * what moving from one source to the other keeps, and the choices of the toolbar that both draw.
 */

import type { T } from "../../i18n";
import { APP_RANGES, RANGES, validateLogsSearch } from "./data";
import type { LogRange, LogsSearch } from "./data";
import type { ToolbarChoice } from "./LogToolbar";

/** A change to the question: a key set to undefined is cleared, and the default range clears `since`. */
export type Patch = { [K in Exclude<keyof LogsSearch, "since">]?: LogsSearch[K] | undefined } & { since?: LogRange | undefined };

/** The question with a change applied, and nothing in it that is empty or is the default. */
export function patched(search: LogsSearch, patch: Patch): LogsSearch {
  return validateLogsSearch({ ...search, ...patch });
}

export type SourceName = "apps" | "system";

export function sourceOf(search: LogsSearch): SourceName {
  return search.source === "system" ? "system" : "apps";
}

/**
 * The question after moving to the other source: the words and the range come with it when the
 * other source can read them; an application, a service, a stretch or a line do not, since each
 * names something that only one of them has.
 */
export function switchedTo(search: LogsSearch, source: SourceName): LogsSearch {
  const keep = { q: search.q, level: search.level, since: search.since };
  if (source === "system") return validateLogsSearch({ ...keep, source: "system" });
  return validateLogsSearch({ ...keep, since: search.since !== undefined && APP_RANGES.includes(search.since) ? search.since : undefined });
}

/** The ranges a source offers, "live" first: the system's include the two boots. */
export function rangeChoices(t: T, source: SourceName): ToolbarChoice<LogRange>[] {
  return RANGES.filter((range) => source === "system" || APP_RANGES.includes(range)).map((value) => ({ value, label: t(`logs.window.${value}`) }));
}

/** The switch between the applications and the system. */
export function sourceChoices(t: T): ToolbarChoice<SourceName>[] {
  return [
    { value: "apps", label: t("logs.source.apps") },
    { value: "system", label: t("logs.source.system") },
  ];
}
