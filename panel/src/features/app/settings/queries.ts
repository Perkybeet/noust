import { queryOptions } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";

import { request } from "../../../api/client";
import type { ResponseOf } from "../../../api/client";
import { isJobFinished, jobQuery } from "../../../api/queries/jobs";
import type { Job } from "../../../api/queries/jobs";

export type WebhookStatus = ResponseOf<"/api/apps/{domain}/webhook", "get">;
export type WebhookReceived = ResponseOf<"/api/apps/{domain}/webhook/received", "get">;
export type ReceivedDelivery = WebhookReceived["items"][number];
export type WebhookSecret = ResponseOf<"/api/apps/{domain}/webhook-secret", "post">;
export type Sandbox = ResponseOf<"/api/apps/{domain}/sandbox", "get">;

/** How many deliveries "Pushes received" lists: the newest, enough to see a pattern. */
export const RECEIVED_SHOWN = 8;

/**
 * Under the app's own prefix (`["app", domain, ...]`), like everything read about one app, so
 * a job on the app finishing refreshes these with the rest.
 */
export const settingsKeys = {
  webhook: (domain: string) => ["app", domain, "webhook"] as const,
  received: (domain: string) => ["app", domain, "webhook-received"] as const,
  sandbox: (domain: string) => ["app", domain, "sandbox"] as const,
};

/** Read again every few seconds while the setup waits for the forge's first delivery. */
const WAITING_POLL_MS = 5_000;

/**
 * Everything the guided webhook setup shows: the public hooks address, whether a secret
 * exists (never the secret), the branch that deploys, the forge, the GitHub App and what the
 * forge has been sending. Asked again while it waits for the first delivery, so "Connected"
 * appears on its own once the forge's ping lands.
 */
export const webhookStatusQuery = (domain: string) =>
  queryOptions({
    queryKey: settingsKeys.webhook(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/webhook", { params: { domain }, signal }),
    refetchInterval: (query) => (query.state.data?.state === "waiting" ? WAITING_POLL_MS : false),
  });

/** Every delivery the forge sent, whatever became of it: pushes, pings, refusals. */
export const webhookReceivedQuery = (domain: string, waiting: boolean) =>
  queryOptions({
    queryKey: settingsKeys.received(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/webhook/received", { params: { domain }, query: { limit: RECEIVED_SHOWN }, signal }),
    refetchInterval: waiting ? WAITING_POLL_MS : false,
  });

/** How the app builds: in the sandbox or as root, and its last trial build. Changes nothing. */
export const sandboxQuery = (domain: string) =>
  queryOptions({
    queryKey: settingsKeys.sandbox(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/sandbox", { params: { domain }, signal }),
  });

/** How often a job a save queued is read again while the save waits for it. */
const JOB_WAIT_MS = 750;

/**
 * Waits for a job a save queued to end, reading it again every moment: a subsection's save
 * is done when what it asked for is done, not when it was queued.
 *
 * @returns The job as it ended.
 */
export async function waitForJob(queryClient: QueryClient, queued: Job): Promise<Job> {
  let job = queued;
  while (!isJobFinished(job)) {
    await new Promise((resolve) => setTimeout(resolve, JOB_WAIT_MS));
    job = await queryClient.query({ ...jobQuery(job.id), staleTime: 0 });
  }
  return job;
}

/** A job that ended without completing, as an error a save can show: its own words. */
export class JobFailedError extends Error {
  readonly detail: string;

  constructor(detail: string) {
    super(detail);
    this.name = "JobFailedError";
    this.detail = detail;
  }
}
