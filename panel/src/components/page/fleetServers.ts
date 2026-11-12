import { useMemo } from "react";

import { useT } from "../../i18n";
import type { Locale } from "../../i18n";
import { formatDuration, parseTimestamp, relativeRefreshMs } from "../../lib/format";
import type { Status } from "../ui/StatusPill";
import { useNow } from "./clock";

/**
 * What the pieces for "All servers" know of a server, and nothing of how the console got it: the pages turn what
 * the central answered (`NodeOutcome`) into these, so the kit is the same for a list, a table of servers and a
 * question whichever view feeds it.
 *
 * A server that did not answer fresh and whole is told in one of three ways, because each asks the person for
 * something different: `stale` has rows and they are the last it gave (wait, or look at the server), `unreachable`
 * has none, and `unsupported` is a Noust that does not know the view (update it; it is not a fault). A refusal or an
 * error is none of these: it keeps its own notice with the system's words verbatim (docs/DESIGN.md 6.5).
 */
export type ServerAnswer =
  | { kind: "fresh" }
  /** `since` is when it last answered; without it the age is not said. */
  | { kind: "stale"; since?: string | number | Date | null }
  | { kind: "unreachable" }
  /** `version` is the Noust it runs, when the central knows it. */
  | { kind: "unsupported"; version?: string | null };

export interface FleetServer {
  /** Its name on this central, in mono wherever it is said. */
  name: string;
  /** The central is one more server of the list, marked "central". */
  central?: boolean;
  /** How it answered what the page shows; fresh when absent. */
  answer?: ServerAnswer;
}

/**
 * Where a server's page opens, as the router writes it: `{ to: "/network", search: { node: "web-01" } }`. The page that
 * draws the piece says where "Open on web-01" goes and what the selector becomes there; the kit never decides it.
 */
export interface ServerTarget {
  to: string;
  search?: Record<string, string | undefined>;
}

/** The way a server answered, with the fresh answer as the default. */
export function answerOf(server: FleetServer): ServerAnswer {
  return server.answer ?? { kind: "fresh" };
}

/** Whether the rows the page shows of this server are fresh and whole. */
export function isFresh(server: FleetServer): boolean {
  return answerOf(server).kind === "fresh";
}

/** The state each way of not answering is told with (colour, shape and word; docs/DESIGN.md 6.1). */
export const ANSWER_STATE: Readonly<Record<Exclude<ServerAnswer["kind"], "fresh">, Status>> = {
  stale: "warning",
  unreachable: "failed",
  // An older Noust is not a fault: grey, with the question mark of what it cannot say.
  unsupported: "unknown",
};

/**
 * How long a server has been silent at `now`, as the console says every span ("2h 5m", "2 h 5 min"). Null when there
 * is no moment to count from (nothing was given, or it cannot be read).
 */
export function silenceAt(since: string | number | Date | null | undefined, now: number, locale: Locale): string | null {
  const date = parseTimestamp(since);
  return date === null ? null : formatDuration(Math.max(0, (now - date.getTime()) / 1000), locale);
}

/** `silenceAt`, kept current while it is on screen. */
export function useSilence(since: string | number | Date | null | undefined): string | null {
  const t = useT();
  const date = useMemo(() => parseTimestamp(since), [since]);
  const now = useNow((current) => (date === null ? 3_600_000 : relativeRefreshMs(date, new Date(current))));
  return silenceAt(date, now, t.locale);
}
