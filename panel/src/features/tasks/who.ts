/**
 * Who started a task, in the console's words. Apart from `words.ts` because it needs the Activity page's way of wording an
 * actor, which the pinned chips in the frame of every page do not, so they do not carry it.
 */

import type { T } from "../../i18n";
import { describeActor } from "../activity/data";
import { isMine } from "./model";
import type { Me, Task } from "./model";

/** Who started it: "You" for the person's own, the actor's words otherwise, and "not recorded" for a job from before they were kept. */
export function whoOf(task: Pick<Task, "actor">, me: Me, t: T): string {
  if (isMine(task, me)) return t("tasks.you");
  if (task.actor === null) return t("activity.actor.notRecorded");
  return describeActor(t, task.actor).label;
}
