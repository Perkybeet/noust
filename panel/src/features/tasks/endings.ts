/**
 * When a task ends, who should hear of it and how (design 3.4 and 11.1): pure decisions the feeds act on.
 *
 * A task of the person's that ends while they are somewhere else says so, with a way to see it; one that ends on
 * its own page says nothing, because they are looking at it; and one that is not theirs is not announced by the
 * task centre at all (the server's notice, which every console gets, is what tells a failure nobody was waiting for).
 */

import { isActive, isFinished, isMine, isOnItsPage } from "./model";
import type { Me, Task } from "./model";
import { LONG_TASK_MS } from "./browserNotice";
import type { NoticeState } from "../../realtime/jobEnds";

/** What to do about the end of a task. */
export type Ending = "toast" | "silent" | "none";

/** The task that has just ended, when this change is the end of one the console knew was running or waiting. */
export function endedTask(before: Task | undefined, after: Task | undefined): Task | null {
  return before !== undefined && isActive(before) && after !== undefined && isFinished(after) ? after : null;
}

/** How to tell the person: a job of theirs, away from its page, says so; on its page it is silent; the rest is not said here. */
export function endingOf(task: Task, me: Me, pathname: string): Ending {
  if (task.kind !== "job" || !isMine(task, me)) return "none";
  return isOnItsPage(task, pathname) ? "silent" : "toast";
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
    if (task.kind !== "job" || task.state !== "running" || !isMine(task, me) || isOnItsPage(task, pathname)) continue;
    const since = task.startedAt ?? task.createdAt;
    if (since !== null && now - since >= LONG_TASK_MS) return true;
  }
  return false;
}
