/**
 * What a task says of itself, in the console's language: its title, the step it is on and its state (who started it is
 * `who.ts`). Pure and given the language, so a test reads English and a component passes `t.locale`.
 * What the server wrote in English beside a sentence is shown when the console has no text for its code, and
 * what a tool printed is never rewritten.
 */

import type { Locale } from "../../app/locale";
import { deployStatus } from "../../components/page/status";
import type { StatusView } from "../../components/page/status";
import { serverSentence } from "../../lib/serverSentence";
import { stepWords } from "../app/stepWords";
import { withComponentName } from "../server/software/words";
import type { Task, TaskState } from "./model";

/** What the task is, as a sentence: "Deploy shop.example.com". A code nobody gave words to is shown as the server wrote it. */
export function titleOf(task: Pick<Task, "title" | "name">, locale: Locale): string {
  // An install or a removal from Software names its component by the registry's id: said by the name the page gives it.
  const said = serverSentence(locale, withComponentName(task.title, locale), task.name, "text");
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
