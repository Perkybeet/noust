/**
 * What the deploy queue says about a job, in the console's words: what it is, where it stands,
 * and whether it can be stopped and, when it cannot, why. Pure, with a trailing `locale` like
 * the other helpers, so a test reads English and a component passes `t.locale`. What the queue
 * itself says (`waiting_for`) and what a refused cancel answers are the system's words and are
 * never rewritten here.
 */

import type { AppList } from "../../api/queries/apps";
import type { QueueEntry } from "../../api/queries/queue";
import { getLocale } from "../../app/locale";
import type { Locale } from "../../app/locale";
import { translate } from "../../i18n";
import type { PlainKey } from "../../i18n";
import { jobWords } from "../app/useAppJob";

/** The job types that build a release and can be stopped while they do (`BUILD_JOB_TYPES`). */
const BUILD_TYPES: ReadonlySet<string> = new Set(["deploy", "update"]);

/** Application types that build in place or inside Docker: nothing there can be stopped halfway. */
const IN_PLACE_TYPES: ReadonlySet<string> = new Set(["docker-compose", "monorepo"]);

/** The application a job acts on, when it acts on one. */
export function entryDomain(entry: Pick<QueueEntry, "metadata">): string | null {
  const domain = entry.metadata?.["domain"];
  return typeof domain === "string" && domain !== "" ? domain : null;
}

/**
 * What the job is, as a sentence: "Deploying shop.example.com" for the types the console knows,
 * the name the server gave it for the rest (a job that acts on no application).
 */
export function entryTitle(entry: Pick<QueueEntry, "type" | "name" | "metadata">, locale: Locale = getLocale()): string {
  const domain = entryDomain(entry);
  return domain === null ? entry.name : jobWords(entry.type, domain, locale).title;
}

/** How many later requests for the same work were folded into this one while it waited. */
export function mergedRequests(entry: Pick<QueueEntry, "metadata">): number {
  const merged = entry.metadata?.["merged_requests"];
  return typeof merged === "number" && Number.isFinite(merged) && merged > 0 ? Math.floor(merged) : 0;
}

/** Whether the job is one that waits its turn: queued, not yet started. */
export function isWaiting(entry: Pick<QueueEntry, "status">): boolean {
  return entry.status === "pending";
}

/**
 * Where a waiting job stands in the line: whether it is the next to start, and its place.
 * Null for a job that runs, or that the queue gave no place.
 */
export function placeOf(entry: Pick<QueueEntry, "status" | "queue_position">): { next: boolean; position: number } | null {
  if (!isWaiting(entry)) return null;
  const position = entry.queue_position ?? null;
  if (position === null) return null;
  return { next: position <= 1, position };
}

/** Why a job that runs cannot be stopped, or null when it is not a reason to say anything. */
export type CancelReason = "activating" | "buildsInPlace" | "unsupported";

/** What cancelling a job comes to right now. */
export type CancelState =
  | { kind: "can"; stage: "queued" | "running" }
  | { kind: "stopping" }
  | { kind: "cannot"; reason: CancelReason }
  /** The job's own event does not say whether it can be stopped; the queue's read does. */
  | { kind: "unknown" };

/**
 * What the Cancel button of a job does: it stops a waiting job at once, and a build that has
 * not started to activate its release; a build asked to stop is winding down; and anything else
 * says why not. The server decides (`cancellable`); the console only words the reason, from the
 * kind of job and the application it builds.
 *
 * @param entry The job as the queue lists it. A job as its `job` event carries it has no
 *   `cancellable`: a running one is then `unknown` until the queue answers, never "cannot".
 * @param apps The applications, to tell a build in place from one beside what serves; absent
 *   while they are read, which words the reason as "being activated" for a release build.
 */
export function cancelState(
  entry: Pick<QueueEntry, "status" | "type" | "cancel_requested" | "metadata"> & { cancellable?: boolean | undefined },
  apps: AppList["apps"] | undefined,
): CancelState {
  if (entry.cancel_requested) return { kind: "stopping" };
  // A job that waits has done nothing yet: taking it out of the line is always accepted.
  if (isWaiting(entry) && entry.cancellable !== false) return { kind: "can", stage: "queued" };
  if (typeof entry.cancellable !== "boolean") return { kind: "unknown" };
  if (entry.cancellable) return { kind: "can", stage: isWaiting(entry) ? "queued" : "running" };
  if (!BUILD_TYPES.has(entry.type)) return { kind: "cannot", reason: "unsupported" };
  const domain = entryDomain(entry);
  const app = domain === null ? undefined : apps?.find((candidate) => candidate.domain === domain);
  if (app !== undefined && (app.layout !== "releases" || IN_PLACE_TYPES.has(app.app_type ?? ""))) {
    return { kind: "cannot", reason: "buildsInPlace" };
  }
  return { kind: "cannot", reason: "activating" };
}

const REASONS: Readonly<Record<CancelReason, PlainKey>> = {
  activating: "queue.cancel.reasons.activating",
  buildsInPlace: "queue.cancel.reasons.buildsInPlace",
  unsupported: "queue.cancel.reasons.unsupported",
};

/** The sentence that says why a job cannot be stopped. */
export function cancelReasonWords(reason: CancelReason, locale: Locale = getLocale()): string {
  return translate(locale, REASONS[reason]);
}
