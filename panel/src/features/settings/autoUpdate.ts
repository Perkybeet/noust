/**
 * The automatic update as a form: what the operator edits (a switch, which releases, a window of
 * hours on the server's clock, the days it opens on) and the body the API takes back. The server
 * judges every part again; the checks here only keep an obviously wrong form from being sent.
 */

import type { AutoUpdateBody, AutoUpdateSettings } from "../../api/queries/autoUpdate";
import type { Locale } from "../../app/locale";
import { WEEKDAYS, validTime, weekdayLabel } from "../databases/backups/schedule";
import type { Weekday } from "../databases/backups/schedule";

/** Which releases a server takes by itself, narrowest first. */
export const CHANNELS = ["security", "patch", "stable"] as const;
export type Channel = (typeof CHANNELS)[number];

export interface AutoUpdateDraft {
  enabled: boolean;
  channel: Channel;
  /** HH:MM, the server's clock. */
  from: string;
  to: string;
  everyDay: boolean;
  days: readonly Weekday[];
}

export type AutoUpdateProblem = "from" | "to" | "same" | "days";

/** The draft of a server that has never set it up: on, patch releases, 03:00 to 05:00, every day. */
export const EMPTY_DRAFT: AutoUpdateDraft = { enabled: true, channel: "patch", from: "03:00", to: "05:00", everyDay: true, days: [] };

function isChannel(value: string): value is Channel {
  return (CHANNELS as readonly string[]).includes(value);
}

/** The API's `mon,thu` as the weekdays the form checks. */
function weekdaysOf(days: string): readonly Weekday[] {
  const wanted = days.split(",").map((day) => day.trim().toLowerCase());
  return WEEKDAYS.filter((day) => wanted.includes(day.toLowerCase()));
}

/** The form a stored setting is edited in. */
export function draftOf(settings: AutoUpdateSettings): AutoUpdateDraft {
  const [from = "03:00", to = "05:00"] = settings.window.split("-");
  const everyDay = settings.days === "daily";
  return {
    enabled: settings.enabled,
    channel: isChannel(settings.channel) ? settings.channel : "patch",
    from,
    to,
    everyDay,
    days: everyDay ? [] : weekdaysOf(settings.days),
  };
}

/** What is wrong with a draft, by field. Empty when it can be sent. */
export function problemsOf(draft: AutoUpdateDraft): Partial<Record<AutoUpdateProblem, true>> {
  const found: Partial<Record<AutoUpdateProblem, true>> = {};
  if (!validTime(draft.from)) found.from = true;
  if (!validTime(draft.to)) found.to = true;
  if (found.from === undefined && found.to === undefined && minutes(draft.from) === minutes(draft.to)) found.same = true;
  if (!draft.everyDay && draft.days.length === 0) found.days = true;
  return found;
}

function minutes(time: string): number {
  const [hours = "0", rest = "0"] = time.trim().split(":");
  return Number(hours) * 60 + Number(rest);
}

function pad(time: string): string {
  const [hours = "0", rest = "00"] = time.trim().split(":");
  return `${hours.padStart(2, "0")}:${rest}`;
}

/** The body for the API. Only valid once `problemsOf` is empty. */
export function bodyOf(draft: AutoUpdateDraft): AutoUpdateBody {
  return {
    enabled: draft.enabled,
    channel: draft.channel,
    window: `${pad(draft.from)}-${pad(draft.to)}`,
    days: draft.everyDay ? "daily" : WEEKDAYS.filter((day) => draft.days.includes(day)).map((day) => day.toLowerCase()).join(","),
  };
}

/** Whether a window is still open after midnight, which is said so the operator is not surprised by it. */
export function crossesMidnight(from: string, to: string): boolean {
  return validTime(from) && validTime(to) && minutes(to) < minutes(from);
}

/** The days a window opens on, in words: "every day", "Mon, Thu". */
export function daysWords(days: string, locale: Locale, everyDay: string): string {
  return days === "daily" ? everyDay : weekdaysOf(days).map((day) => weekdayLabel(day, locale)).join(", ");
}
