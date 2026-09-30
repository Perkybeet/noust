/**
 * What the Cron page reads beyond the raw API shape: a run's outcome in the console's state
 * language, the calendar presets the editor offers, and the formatting its live preview needs.
 *
 * The preset values double as the `schedule` the API takes (`hourly`, `daily`, `weekly`,
 * `monthly`): `CronManager` expands the alias itself (`SCHEDULE_ALIASES`), so the dialog sends
 * the alias rather than keeping its own copy of what each one expands to - one implementation,
 * and it is `POST /api/cron/preview` that shows the operator what the alias actually means.
 */

import type { Status } from "../../components/ui/StatusPill";
import type { CronJobList } from "../../api/queries/cron";
import { getLocale } from "../../app/locale";
import type { Locale } from "../../app/locale";
import { translate } from "../../i18n";
import { formatClock, parseTimestamp } from "../../lib/format";

export type CronJob = CronJobList["jobs"][number];

export type Schedule = "hourly" | "daily" | "weekly" | "monthly" | "custom";

const SCHEDULE_VALUES: readonly Schedule[] = ["hourly", "daily", "weekly", "monthly", "custom"];

function presetLabel(value: Schedule, locale: Locale): string {
  switch (value) {
    case "hourly":
      return translate(locale, "cron.presets.hourly");
    case "daily":
      return translate(locale, "cron.presets.daily");
    case "weekly":
      return translate(locale, "cron.presets.weekly");
    case "monthly":
      return translate(locale, "cron.presets.monthly");
    case "custom":
      return translate(locale, "cron.presets.custom");
  }
}

/** The calendar presets the editor offers, labelled in the active language. */
export function schedulePresets(locale: Locale = getLocale()): readonly { value: Schedule; label: string }[] {
  return SCHEDULE_VALUES.map((value) => ({ value, label: presetLabel(value, locale) }));
}

export interface RunView {
  state: Status;
  label: string;
  /** Systemd's own word for how a failed run ended (`exit-code`, `timeout`), shown in mono. */
  detail?: string;
}

/**
 * A run's systemd `Result` (`success`, `exit-code`, `signal`, `timeout`, `resources`,
 * `core-dump`, `watchdog`, `start-limit-hit`), or the job's own `never ran`/`unknown`, in the
 * console's state language.
 */
export function runStatus(result: string | null | undefined, locale: Locale = getLocale()): RunView {
  const word = (result ?? "").trim().toLowerCase();
  if (word === "" || word === "never ran") return { state: "unknown", label: translate(locale, "cron.status.neverRun") };
  if (word === "success") return { state: "running", label: translate(locale, "cron.status.succeeded") };
  if (word === "unknown") return { state: "unknown", label: translate(locale, "cron.status.unknown") };
  // The state word is the state; systemd's reason goes beside it, as the services list does.
  return { state: "failed", label: translate(locale, "cron.status.failed"), detail: word };
}

const SYSTEMD_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;
const TIME = "(\\d{1,2}):(\\d{2})(?::00)?";
const EVERY_HOUR = /^\*-\*-\* \*:00(?::00)?$/;
const EVERY_MINUTES = /^\*-\*-\* \*:0{0,2}\/(\d{1,2})(?::00)?$/;
const EVERY_DAY = new RegExp(`^\\*-\\*-\\* ${TIME}$`);
const WORKDAYS = new RegExp(`^Mon\\.\\.Fri \\*-\\*-\\* ${TIME}$`);
const ONE_DAY = new RegExp(`^(${SYSTEMD_DAYS.join("|")}) \\*-\\*-\\* ${TIME}$`);
const MONTHLY = new RegExp(`^\\*-\\*-(\\d{1,2}) ${TIME}$`);

function clock(hours: string | undefined, minutes: string | undefined): string {
  return `${(hours ?? "").padStart(2, "0")}:${minutes ?? "00"}`;
}

/** A systemd day name ("Mon") as the language writes it in a sentence ("Monday", "lunes"). */
function weekdayName(day: string, locale: Locale): string {
  // 2024-01-01 was a Monday: the index into the week is the offset from it.
  const date = new Date(Date.UTC(2024, 0, 1 + SYSTEMD_DAYS.indexOf(day as (typeof SYSTEMD_DAYS)[number])));
  return new Intl.DateTimeFormat(locale, { weekday: "long", timeZone: "UTC" }).format(date);
}

/**
 * A schedule in plain words, read from the calendar expression systemd normalised: "Every day
 * at 02:00", "Every Monday at 02:00", "Every 15 minutes". A shape it does not recognise is
 * called a custom schedule, with the expression itself shown beside it by the caller. Times are
 * the server's clock, as the timer reads them.
 */
export function calendarWords(schedule: string, onCalendar: string, locale: Locale = getLocale()): string {
  const calendar = onCalendar.trim().replace(/\s+/g, " ");
  if (EVERY_HOUR.test(calendar)) return translate(locale, "cron.words.everyHour");
  const minutes = EVERY_MINUTES.exec(calendar);
  if (minutes) return translate(locale, "cron.words.everyMinutes", { count: Number(minutes[1]) });
  const daily = EVERY_DAY.exec(calendar);
  if (daily) return translate(locale, "cron.words.everyDayAt", { time: clock(daily[1], daily[2]) });
  const workdays = WORKDAYS.exec(calendar);
  if (workdays) return translate(locale, "cron.words.workdaysAt", { time: clock(workdays[1], workdays[2]) });
  const oneDay = ONE_DAY.exec(calendar);
  if (oneDay) return translate(locale, "cron.words.everyWeekdayAt", { weekday: weekdayName(oneDay[1] ?? "Mon", locale), time: clock(oneDay[2], oneDay[3]) });
  const monthly = MONTHLY.exec(calendar);
  if (monthly) return translate(locale, "cron.words.monthlyAt", { day: Number(monthly[1]), time: clock(monthly[2], monthly[3]) });
  // With no expression to read, the preset it was made from says it; an expression of another
  // shape is not guessed at from its preset.
  const preset = schedule.trim().toLowerCase();
  if (calendar === "" && preset !== "custom" && SCHEDULE_VALUES.includes(preset as Schedule)) return presetLabel(preset as Schedule, locale);
  return translate(locale, "cron.words.custom");
}

export interface CronSearch {
  /** Free text matched against the job name and command. */
  q?: string;
}

function text(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed.slice(0, 200);
}

export function validateCronSearch(search: Record<string, unknown>): CronSearch {
  const q = text(search["q"]);
  return q !== undefined ? { q } : {};
}

export function isFiltered(search: CronSearch): boolean {
  return search.q !== undefined;
}

export function filterJobs(jobs: readonly CronJob[], search: CronSearch): CronJob[] {
  const needle = search.q?.toLowerCase();
  if (needle === undefined) return [...jobs];
  return jobs.filter((job) => `${job.name} ${job.command}`.toLowerCase().includes(needle));
}

const OFFSET = /(?:Z|([+-])(\d{2}):?(\d{2}))$/;

export interface RunTimes {
  /** The run on the server's clock, the one the calendar expression is written in: "Wed, Sep 30, 02:00". */
  server: string;
  /** That clock's zone: "UTC", "UTC+02:00". */
  zone: string;
  /** The same moment on the reader's own clock ("04:00"), when it reads differently. */
  local: string | null;
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

/**
 * One of the preview's next runs, as the server's clock reads it and, when the reader's clock
 * differs, as theirs does: the calendar expression is in server time, so that is what the
 * preview leads with. The preview is computed by `systemd-analyze` with an explicit offset in
 * each timestamp; one the console cannot place in time is returned as it came.
 */
export function runTimes(value: string, locale: Locale = getLocale()): RunTimes {
  const date = parseTimestamp(value);
  const offset = OFFSET.exec(value.trim());
  if (date === null || offset === null) return { server: value, zone: "", local: null };
  const minutes = offset[1] === undefined ? 0 : (offset[1] === "-" ? -1 : 1) * (Number(offset[2]) * 60 + Number(offset[3]));
  // The server's wall clock, drawn as if it were UTC so the reader's own zone plays no part.
  const wall = new Date(date.getTime() + minutes * 60_000);
  const server = new Intl.DateTimeFormat(locale, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "UTC",
  }).format(wall);
  const zone = minutes === 0 ? "UTC" : `UTC${minutes < 0 ? "-" : "+"}${pad(Math.floor(Math.abs(minutes) / 60))}:${pad(Math.abs(minutes) % 60)}`;
  // getTimezoneOffset is minutes behind UTC; the server's offset is minutes ahead of it.
  const local = -date.getTimezoneOffset() === minutes ? null : formatClock(date, locale);
  return { server, zone, local };
}
