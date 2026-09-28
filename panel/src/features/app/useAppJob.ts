import { useQuery } from "@tanstack/react-query";

import { activeJobsQuery, useFollowedJob } from "../../api/queries/jobs";
import type { Job } from "../../api/queries/jobs";
import { getLocale } from "../../app/locale";
import type { Locale } from "../../app/locale";
import { translate } from "../../i18n";
import type { MessageKey } from "../../i18n";

/** How a job on an app is named while it runs and when it ends (the backend's JobType). */
const JOB_KEYS: Readonly<Record<string, { running: MessageKey; failed: MessageKey }>> = {
  deploy: { running: "appPages.job.deploy.running", failed: "appPages.job.deploy.failed" },
  update: { running: "appPages.job.update.running", failed: "appPages.job.update.failed" },
  rollback: { running: "appPages.job.rollback.running", failed: "appPages.job.rollback.failed" },
  restore: { running: "appPages.job.restore.running", failed: "appPages.job.restore.failed" },
  delete: { running: "appPages.job.delete.running", failed: "appPages.job.delete.failed" },
  backup: { running: "appPages.job.backup.running", failed: "appPages.job.backup.failed" },
  push: { running: "appPages.job.push.running", failed: "appPages.job.push.failed" },
  migrate: { running: "appPages.job.migrate.running", failed: "appPages.job.migrate.failed" },
  service_action: { running: "appPages.job.serviceAction.running", failed: "appPages.job.serviceAction.failed" },
  zero_downtime: { running: "appPages.job.zeroDowntime.running", failed: "appPages.job.zeroDowntime.failed" },
};

const DEFAULT_JOB_KEYS = { running: "appPages.job.default.running", failed: "appPages.job.default.failed" } as const;

export interface JobWords {
  /** The bare state word ("Deploying"): a label on its own, or before the domain it names. */
  running: string;
  /** How its failure is titled, the domain's name and grammar already in the sentence. */
  failed: string;
}

export function jobWords(type: string, domain: string, locale: Locale = getLocale()): JobWords {
  const keys = JOB_KEYS[type] ?? DEFAULT_JOB_KEYS;
  return { running: translate(locale, keys.running), failed: translate(locale, keys.failed, { domain }) };
}

const RUNNING = new Set(["pending", "running"]);

/** The newest line the job logged, which is what it is doing now. */
export function jobStep(job: Job): string | null {
  const message = job.logs?.at(-1)?.["message"];
  return typeof message === "string" && message.trim() !== "" ? message.trim() : null;
}

export interface AppJob {
  /** A job on this app that is queued or running, from here or from anywhere else. */
  running: Job | null;
  /** The job started from this page, once it has failed, until dismissed. */
  failed: Job | null;
  /** Follows a job this page queued. */
  track: (job: Job) => void;
  dismiss: () => void;
}

/**
 * What is being done to one app right now. Any running job on it counts (a deploy from the
 * CLI, an update from another tab: the active jobs list names them); the job this page queued
 * is followed to its end (useFollowedJob: events, and a poll as the guarantee), so its failure
 * stays on screen until the operator dismisses it.
 */
export function useAppJob(domain: string): AppJob {
  const active = useQuery(activeJobsQuery());
  const followed = useFollowedJob();

  const mine = followed.job ?? undefined;
  const elsewhere = active.data?.jobs.find((job) => job.metadata?.["domain"] === domain && RUNNING.has(job.status));
  const running = mine !== undefined && RUNNING.has(mine.status) ? mine : (elsewhere ?? null);

  return {
    running,
    failed: mine?.status === "failed" ? mine : null,
    track: followed.follow,
    dismiss: followed.dismiss,
  };
}
