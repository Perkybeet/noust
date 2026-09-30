/**
 * The Backups page's first question, "is every application protected?", answered once from
 * three reads: the applications, every backup, and the schedules. Pure, so every threshold is
 * tested without a page.
 *
 * An application's backups are "up to date" while the newest one is younger than twice its
 * schedule's period (a daily schedule tolerates one missed night), or than a week when nothing
 * schedules it. Domains with backups but no application (one deleted since) keep their row:
 * their backups are still on disk and still restorable.
 */

import type { AppList } from "../../api/queries/apps";
import type { BackupList, BackupSchedule } from "../../api/queries/backups";
import { parseTimestamp } from "../../lib/format";

export type BackupRow = BackupList["backups"][number];
type AppRow = AppList["apps"][number];

export type Coverage = "current" | "stale" | "never";

export interface CoverageRow {
  domain: string;
  /** False for a domain whose application is gone but whose backups are still kept. */
  deployed: boolean;
  /** Its backups, newest first. */
  backups: BackupRow[];
  latest: BackupRow | null;
  schedule: BackupSchedule | null;
  /** Bytes, summed over its backups. */
  size: number;
  coverage: Coverage;
}

const HOUR = 3_600_000;
const DAY = 24 * HOUR;

/** How often each schedule preset runs; a custom calendar is read as daily, the common case. */
const PERIOD: Readonly<Record<string, number>> = { hourly: HOUR, daily: DAY, weekly: 7 * DAY, monthly: 31 * DAY };

/** With nothing scheduling it, a backup older than this no longer protects much. */
export const UNSCHEDULED_STALE_AFTER = 7 * DAY;

/** How old the newest backup may be before the application counts as out of date. */
export function staleAfter(schedule: Pick<BackupSchedule, "schedule"> | null): number {
  if (schedule === null) return UNSCHEDULED_STALE_AFTER;
  return 2 * (PERIOD[schedule.schedule.trim().toLowerCase()] ?? DAY);
}

function time(backup: BackupRow): number {
  return parseTimestamp(backup.timestamp)?.getTime() ?? Number.NEGATIVE_INFINITY;
}

/** Whether the newest backup is recent enough, given what schedules the application. */
export function coverageOf(latest: BackupRow | null, schedule: BackupSchedule | null, now: number): Coverage {
  if (latest === null) return "never";
  return now - time(latest) > staleAfter(schedule) ? "stale" : "current";
}

export interface CoverageInput {
  apps: readonly Pick<AppRow, "domain">[];
  backups: readonly BackupRow[];
  schedules: readonly BackupSchedule[];
  now?: number;
}

/** One row per application, and per domain that still has backups, alphabetically. */
export function coverageRows({ apps, backups, schedules, now = Date.now() }: CoverageInput): CoverageRow[] {
  const deployed = new Set(apps.map((app) => app.domain));
  const domains = new Set([...deployed, ...backups.map((backup) => backup.domain)]);
  const byDomain = new Map<string, BackupRow[]>();
  for (const backup of backups) byDomain.set(backup.domain, [...(byDomain.get(backup.domain) ?? []), backup]);
  const scheduleOf = new Map(schedules.map((schedule) => [schedule.domain, schedule]));
  return [...domains].sort().map((domain) => {
    const own = [...(byDomain.get(domain) ?? [])].sort((a, b) => time(b) - time(a));
    const latest = own[0] ?? null;
    const schedule = scheduleOf.get(domain) ?? null;
    return {
      domain,
      deployed: deployed.has(domain),
      backups: own,
      latest,
      schedule,
      size: own.reduce((sum, backup) => sum + backup.size, 0),
      coverage: coverageOf(latest, schedule, now),
    };
  });
}

export type CoverageFilter = "attention";

export interface CoverageSearch {
  /** Free text matched against the domain. */
  q?: string;
  /** Only the applications whose backups are missing or out of date. */
  show?: CoverageFilter;
  /** The application whose backups are open in the drawer. */
  domain?: string;
}

function text(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed.slice(0, 200);
}

/**
 * The Backups tab's search params. `domain` opens that application's backups in the drawer,
 * so `/backups?domain=shop.example.com`, the address 3.0 filtered the flat list with, still
 * lands on that application's backups.
 */
export function validateCoverageSearch(search: Record<string, unknown>): CoverageSearch {
  const q = text(search["q"]);
  const domain = text(search["domain"]);
  return {
    ...(q !== undefined ? { q } : {}),
    ...(search["show"] === "attention" ? { show: "attention" as const } : {}),
    ...(domain !== undefined ? { domain } : {}),
  };
}

export function isCoverageFiltered(search: CoverageSearch): boolean {
  return search.q !== undefined || search.show !== undefined;
}

export function filterCoverage(rows: readonly CoverageRow[], search: CoverageSearch): CoverageRow[] {
  const needle = search.q?.toLowerCase();
  return rows.filter(
    (row) => (needle === undefined || row.domain.toLowerCase().includes(needle)) && (search.show !== "attention" || row.coverage !== "current"),
  );
}

/** Worst first, for sorting by the state column: never backed up, then out of date. */
export const COVERAGE_RANK: Readonly<Record<Coverage, number>> = { never: 0, stale: 1, current: 2 };
