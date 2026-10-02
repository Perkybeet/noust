import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

/** Everything that happened in a stretch, as `GET /api/timeline` answers it. */
export type Timeline = ResponseOf<"/api/timeline", "get">;
/** One thing that happened, from any source; `text` is the source's own words, verbatim. */
export type TimelineEvent = Timeline["events"][number];
/** What became of one source: shown, withheld (with its permission) or failed (with its words). */
export type TimelineSourceStatus = Timeline["sources"][number];
export type TimelineMinute = NonNullable<Timeline["processes"]>["minutes"][number];
/** One process in one minute's ranking. `command` is null when the caller may not read it. */
export type TimelineProcess = TimelineMinute["cpu"][number];

/*
 * The schema types `source` and `level` as strings. The console names the ones it knows, in
 * this order; anything else a newer server sends is shown as it came, never dropped.
 */

/** Where an event comes from; also the order the console lists the sources in. */
export const TIMELINE_SOURCES = ["journal", "audit", "deployments", "jobs", "monitor", "processes"] as const;
export type TimelineSource = (typeof TIMELINE_SOURCES)[number];

export function isTimelineSource(value: string): value is TimelineSource {
  return (TIMELINE_SOURCES as readonly string[]).includes(value);
}

const TIMELINE_LEVELS = ["error", "warning", "notice", "info"] as const;
export type TimelineLevel = (typeof TIMELINE_LEVELS)[number];

/** An event's level as the console ranks it; one it does not know ranks as information. */
export function timelineLevel(value: string): TimelineLevel {
  return (TIMELINE_LEVELS as readonly string[]).includes(value) ? (value as TimelineLevel) : "info";
}

/** The stretch asked for, in Unix seconds, and the application it is narrowed to. */
export interface TimelineSpan {
  start: number;
  end: number;
  app?: string | undefined;
}

export const timelineKeys = {
  all: ["timeline"] as const,
  span: (span: TimelineSpan) => ["timeline", span.start, span.end, span.app ?? null] as const,
};

/** Everything that happened in a stretch, merged and ordered; each source says what became of it. */
export const timelineQuery = (span: TimelineSpan) =>
  queryOptions({
    queryKey: timelineKeys.span(span),
    queryFn: ({ signal }) =>
      request("get", "/api/timeline", {
        query: { start: Math.floor(span.start), end: Math.ceil(span.end), ...(span.app !== undefined ? { app: span.app } : {}) },
        signal,
      }),
    // A stretch in the past does not change; one that reaches now is read again when reopened.
    staleTime: 30_000,
  });
