/**
 * When a task ends, who should hear of it and how (design 3.4 and 11.1): pure decisions the feeds act on.
 *
 * A task of the person's that ends while they are somewhere else says so, with a way to see it; one that ends on
 * its own page says nothing, because they are looking at it; and one that is not theirs is not announced by the
 * task centre at all (the server's notice, which every console gets, is what tells a failure nobody was waiting for).
 */

import { launchedFrom } from "./launches";
import { isActive, isFinished, isMine, isOnItsPage, placeOf } from "./model";
import type { Me, Task } from "./model";
import { LONG_TASK_MS } from "./browserNotice";
import type { NoticeState } from "../../realtime/jobEnds";

/** What to do about the end of a task. */
export type Ending = "toast" | "silent" | "none";

/** The task that has just ended, when this change is the end of one the console knew was running or waiting. */
export function endedTask(before: Task | undefined, after: Task | undefined): Task | null {
  return before !== undefined && isActive(before) && after !== undefined && isFinished(after) ? after : null;
}

/**
 * Whether the person is where they asked for the task: its page (its own, its application's, Software's) or the page they were
 * on when it was launched (the wizard, the firewall, the backups of a database). Only a person who left is told they left.
 */
function isStaying(task: Task, pathname: string): boolean {
  // A tab that is hidden is a person who is elsewhere, whatever page it shows: the browser's notification is for them.
  const looking = typeof document === "undefined" || document.visibilityState !== "hidden";
  return isOnItsPage(task, pathname) || (looking && launchedFrom(task.key) === pathname);
}

/** Whether the page on screen is the task's own (Activity > Tasks > one), which is its log. */
function isOnTaskPage(task: Task, pathname: string): boolean {
  if (task.kind !== "job") return false;
  const own = placeOf(task).to.replace("$id", encodeURIComponent(task.id));
  return pathname === own || pathname.startsWith(`${own}/`);
}

/** Whether the page on screen is the page of the application the task acts on. */
function isOnApplicationPage(task: Task, pathname: string): boolean {
  if (task.domain === null) return false;
  const app = `/apps/${encodeURIComponent(task.domain)}`;
  return pathname === app || pathname.startsWith(`${app}/`);
}

/**
 * How to tell the person: a job of theirs, away from where they launched it, says so; where they are looking at it it is
 * silent; the rest is not said here. A failure on an application's page is the exception: the card a page draws for its own
 * job does not announce it, and the page of a first deploy that failed has no application to draw it, so this is the one
 * channel that says it (item 141). Only the task's own page, which is its log, says a failure by itself.
 */
export function endingOf(task: Task, me: Me, pathname: string): Ending {
  if (task.kind !== "job" || !isMine(task, me)) return "none";
  if (isOnTaskPage(task, pathname)) return "silent";
  if (task.state === "failed" && isOnApplicationPage(task, pathname)) return "toast";
  return isStaying(task, pathname) ? "silent" : "toast";
}

/** The server's own word for how the task ended, which its notice carries: what the mark of `realtime/jobEnds.ts` is left for. */
export function noticeStateOf(task: Pick<Task, "state">): NoticeState {
  return task.state === "succeeded" ? "active" : task.state === "failed" ? "failed" : "idle";
}

/**
 * Whether the person is leaving a long task of theirs: one that has run for a while and whose page is not the one
 * they are on. The first time it happens is when the browser's notification is offered.
 */
export function isLeavingLongTask(tasks: Iterable<Task>, me: Me, pathname: string, now: number): boolean {
  for (const task of tasks) {
    if (task.kind !== "job" || task.state !== "running" || !isMine(task, me) || isStaying(task, pathname)) continue;
    const since = task.startedAt ?? task.createdAt;
    if (since !== null && now - since >= LONG_TASK_MS) return true;
  }
  return false;
}
