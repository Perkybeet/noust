import { queryOptions } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";

import { isApiError, request } from "../../../api/client";
import type { ResponseOf } from "../../../api/client";
import { isJobFinished, jobQuery } from "../../../api/queries/jobs";
import type { Job } from "../../../api/queries/jobs";

export type WebhookStatus = ResponseOf<"/api/apps/{domain}/webhook", "get">;
export type WebhookReceived = ResponseOf<"/api/apps/{domain}/webhook/received", "get">;
export type ReceivedDelivery = WebhookReceived["items"][number];
export type WebhookSecret = ResponseOf<"/api/apps/{domain}/webhook-secret", "post">;
export type Sandbox = ResponseOf<"/api/apps/{domain}/sandbox", "get">;
export type AppHooks = ResponseOf<"/api/apps/{domain}/hooks", "get">;
export type Identity = ResponseOf<"/api/apps/{domain}/identity", "get">;
export type AppBuild = ResponseOf<"/api/apps/{domain}/build", "get">;
export type BackupRequired = ResponseOf<"/api/apps/{domain}/backup-required", "get">;
export type BuildCache = ResponseOf<"/api/apps/{domain}/build-cache", "get">;
export type ProcessTypes = ResponseOf<"/api/apps/{domain}/process-types", "get">;
export type ComposeSettings = ResponseOf<"/api/apps/{domain}/compose", "get">;

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
  hooks: (domain: string) => ["app", domain, "hooks"] as const,
  identity: (domain: string) => ["app", domain, "identity"] as const,
  headless: (domain: string) => ["app", domain, "headless"] as const,
  container: (domain: string) => ["app", domain, "container"] as const,
  build: (domain: string) => ["app", domain, "build"] as const,
  backupRequired: (domain: string) => ["app", domain, "backup-required"] as const,
  buildCache: (domain: string) => ["app", domain, "build-cache"] as const,
  processTypes: (domain: string) => ["app", domain, "process-types"] as const,
  compose: (domain: string) => ["app", domain, "compose"] as const,
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

/** The deploy hooks that apply, where they come from, and the operator's document when there is one. */
export const hooksQuery = (domain: string) =>
  queryOptions({
    queryKey: settingsKeys.hooks(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/hooks", { params: { domain }, signal }),
  });

/** How a container application is built and run (never the registry's password). Changes nothing. */
export const containerQuery = (domain: string) =>
  queryOptions({
    queryKey: settingsKeys.container(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/container", { params: { domain }, signal }),
  });

/** The build block the next deployment uses, and whether it is the operator's or the repository's. */
export const buildQuery = (domain: string) =>
  queryOptions({
    queryKey: settingsKeys.build(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/build", { params: { domain }, signal }),
  });

/** Whether an update is refused when its backup fails, and whether that is the default. */
export const backupRequiredQuery = (domain: string) =>
  queryOptions({
    queryKey: settingsKeys.backupRequired(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/backup-required", { params: { domain }, signal }),
  });

/** The account the app runs as, and whether it can move to one of its own. Changes nothing. */
export const identityQuery = (domain: string) =>
  queryOptions({
    queryKey: settingsKeys.identity(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/identity", { params: { domain }, signal }),
  });

/** Whether a Compose stack is a worker that still has a port recorded (`noust app headless`). */
export const headlessQuery = (domain: string) =>
  queryOptions({
    queryKey: settingsKeys.headless(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/headless", { params: { domain }, signal }),
  });

/**
 * True when the server answered that it has no such endpoint: a node on an older Noust (3.1)
 * behind a 3.2 central, which has neither deploy hooks nor accounts per application. The
 * application itself exists, or its settings would not be open.
 */
export function olderServer(error: unknown): boolean {
  return isApiError(error) && (error.status === 404 || error.status === 405);
}

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

/** How much the package managers keep between this application's builds. */
export const buildCacheQuery = (domain: string) =>
  queryOptions({
    queryKey: settingsKeys.buildCache(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/build-cache", { params: { domain }, signal }),
  });

/** The process types the code declares, and which of them a deployment uses. */
export const processTypesQuery = (domain: string) =>
  queryOptions({
    queryKey: settingsKeys.processTypes(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/process-types", { params: { domain }, signal }),
  });

/** A Compose stack's file, profiles and extra arguments, and the flags the arguments may use. */
export const composeSettingsQuery = (domain: string) =>
  queryOptions({
    queryKey: settingsKeys.compose(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/compose", { params: { domain }, signal }),
  });
