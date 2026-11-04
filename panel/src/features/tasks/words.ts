/**
 * What a task says of itself, in the console's language: its title, the step it is on, its state and who
 * started it. Pure and given the language, so a test reads English and a component passes `t.locale`.
 * What the server wrote in English beside a sentence is shown when the console has no text for its code, and
 * what a tool printed is never rewritten.
 */

import type { Locale } from "../../app/locale";
import { deployStatus } from "../../components/page/status";
import type { StatusView } from "../../components/page/status";
import type { T } from "../../i18n";
import { serverSentence } from "../../lib/serverSentence";
import { describeActor } from "../activity/data";
import { stepWords } from "../app/stepWords";
import { isMine } from "./model";
import type { Me, Task, TaskState } from "./model";

/** What the task is, as a sentence: "Deploy shop.example.com". A code nobody gave words to is shown as the server wrote it. */
export function titleOf(task: Pick<Task, "title" | "name">, locale: Locale): string {
  const said = serverSentence(locale, task.title, task.name, "text");
  if (said !== "") return said;
  // An operation carries no English beside its code: the code is better than nothing.
  return task.title?.code ?? "";
}

/**
 * What it is doing now: the step in the console's language when the server sent its sentence (and for the
 * deploy's own steps, which the console knows by name), what a queued job waits for in the queue's own words,
 * or nothing.
 */
export function stepOf(task: Pick<Task, "state" | "type" | "step" | "stepCoded" | "waitingFor">, locale: Locale): string | null {
  if (task.state === "queued") return task.waitingFor;
  return stepWords(task.type ?? "", task.step, locale, task.stepCoded);
}

const WORD_OF: Readonly<Record<TaskState, string>> = {
  queued: "queued",
  running: "running",
  succeeded: "completed",
  failed: "failed",
  cancelled: "cancelled",
};

/** The glyph and the word of a task's state, from the one table of deployments and jobs. */
export function viewOf(state: TaskState, locale: Locale): StatusView {
  return deployStatus(WORD_OF[state], locale);
}

/** Who started it: "You" for the person's own, the actor's words otherwise, and "not recorded" for a job from before they were kept. */
export function whoOf(task: Pick<Task, "actor">, me: Me, t: T): string {
  if (isMine(task, me)) return t("tasks.you");
  if (task.actor === null) return t("activity.actor.notRecorded");
  return describeActor(t, task.actor).label;
}
