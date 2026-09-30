/**
 * A backup policy's schedule as the operator chooses it (how often, at what time) and the
 * systemd calendar expression it becomes. The words it is read back in are the Cron page's
 * (`calendarWords`), so a schedule reads the same wherever it appears.
 */

import type { BackupPolicy } from "../../../api/queries/databases";
import type { Locale } from "../../../app/locale";
import type { T } from "../../../i18n";
import { calendarWords } from "../../cron/data";

export type Frequency = "hourly" | "daily" | "weekly" | "monthly" | "custom";

export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;
export type Weekday = (typeof WEEKDAYS)[number];

export interface ScheduleForm {
  frequency: Frequency;
  /** HH:MM, the server's clock. */
  time: string;
  weekday: Weekday;
  /** Day of the month, 1 to 28 (every month has it). */
  day: string;
  /** A systemd OnCalendar expression, for `custom`. */
  custom: string;
}

export const DEFAULT_SCHEDULE: ScheduleForm = { frequency: "daily", time: "02:00", weekday: "Sun", day: "1", custom: "" };

const TIME = "(\\d{1,2}):(\\d{2})(?::00)?";
const EVERY_HOUR = /^\*-\*-\* \*:00(?::00)?$/;
const EVERY_DAY = new RegExp(`^\\*-\\*-\\* ${TIME}$`);
const ONE_DAY = new RegExp(`^(${WEEKDAYS.join("|")}) \\*-\\*-\\* ${TIME}$`);
const MONTHLY = new RegExp(`^\\*-\\*-(\\d{1,2}) ${TIME}$`);

function clock(hours: string | undefined, minutes: string | undefined): string {
  return `${(hours ?? "0").padStart(2, "0")}:${minutes ?? "00"}`;
}

/** The form a stored calendar expression is edited in; an unknown shape is a custom one. */
export function scheduleForm(expression: string | null | undefined): ScheduleForm {
  const calendar = (expression ?? "").trim().replace(/\s+/g, " ");
  if (calendar === "" || calendar === "daily") return DEFAULT_SCHEDULE;
  if (calendar === "hourly" || EVERY_HOUR.test(calendar)) return { ...DEFAULT_SCHEDULE, frequency: "hourly" };
  const daily = EVERY_DAY.exec(calendar);
  if (daily) return { ...DEFAULT_SCHEDULE, frequency: "daily", time: clock(daily[1], daily[2]) };
  const weekly = ONE_DAY.exec(calendar);
  if (weekly) return { ...DEFAULT_SCHEDULE, frequency: "weekly", weekday: (weekly[1] ?? "Sun") as Weekday, time: clock(weekly[2], weekly[3]) };
  const monthly = MONTHLY.exec(calendar);
  if (monthly && Number(monthly[1]) >= 1 && Number(monthly[1]) <= 28) {
    return { ...DEFAULT_SCHEDULE, frequency: "monthly", day: String(Number(monthly[1])), time: clock(monthly[2], monthly[3]) };
  }
  return { ...DEFAULT_SCHEDULE, frequency: "custom", custom: calendar };
}

/** Whether a time of day is written the way the form takes it. */
export function validTime(time: string): boolean {
  const match = /^(\d{1,2}):(\d{2})$/.exec(time.trim());
  return match !== null && Number(match[1]) <= 23 && Number(match[2]) <= 59;
}

/** The calendar expression a form becomes; systemd, through the API, is the judge of a custom one. */
export function scheduleExpression(form: ScheduleForm): string {
  const [hours = "02", minutes = "00"] = form.time.trim().split(":");
  const at = `${hours.padStart(2, "0")}:${minutes.padStart(2, "0")}:00`;
  switch (form.frequency) {
    case "hourly":
      return "*-*-* *:00:00";
    case "daily":
      return `*-*-* ${at}`;
    case "weekly":
      return `${form.weekday} *-*-* ${at}`;
    case "monthly":
      return `*-*-${form.day.padStart(2, "0")} ${at}`;
    case "custom":
      return form.custom.trim();
  }
}

/** A policy's schedule in plain words: "Every day at 02:00". */
export function policyScheduleWords(policy: Pick<BackupPolicy, "schedule" | "schedule_alias">, locale: Locale): string {
  return calendarWords(policy.schedule_alias ?? "custom", policy.schedule ?? "", locale);
}

/** The weekday names the form offers, in the language's own words. */
export function weekdayLabel(day: Weekday, locale: Locale): string {
  // 2024-01-01 was a Monday: the index into the week is the offset from it.
  const date = new Date(Date.UTC(2024, 0, 1 + WEEKDAYS.indexOf(day)));
  const name = new Intl.DateTimeFormat(locale, { weekday: "long", timeZone: "UTC" }).format(date);
  return name.charAt(0).toLocaleUpperCase(locale) + name.slice(1);
}

/** How long dumps are kept, in words: "The last 7, for up to 30 days". */
export function retentionWords(t: T, count: number | null | undefined, days: number | null | undefined): string {
  if (count != null && days != null) return t("databases.backups.policy.keepsBoth", { count, days: String(days) });
  if (count != null) return t("databases.backups.policy.keepsCount", { count });
  if (days != null) return t("databases.backups.policy.keepsDays", { count: days });
  return t("databases.backups.policy.keepsAll");
}
