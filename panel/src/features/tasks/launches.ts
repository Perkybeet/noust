/**
 * Where each task of the person's was launched from, so that "you started it and left that page" is said only of a person who
 * left. The console has no record of which page asked for a task (it hears of it from the server), so the page the person is on
 * when it first hears of a task that has only just been created is taken as the page it was launched from: the job event, or the
 * read the page asks for as it queues the job, comes a moment after the request, and a person does not change page in that
 * moment. A task the console meets old (the ones a page load reads) was launched earlier and from somewhere it cannot know, so
 * it has no page and is told as it always was. Kept in memory only, and bounded: a session that launches a thousand tasks does
 * not keep a thousand paths.
 */

import type { Task } from "./model";

/** The most paths held; the oldest are forgotten first. */
const MAX = 200;

/** How young a task is, when the console first hears of it, for the page it is on to be the one that launched it. */
export const FRESH_MS = 30_000;

const launched = new Map<string, string>();

/** The page the task was launched from, or null when the console did not see it arrive. */
export function launchedFrom(key: string): string | null {
  return launched.get(key) ?? null;
}

/**
 * Notes the tasks the console has heard of for the first time, and only the ones that were just created, as launched from
 * `pathname`. It is handed everything the store holds after a change: only a task with no page yet is new.
 */
export function noteArrivals(tasks: Iterable<Pick<Task, "key" | "createdAt" | "state">>, pathname: string, now: number): void {
  for (const task of tasks) {
    if (launched.has(task.key)) continue;
    if (task.createdAt === null || now - task.createdAt > FRESH_MS || (task.state !== "queued" && task.state !== "running")) continue;
    launched.set(task.key, pathname);
    if (launched.size > MAX) {
      const oldest = launched.keys().next();
      if (oldest.done !== true) launched.delete(oldest.value);
    }
  }
}

/** Forgets every page. For tests. */
export function forgetLaunches(): void {
  launched.clear();
}
