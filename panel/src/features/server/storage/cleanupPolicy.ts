/**
 * The cleanup policy of a server as a form: what the operator edits (a schedule in the one shape
 * every policy uses, a threshold, a set of actions) and the body the API takes back. The server
 * is the judge of every part, so the checks here only keep an obviously wrong form from being sent.
 */

import { DEFAULT_SCHEDULE, scheduleExpression, scheduleForm, validTime } from "../../databases/backups/schedule";
import type { ScheduleForm } from "../../databases/backups/schedule";
import type { CleanupPolicy } from "../queries";

/** The least and the most a journal can be vacuumed to (the API's range). */
export const JOURNAL_MIN_MB = 16;
export const JOURNAL_MAX_MB = 65536;

/** What the form holds while it is edited: text where the operator types, never a half-number. */
export interface PolicyDraft {
  enabled: boolean;
  schedule: ScheduleForm;
  /** A whole percentage, 0 to 99; 0 runs every time. */
  threshold: string;
  actions: string[];
  /** MB; empty keeps the journal action's own default. */
  journal: string;
  allowDocker: boolean;
}

export type PolicyProblem = "schedule" | "time" | "threshold" | "actions" | "journal";

/** The expressions systemd also accepts as words; a form shows them as the time they mean. */
const ALIASES: Readonly<Record<string, string>> = {
  weekly: "Mon *-*-* 00:00:00",
  monthly: "*-*-01 00:00:00",
};

/** A stored schedule as the form edits it (`weekly` is Monday midnight, `monthly` the first). */
export function scheduleOfPolicy(schedule: string): ScheduleForm {
  return scheduleForm(ALIASES[schedule.trim()] ?? schedule);
}

/** The form a stored policy is edited in. */
export function draftOf(policy: CleanupPolicy): PolicyDraft {
  return {
    enabled: policy.enabled,
    schedule: scheduleOfPolicy(policy.schedule),
    threshold: String(policy.threshold_percent),
    actions: [...(policy.actions ?? [])],
    journal: policy.journal_mb != null ? String(policy.journal_mb) : "",
    allowDocker: policy.allow_docker,
  };
}

/** The draft of a server that has never set a policy: off, daily at 02:00, at 80%. */
export const EMPTY_DRAFT: PolicyDraft = {
  enabled: true,
  schedule: DEFAULT_SCHEDULE,
  threshold: "80",
  actions: ["journal", "pkg-cache", "releases"],
  journal: "",
  allowDocker: false,
};

function wholeNumber(text: string): number | null {
  return /^\d+$/.test(text.trim()) ? Number(text.trim()) : null;
}

/** What is wrong with a draft, by field. Empty when it can be sent. */
export function policyProblems(draft: PolicyDraft): Partial<Record<PolicyProblem, true>> {
  const found: Partial<Record<PolicyProblem, true>> = {};
  if (draft.schedule.frequency === "custom" && draft.schedule.custom.trim() === "") found.schedule = true;
  if (draft.schedule.frequency !== "hourly" && draft.schedule.frequency !== "custom" && !validTime(draft.schedule.time)) found.time = true;
  const threshold = wholeNumber(draft.threshold);
  if (threshold === null || threshold > 99) found.threshold = true;
  if (draft.actions.length === 0) found.actions = true;
  if (draft.actions.includes("journal") && draft.journal.trim() !== "") {
    const journal = wholeNumber(draft.journal);
    if (journal === null || journal < JOURNAL_MIN_MB || journal > JOURNAL_MAX_MB) found.journal = true;
  }
  return found;
}

/** Whether the draft runs an action that deletes something the operator may want back. */
export function runsDocker(draft: PolicyDraft, dockerActions: readonly string[]): boolean {
  return draft.actions.some((action) => dockerActions.includes(action));
}

/** The body the API takes; the journal size is sent only while the journal action is chosen. */
export function policyBody(draft: PolicyDraft, dockerActions: readonly string[]): CleanupPolicy {
  const journal = draft.actions.includes("journal") ? wholeNumber(draft.journal) : null;
  return {
    enabled: draft.enabled,
    schedule: scheduleExpression(draft.schedule),
    threshold_percent: wholeNumber(draft.threshold) ?? 80,
    actions: draft.actions,
    journal_mb: journal,
    allow_docker: runsDocker(draft, dockerActions) && draft.allowDocker,
  };
}
