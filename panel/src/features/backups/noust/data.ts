/**
 * What the Noust tab reads beyond the API's shapes: whether the schedule is working, what the
 * form for it holds and sends, and the command that restores a bundle (never run from the
 * console). Pure, so every rule is tested without a screen.
 */

import type { Bundle, SelfSchedule, SelfScheduleBody } from "../../../api/queries/selfBackup";
import { scheduleExpression, scheduleForm } from "../../databases/backups/schedule";
import type { ScheduleForm } from "../../databases/backups/schedule";
import { destinationBody, limitText, limitValue } from "../policy/limits";
import type { DestinationDraft } from "../policy/limits";
import { isTrue, numberOf, textOf } from "../policy/record";

/** The shortest passphrase the server accepts. */
export const MIN_PASSPHRASE = 12;
/** The longest. */
export const MAX_PASSPHRASE = 1024;

export type ScheduleHealth = "off" | "paused" | "ok" | "problem";

export interface ScheduleReading {
  health: ScheduleHealth;
  /** What is wrong, when `problem`: each a key of `selfBackup.state.problems`. */
  problems: ("noPassphrase" | "timerMissing" | "lastFailed" | "locked")[];
}

/** Whether the schedule is on, paused, or on but not doing what it says. */
export function readSchedule(schedule: Pick<SelfSchedule, "configured" | "enabled" | "passphrase_stored" | "timer" | "last_status">): ScheduleReading {
  if (!schedule.configured) return { health: "off", problems: [] };
  if (!schedule.enabled) return { health: "paused", problems: [] };
  const problems: ScheduleReading["problems"] = [];
  if (schedule.passphrase_stored === false) problems.push("noPassphrase");
  if (schedule.passphrase_stored == null) problems.push("locked");
  if (!isTrue(schedule.timer, "installed")) problems.push("timerMissing");
  if (schedule.last_status === "failed") problems.push("lastFailed");
  return { health: problems.length === 0 ? "ok" : "problem", problems };
}

/** Whether a bundle can be taken or checked without asking for the passphrase: the server holds it. */
export function passphraseKnown(schedule: Pick<SelfSchedule, "passphrase_stored">): boolean {
  return schedule.passphrase_stored === true;
}

/** The kind of a bundle: scheduled (retention may delete it), by hand, or the copy a restore takes first. */
export function bundleKind(bundle: Pick<Bundle, "kind" | "scheduled">): "scheduled" | "manual" | "pre-restore" {
  if (bundle.kind === "pre-restore") return "pre-restore";
  return bundle.scheduled || bundle.kind === "scheduled" ? "scheduled" : "manual";
}

/** The command that restores a bundle, run at a terminal on this server. */
export function restoreCommand(name: string): string {
  return `noust self-backup restore ${name}`;
}

/** The command that brings a whole server back from a bundle, on a new machine. */
export function restoreServerCommand(name: string): string {
  return `noust restore-server --from ${name}`;
}

export interface SelfForm {
  schedule: ScheduleForm;
  count: string;
  days: string;
  sends: DestinationDraft[];
  passphrase: string;
  repeat: string;
  enabled: boolean;
}

/** The form's starting values: the stored schedule's, or what a new one keeps unless changed. */
export function formOf(schedule: SelfSchedule | undefined): SelfForm {
  if (!schedule?.configured) {
    return { schedule: scheduleForm(null), count: "7", days: "30", sends: [], passphrase: "", repeat: "", enabled: true };
  }
  return {
    schedule: scheduleForm(schedule.schedule),
    count: limitText(schedule.retention_count),
    days: limitText(schedule.retention_days),
    sends: (schedule.destinations ?? []).map((item) => ({
      name: textOf(item, "name"),
      count: limitText(numberOf(item, "retention_count")),
      days: limitText(numberOf(item, "retention_days")),
    })),
    passphrase: "",
    repeat: "",
    enabled: schedule.enabled,
  };
}

/**
 * The request a form becomes. The passphrase goes only when one was typed: the one the server
 * keeps stays when the box is left empty.
 */
export function bodyOf(form: SelfForm): SelfScheduleBody {
  return {
    schedule: scheduleExpression(form.schedule),
    retention_count: limitValue(form.count),
    retention_days: limitValue(form.days),
    destinations: destinationBody(form.sends),
    passphrase: form.passphrase === "" ? null : form.passphrase,
    enabled: form.enabled,
  };
}

export type PassphraseProblem = "short" | "long" | "mismatch";

/** What is wrong with a passphrase typed twice, if anything. */
export function passphraseProblem(passphrase: string, repeat: string | null): PassphraseProblem | null {
  if (passphrase.length < MIN_PASSPHRASE) return "short";
  if (passphrase.length > MAX_PASSPHRASE) return "long";
  if (repeat !== null && repeat !== passphrase) return "mismatch";
  return null;
}
