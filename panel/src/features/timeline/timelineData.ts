import type { LogLine } from "../../components/ui/LogViewer";
import type { Locale } from "../../i18n";
import { formatClockSeconds } from "../../lib/format";
import { timelineLevel } from "../../api/queries/timeline";
import type { TimelineEvent, TimelineLevel, TimelineMinute, TimelineSource } from "../../api/queries/timeline";

/** "Every level" or the least serious level still shown. */
export type LevelFilter = "all" | "error" | "warning" | "notice";

export interface EventFilters {
  source: "all" | TimelineSource;
  level: LevelFilter;
  unit: string;
}

export const ALL = "all";

export const NO_FILTERS: EventFilters = { source: ALL, level: ALL, unit: ALL };

const LEVEL_RANK: Readonly<Record<TimelineLevel, number>> = { error: 0, warning: 1, notice: 2, info: 3 };

/** The events the filters keep, in the order they came (oldest first). */
export function filterEvents(events: readonly TimelineEvent[], filters: EventFilters): TimelineEvent[] {
  return events.filter(
    (event) =>
      (filters.source === ALL || event.source === filters.source) &&
      (filters.level === ALL || LEVEL_RANK[timelineLevel(event.level)] <= LEVEL_RANK[filters.level]) &&
      (filters.unit === ALL || event.unit === filters.unit),
  );
}

/** Every unit an event names, sorted: what the service filter offers. */
export function unitsOf(events: readonly TimelineEvent[]): string[] {
  return [...new Set(events.map((event) => event.unit).filter((unit): unit is string => typeof unit === "string" && unit !== ""))].sort();
}

const LOG_LEVEL: Readonly<Record<TimelineLevel, NonNullable<LogLine["level"]>>> = {
  error: "error",
  warning: "warn",
  notice: "info",
  info: "info",
};

function detail(event: TimelineEvent, key: string): string | null {
  const value = event.details?.[key];
  return typeof value === "string" || typeof value === "number" ? String(value) : null;
}

/**
 * Who or what an event is about, as the source names it: the unit, the person, the process.
 * Raw values, never translated (a unit name, an account), like the rest of the line.
 */
function subject(event: TimelineEvent): string {
  if (event.unit) return event.unit;
  if (event.kind === "observation") {
    const process = detail(event, "process");
    const pid = detail(event, "pid");
    return process !== null ? `${process}${pid !== null ? `[${pid}]` : ""}` : "-";
  }
  if (event.kind === "deployment") return event.app ?? "-";
  return event.actor ?? event.app ?? "-";
}

/** What the source said, with the fields a line of its kind carries: verbatim values only. */
function words(event: TimelineEvent): string {
  switch (event.kind) {
    case "audit": {
      const target = detail(event, "target");
      return [event.text, event.status, target].filter(Boolean).join("  ");
    }
    case "deployment":
      return [`#${event.ref ?? "?"}`, event.status, event.text, detail(event, "error")].filter(Boolean).join("  ");
    case "job":
      return [detail(event, "type"), event.status, event.text, detail(event, "error")].filter(Boolean).join("  ");
    case "unit_failed":
    case "unit_recovered":
      return [event.kind, event.text].filter(Boolean).join("  ");
    case "boot":
      return `boot  ${event.text}`;
    default:
      return event.text;
  }
}

/**
 * The events as lines of a log: the moment, then the source, who or what it is about and what
 * it said. Every part is the system's own value, so a line is read like any other log.
 */
export function logLines(events: readonly TimelineEvent[], locale: Locale): LogLine[] {
  return events.map((event, index) => ({
    id: index,
    ts: formatClockSeconds(new Date(event.at * 1000), locale),
    level: LOG_LEVEL[timelineLevel(event.level)],
    text: `${event.source.padEnd(11)} ${subject(event)}  ${words(event)}`,
  }));
}

/** The deployments and jobs, which have a page to open, oldest first. */
export function changesOf(events: readonly TimelineEvent[]): TimelineEvent[] {
  return events.filter((event) => event.kind === "deployment" || event.kind === "job");
}

/** How busy a minute was: the CPU of its busiest process. */
export function minuteLoad(minute: TimelineMinute): number {
  return minute.cpu.reduce((peak, row) => Math.max(peak, row.cpu_percent), 0);
}

/** The minute to open first: the busiest, the earliest of equals; null when there is none. */
export function busiestMinute(minutes: readonly TimelineMinute[]): TimelineMinute | null {
  let best: TimelineMinute | null = null;
  for (const minute of minutes) {
    if (best === null || minuteLoad(minute) > minuteLoad(best)) best = minute;
  }
  return best;
}
