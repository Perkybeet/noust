import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

/** What builds, what waits and the limits that decide it: GET /api/jobs/queue. */
export type Queue = ResponseOf<"/api/jobs/queue", "get">;
export type QueueEntry = Queue["running"][number];
export type CancelOutcome = ResponseOf<"/api/jobs/{job_id}/cancel", "post">;

/**
 * Under `["jobs"]`, the prefix every job list is read under, so a job changing state (one
 * started, one finished, one cancelled) refreshes the queue with the lists. A job whose place
 * in the line moved without changing state is the `job` event's own case (`applyServerEvent`).
 */
export const queueKeys = {
  all: ["jobs", "queue"] as const,
};

/** How often the queue is read again while anything is in it, whatever the events say. */
export const QUEUE_POLL_MS = 3_000;

/** Whether anything builds or waits: the one case in which the queue changes by itself. */
export function queueBusy(queue: Pick<Queue, "running" | "queued"> | undefined): boolean {
  return queue !== undefined && queue.running.length + queue.queued.length > 0;
}

/**
 * The running jobs, the waiting ones in the order they start, and the server's limits. The
 * `job` events are the fast path; reading it again every few seconds while anything is in it
 * is the guarantee, for what an event does not say: whether a build can still be cancelled
 * flips when its release starts to be activated.
 */
export const queueQuery = () =>
  queryOptions({
    queryKey: queueKeys.all,
    queryFn: ({ signal }) => request("get", "/api/jobs/queue", { signal }),
    refetchInterval: (query) => (queueBusy(query.state.data) ? QUEUE_POLL_MS : false),
  });
