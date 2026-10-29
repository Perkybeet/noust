/**
 * The schedule of a scheduled task as a person chooses it (how often, and at what time), and as the server takes
 * it (a calendar expression in systemd's syntax). A person says "every Friday at 18:05"; the console writes
 * `Fri *-*-* 18:05:00` and the server's own preview says what that means before anything is saved. What the
 * choices cannot say stays a custom expression, which is written and shown as it is.
 *
 * Reading goes through the patterns `calendarWords` reads (`data.ts`), so what the page says of a task and what the
 * editor opens on are the same understanding of the same text.
 */

import { EVERY_DAY, EVERY_HOUR, MONTHLY, ONE_DAY, SYSTEMD_DAYS } from "./data";

export type Frequency = "hourly" | "daily" | "weekly" | "monthly" | "custom";
export type Weekday = (typeof SYSTEMD_DAYS)[number];

export interface ScheduleChoice {
  frequency: Frequency;
  /** `HH:MM`, for every day, week and month. */
  time: string;
  weekday: Weekday;
  /** The day of the month, 1 to 28: the days every month has. */
  day: number;
  /** What is written when the frequency is custom. */
  calendar: string;
}

/** Every day at the hour the old presets meant: a nightly task is what most are. */
export const DEFAULT_CHOICE: ScheduleChoice = { frequency: "daily", time: "02:00", weekday: "Mon", day: 1, calendar: "" };

/** The `schedule` of `POST /api/cron`: the alias for every hour, otherwise a calendar expression. */
export function scheduleOf(choice: ScheduleChoice): string {
  const time = `${choice.time}:00`;
  switch (choice.frequency) {
    case "hourly":
      return "hourly";
    case "daily":
      return `*-*-* ${time}`;
    case "weekly":
      return `${choice.weekday} *-*-* ${time}`;
    case "monthly":
      return `*-*-${String(choice.day)} ${time}`;
    case "custom":
      return choice.calendar.trim();
  }
}

/** Whether a time is not `HH:MM` within a day. */
export function timeProblem(time: string): boolean {
  const match = /^(\d{2}):(\d{2})$/.exec(time);
  if (match === null) return true;
  return Number(match[1]) > 23 || Number(match[2]) > 59;
}

function clock(hours: string | undefined, minutes: string | undefined): string {
  return `${(hours ?? "").padStart(2, "0")}:${minutes ?? "00"}`;
}

/**
 * The choice a task that exists opens the editor on: read from the calendar systemd normalised, or from the
 * alias it was made with when no calendar was read. A calendar the choices cannot say opens as custom.
 */
export function choiceOf(job: { schedule: string; on_calendar: string }): ScheduleChoice {
  const calendar = job.on_calendar.trim().replace(/\s+/g, " ");
  if (calendar === "") {
    switch (job.schedule.trim().toLowerCase()) {
      case "hourly":
        return { ...DEFAULT_CHOICE, frequency: "hourly" };
      case "weekly":
        return { ...DEFAULT_CHOICE, frequency: "weekly" };
      case "monthly":
        return { ...DEFAULT_CHOICE, frequency: "monthly" };
      default:
        return DEFAULT_CHOICE;
    }
  }
  if (EVERY_HOUR.test(calendar)) return { ...DEFAULT_CHOICE, frequency: "hourly" };
  const daily = EVERY_DAY.exec(calendar);
  if (daily !== null) return { ...DEFAULT_CHOICE, frequency: "daily", time: clock(daily[1], daily[2]) };
  const weekly = ONE_DAY.exec(calendar);
  if (weekly !== null) return { ...DEFAULT_CHOICE, frequency: "weekly", weekday: (weekly[1] ?? "Mon") as Weekday, time: clock(weekly[2], weekly[3]) };
  const monthly = MONTHLY.exec(calendar);
  if (monthly !== null && Number(monthly[1]) >= 1 && Number(monthly[1]) <= 28) {
    return { ...DEFAULT_CHOICE, frequency: "monthly", day: Number(monthly[1]), time: clock(monthly[2], monthly[3]) };
  }
  return { ...DEFAULT_CHOICE, frequency: "custom", calendar: job.on_calendar.trim() };
}
