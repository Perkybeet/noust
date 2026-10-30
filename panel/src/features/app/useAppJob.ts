import { useQuery } from "@tanstack/react-query";

import { activeJobsQuery, useFollowedJob } from "../../api/queries/jobs";
import type { Job } from "../../api/queries/jobs";
import { getLocale } from "../../app/locale";
import type { Locale } from "../../app/locale";
import { translate } from "../../i18n";
import type { MessageKey } from "../../i18n";
import { phaseOf, stepOf } from "./deployments/phases";

/** How a job on an app is named while it runs and when it ends (the backend's JobType). */
const JOB_KEYS: Readonly<Record<string, { running: MessageKey; title: MessageKey; failed: MessageKey }>> = {
  deploy: { running: "common.statusPill.deploying", title: "appPages.job.deploy.title", failed: "appPages.job.deploy.failed" },
  update: { running: "appPages.job.update.running", title: "appPages.job.update.title", failed: "appPages.job.update.failed" },
  rollback: { running: "appPages.job.rollback.running", title: "appPages.job.rollback.title", failed: "appPages.job.rollback.failed" },
  restore: { running: "appPages.job.restore.running", title: "appPages.job.restore.title", failed: "appPages.job.restore.failed" },
  delete: { running: "appPages.job.delete.running", title: "appPages.job.delete.title", failed: "appPages.job.delete.failed" },
  backup: { running: "appPages.job.backup.running", title: "appPages.job.backup.title", failed: "appPages.job.backup.failed" },
  push: { running: "appPages.job.push.running", title: "appPages.job.push.title", failed: "appPages.job.push.failed" },
  migrate: { running: "appPages.job.migrate.running", title: "appPages.job.migrate.title", failed: "appPages.job.migrate.failed" },
  service_action: { running: "appPages.job.serviceAction.running", title: "appPages.job.serviceAction.title", failed: "appPages.job.serviceAction.failed" },
  zero_downtime: { running: "appPages.job.zeroDowntime.running", title: "appPages.job.zeroDowntime.title", failed: "appPages.job.zeroDowntime.failed" },
  sandbox_test: { running: "appPages.job.sandboxTest.running", title: "appPages.job.sandboxTest.title", failed: "appPages.job.sandboxTest.failed" },
};

const DEFAULT_JOB_KEYS = { running: "appPages.job.default.running", title: "appPages.job.default.title", failed: "appPages.job.default.failed" } as const;

export interface JobWords {
  /** The bare state word ("Deploying"): the app's state while the job runs. */
  running: string;
  /** What is being done, as a sentence with the domain in it: "Deploying shop.example.com". */
  title: string;
  /** How its failure is titled, the domain's name and grammar already in the sentence. */
  failed: string;
}

export function jobWords(type: string, domain: string, locale: Locale = getLocale()): JobWords {
  const keys = JOB_KEYS[type] ?? DEFAULT_JOB_KEYS;
  return {
    running: translate(locale, keys.running),
    title: translate(locale, keys.title, { domain }),
    failed: translate(locale, keys.failed, { domain }),
  };
}

const RUNNING = new Set(["pending", "running"]);

/**
 * Jobs that are work done in an app and not something that happens to it: a one-off command
 * leaves the app as it was, so it never turns the header into "Working" or stops its verbs.
 */
const NOT_A_STATE = new Set(["app_exec", "disk"]);

/** The jobs whose steps are the deploy pipeline's, which the console knows by name. */
const DEPLOY_JOBS = new Set(["deploy", "update", "rollback", "push", "zero_downtime", "migrate"]);

/** The steps of a deploy that are not one of its five phases, as the deployer names them, and their words. */
const FIXED_STEPS: readonly (readonly [RegExp, MessageKey])[] = [
  [/^deploying$/i, "appPages.job.steps.deploying"],
  [/^preparing deployment$/i, "appPages.job.steps.preparing"],
  [/^registering application$/i, "appPages.job.steps.registering"],
  [/^setting permissions$/i, "appPages.job.steps.permissions"],
  [/^creating site configuration$/i, "appPages.job.steps.site"],
  [/^creating systemd service$/i, "appPages.job.steps.service"],
  [/^obtaining ssl certificate$/i, "appPages.job.steps.certificate"],
  [/^enabling updates without downtime$/i, "appPages.job.steps.zeroDowntime"],
];

const PHASE_DOING = {
  fetch: "appPages.deployments.phases.fetch.doing",
  install: "appPages.deployments.phases.install.doing",
  build: "appPages.deployments.phases.build.doing",
  activate: "appPages.deployments.phases.activate.doing",
  health: "appPages.deployments.phases.health.doing",
} as const satisfies Record<string, MessageKey>;

/**
 * What a deploy is doing, in the console's language. The server names its steps in English ("Deploying",
 * "[7/7] Activating release...") and sends no code for them, so the ones a deploy takes are known here by
 * name: the five phases the deployment page draws, and the few steps around them. A step the console does not
 * know, and any step of a job that is not a deploy, is shown as the server said it. In English the server's own
 * words are shown whatever they are.
 *
 * @param type The job's type (the backend's JobType).
 * @param step What the job is doing, as the server says it: its current step or its newest line.
 * @param locale The console's language.
 */
export function stepWords(type: string, step: string | null | undefined, locale: Locale = getLocale()): string | null {
  if (step === null || step === undefined || step.trim() === "") return null;
  if (locale === "en" || !DEPLOY_JOBS.has(type)) return step;
  const name = (stepOf(step) ?? step).trim().replace(/(?:\.{3}|…)$/, "");
  for (const [pattern, key] of FIXED_STEPS) if (pattern.test(name)) return translate(locale, key);
  const phase = phaseOf(name);
  return phase === null ? step : `${translate(locale, PHASE_DOING[phase])}…`;
}

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
  const elsewhere = active.data?.jobs.find(
    (job) => job.metadata?.["domain"] === domain && RUNNING.has(job.status) && !NOT_A_STATE.has(job.type),
  );
  const running = mine !== undefined && RUNNING.has(mine.status) ? mine : (elsewhere ?? null);

  return {
    running,
    failed: mine?.status === "failed" ? mine : null,
    track: followed.follow,
    dismiss: followed.dismiss,
  };
}
