import type { Locale } from "../../../app/locale";
import type { Rollout } from "../../../api/queries/rollout";
import { translate } from "../../../i18n/translate";

/** The bounds the backend holds the delivery settings to (`noust.core.app_rollout_store`). */
export const HISTORY_MIN = 1;
export const HISTORY_MAX = 1000;
export const STOP_TIMEOUT_MIN = 1;
export const STOP_TIMEOUT_MAX = 3600;

/** The signals a unit may be told to stop with; SIGTERM is what systemd sends when none is set. */
export const STOP_SIGNALS = ["SIGINT", "SIGQUIT", "SIGHUP"] as const;
export const DEFAULT_SIGNAL = "default";

/** The form, as typed: an empty number is "the default". */
export interface RolloutDraft {
  history_keep: string;
  stop_timeout: string;
  stop_signal: string;
}

export type RolloutField = keyof RolloutDraft;
export const ROLLOUT_FIELDS: readonly RolloutField[] = ["history_keep", "stop_timeout", "stop_signal"];

/** The form for the settings an app has. */
export function rolloutDraftOf(rollout: Pick<Rollout, "history_keep" | "stop_timeout" | "stop_signal">): RolloutDraft {
  return {
    history_keep: rollout.history_keep === null ? "" : String(rollout.history_keep),
    stop_timeout: rollout.stop_timeout === null ? "" : String(rollout.stop_timeout),
    // SIGTERM is the default, so an explicit one is the same choice as none.
    stop_signal: rollout.stop_signal === null || rollout.stop_signal === "SIGTERM" ? DEFAULT_SIGNAL : rollout.stop_signal,
  };
}

/** The numbers an empty field leaves unset, and the signal an unset choice leaves to systemd. */
export interface RolloutValues {
  history_keep: number | null;
  stop_timeout: number | null;
  stop_signal: string | null;
}

export type RolloutErrors = Partial<Record<RolloutField, string>>;

function whole(text: string, min: number, max: number): number | null | "bad" {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  if (!/^\d+$/.test(trimmed)) return "bad";
  const value = Number.parseInt(trimmed, 10);
  return value < min || value > max ? "bad" : value;
}

/** The form, read as the backend will; only what is plainly out of range is caught here. */
export function parseRolloutDraft(draft: RolloutDraft, locale: Locale): { values: RolloutValues | null; errors: RolloutErrors } {
  const errors: RolloutErrors = {};
  const history = whole(draft.history_keep, HISTORY_MIN, HISTORY_MAX);
  if (history === "bad") errors.history_keep = translate(locale, "rollout.limits.historyRange", { min: HISTORY_MIN, max: HISTORY_MAX });
  const timeout = whole(draft.stop_timeout, STOP_TIMEOUT_MIN, STOP_TIMEOUT_MAX);
  if (timeout === "bad") errors.stop_timeout = translate(locale, "rollout.limits.stopTimeoutRange", { min: STOP_TIMEOUT_MIN, max: STOP_TIMEOUT_MAX });
  if (history === "bad" || timeout === "bad") return { values: null, errors };
  return {
    values: { history_keep: history, stop_timeout: timeout, stop_signal: draft.stop_signal === DEFAULT_SIGNAL ? null : draft.stop_signal },
    errors,
  };
}

/**
 * What to send: only the fields that differ from what is stored, so saving one never rewrites
 * the others (a stop policy rewrites the unit).
 */
export function rolloutChanges(values: RolloutValues, stored: RolloutValues): Partial<RolloutValues> {
  const changed: Partial<RolloutValues> = {};
  if (values.history_keep !== stored.history_keep) changed.history_keep = values.history_keep;
  if (values.stop_timeout !== stored.stop_timeout) changed.stop_timeout = values.stop_timeout;
  if (values.stop_signal !== stored.stop_signal) changed.stop_signal = values.stop_signal;
  return changed;
}

/** How many of the form's fields differ from what is stored: the save bar's count. */
export function rolloutDraftChanges(draft: RolloutDraft, stored: RolloutDraft): number {
  return ROLLOUT_FIELDS.filter((field) => draft[field].trim() !== stored[field].trim()).length;
}
