/**
 * Acting on several applications at once (checklist M-08): what each action will do to each of the
 * chosen applications, found before anything is sent, so the operator reads the plan and not only
 * the verb. Pure, like the other models of the console; the dialog draws it and runs it.
 *
 * Every action is the call the application's own page makes (restart, start, stop, queue an
 * update), one application at a time: a bulk action is the same verb said many times, never a
 * second implementation of it.
 */

import { appStatus } from "../../components/page/status";
import { hasUnit } from "./data";
import type { AppInfo } from "./data";

export type BulkAction = "restart" | "start" | "stop" | "update";

export const BULK_ACTIONS: readonly BulkAction[] = ["restart", "update", "stop", "start"];

/** Why an application is left out of an action. */
export type SkipReason =
  /** A static site has no process to restart, start or stop. */
  | "static"
  /** Stopping what is already stopped. */
  | "stopped"
  /** Starting what is already running. */
  | "running"
  /** A deploy or an update is in progress: not the moment to restart or stop it. */
  | "busy";

export interface PlanEntry {
  domain: string;
  /** Null when the action will be done to it. */
  skip: SkipReason | null;
}

function skipFor(action: BulkAction, app: AppInfo): SkipReason | null {
  // An update queues, and a second one for an application that is being deployed folds into the
  // one waiting, so nothing is skipped: it is the one action a static site takes too.
  if (action === "update") return null;
  if (!hasUnit(app)) return "static";
  const state = appStatus(app.status).state;
  if (state === "deploying") return "busy";
  if (action === "stop" && state === "stopped") return "stopped";
  if (action === "start" && state === "running") return "running";
  return null;
}

/**
 * What an action will do to each application, in the order they were chosen.
 *
 * @param action - The verb.
 * @param apps - The chosen applications, as the list read them.
 * @returns One entry for each, saying whether it is done or left out, and why.
 */
export function planBulk(action: BulkAction, apps: readonly AppInfo[]): PlanEntry[] {
  return apps.map((app) => ({ domain: app.domain, skip: skipFor(action, app) }));
}

/** How many of a plan will be done and how many left out. */
export function counts(plan: readonly PlanEntry[]): { run: number; skipped: number } {
  const run = plan.filter((entry) => entry.skip === null).length;
  return { run, skipped: plan.length - run };
}
