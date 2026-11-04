/**
 * What Home's widgets say, as pure functions so every rule is tested without a page: the six figures of a
 * server, the work in progress, the certificates and backups that need a look. A widget reads a query and
 * draws what these answer, so what it says is the same wherever it is drawn (a tile, a half page, a band).
 */

import type { CertEntry } from "../../api/queries/certs";
import type { BackupList } from "../../api/queries/backups";
import type { Job } from "../../api/queries/jobs";
import type { Operation } from "../../api/queries/operations";
import type { Overview } from "../../api/queries/overview";
import type { Status } from "../../components/ui/StatusPill";
import type { T } from "../../i18n";
import { formatCount, formatPercent, parseTimestamp } from "../../lib/format";
import { serverSentence } from "../../lib/serverSentence";
import { CERT_WARNING_DAYS } from "./attention";
import { appsTotal, unmanagedApps } from "./overviewData";

// ---------------------------------------------------------------------------------------
// The six figures

export type FigureId = "apps" | "deploys" | "certificates" | "backups" | "disk" | "updates";

/** One of the six figures of a server: what it counts, how it stands, and where the whole of it is. */
export interface Figure {
  id: FigureId;
  /** Already in the console's language: "Applications". */
  label: string;
  /** The reading, with its unit: "12", "5 of 6", "62 %". */
  value: string;
  /** How it stands when it is not well; null when it is, so that a figure that is fine stays stone. */
  state: Status | null;
  /** One line under it, or null when there is nothing to add. */
  context: string | null;
  /** The page that has the whole of it. */
  to: string;
}

/** Where the disk starts to be a state: amber from here, red from the next. */
const DISK_WARN = 75;
const DISK_FAIL = 90;

function join(parts: readonly string[]): string {
  return parts.join(", ");
}

/**
 * The six figures `GET /api/overview` already carries (the page that came before the widgets never drew
 * them). A figure the server could not read says so with a dash, never with a zero that would read as "none".
 */
export function figuresOf(overview: Overview, t: T): Figure[] {
  const { apps, deploys, certificates, backups, disk, updates } = overview;
  const locale = t.locale;

  const failing = apps.failed;
  const running = apps.running + apps.static + unmanagedApps(apps);
  const appsFigure: Figure = {
    id: "apps",
    label: t("widgets.figures.apps"),
    value: apps.error ? "-" : formatCount(appsTotal(apps), locale),
    state: failing > 0 ? "failed" : null,
    context: apps.error ? null : join([t("widgets.figures.running", { count: running }), ...(failing > 0 ? [t("widgets.figures.failing", { count: failing })] : [])]),
    to: "/apps",
  };

  const deployParts = [
    ...(deploys.failed > 0 ? [t("widgets.figures.failed", { count: deploys.failed })] : []),
    ...(deploys.running > 0 ? [t("widgets.figures.inProgress", { count: deploys.running })] : []),
  ];
  const deploysFigure: Figure = {
    id: "deploys",
    label: t("widgets.figures.deploys"),
    value: deploys.error ? "-" : formatCount(deploys.total, locale),
    state: deploys.failed > 0 ? "warning" : null,
    context: deploys.error ? null : deployParts.length > 0 ? join(deployParts) : deploys.total === 0 ? t("widgets.figures.noDeploys") : t("widgets.figures.allSucceeded"),
    to: "/activity",
  };

  const needing = certificates.expiring + certificates.expired;
  const certificatesFigure: Figure = {
    id: "certificates",
    label: t("widgets.figures.certificates"),
    value: certificates.error ? "-" : formatCount(needing, locale),
    state: certificates.expired > 0 ? "failed" : certificates.expiring > 0 ? "warning" : null,
    context: certificates.error
      ? null
      : certificates.expired > 0
        ? t("widgets.figures.certsExpired", { count: certificates.expired })
        : certificates.expiring > 0 && certificates.next_days !== null && certificates.next_days !== undefined
          ? t("widgets.figures.certsExpiring", { count: certificates.next_days })
          : t("widgets.figures.certsValid", { count: certificates.total }),
    to: "/domains",
  };

  const unprotected = Math.max(0, backups.apps - backups.with_backup_24h);
  const backupsFigure: Figure = {
    id: "backups",
    label: t("widgets.figures.backups"),
    value: backups.error ? "-" : backups.apps === 0 ? "0" : t("widgets.figures.ratio", { done: backups.with_backup_24h, of: backups.apps }),
    state: backups.failed_24h > 0 ? "warning" : null,
    context: backups.error
      ? null
      : backups.failed_24h > 0
        ? t("widgets.figures.backupsFailed", { count: backups.failed_24h })
        : backups.apps === 0
          ? t("widgets.figures.noApps")
          : unprotected > 0
            ? t("widgets.figures.backupsMissing", { count: unprotected })
            : t("widgets.figures.backupsDone"),
    to: "/backups",
  };

  const diskState: Status | null = disk.error ? null : disk.percent >= DISK_FAIL ? "failed" : disk.percent >= DISK_WARN ? "warning" : null;
  const diskFigure: Figure = {
    id: "disk",
    label: t("widgets.figures.disk"),
    value: disk.error ? "-" : formatPercent(disk.percent, locale),
    state: diskState,
    context: disk.error ? null : disk.forecast_full_days !== null && disk.forecast_full_days !== undefined ? t("widgets.figures.diskFull", { count: Math.round(disk.forecast_full_days) }) : null,
    to: "/server",
  };

  const available = updates.available ?? null;
  const security = updates.security ?? 0;
  const updatesFigure: Figure = {
    id: "updates",
    label: t("widgets.figures.updates"),
    value: updates.error || available === null ? "-" : formatCount(available, locale),
    state: security > 0 ? "warning" : null,
    context:
      updates.supported === false
        ? t("widgets.figures.updatesUnsupported")
        : available === null
          ? null
          : security > 0
            ? t("widgets.figures.updatesSecurity", { count: security })
            : available === 0
              ? t("widgets.figures.upToDate")
              : null,
    to: "/server/system",
  };

  return [appsFigure, deploysFigure, certificatesFigure, backupsFigure, diskFigure, updatesFigure];
}

// ---------------------------------------------------------------------------------------
// Work in progress

/** One line of "Work in progress": a job that runs or waits, or a long operation that is not a job. */
export interface WorkLine {
  key: string;
  state: Extract<Status, "deploying" | "queued">;
  /** What it is, as a sentence in the console's language. */
  title: string;
  /** The step it is on, or what it waits for. */
  step: string | null;
  /** How far it has got, 0 to 100; null for what has no end it can measure (an operation, a job with no steps). */
  percent: number | null;
  /** The server it runs on, when the panel is the fleet's. */
  server: string | null;
}

export interface Work {
  running: number;
  queued: number;
  lines: WorkLine[];
}

function percentOf(job: Pick<Job, "progress" | "total_steps">): number | null {
  return job.total_steps > 0 ? Math.min(100, Math.round((job.progress / job.total_steps) * 100)) : null;
}

/** The lines of one server's work, as `workOf` builds them for the fleet too. */
function linesOf(jobs: readonly Job[], operations: readonly Operation[], t: T, server: string | null): WorkLine[] {
  const fromJobs = jobs.flatMap((job): WorkLine[] => {
    const queued = job.state === "queued";
    if (!queued && job.state !== "running") return [];
    return [
      {
        key: `${server ?? ""}:job:${job.id}`,
        state: queued ? "queued" : "deploying",
        title: serverSentence(t.locale, job.title, job.name, "text"),
        step: queued ? (job.waiting_for ?? null) : job.current_step === "" ? null : job.current_step,
        percent: queued ? null : percentOf(job),
        server,
      },
    ];
  });
  const fromOperations = operations.map(
    (operation): WorkLine => ({ key: `${server ?? ""}:operation:${operation.id}`, state: "deploying", title: serverSentence(t.locale, operation.title, operation.title.code, "text"), step: null, percent: null, server }),
  );
  // What runs first, then what waits, each in the order the server gave it.
  return [...fromJobs.filter((line) => line.state === "deploying"), ...fromOperations, ...fromJobs.filter((line) => line.state === "queued")];
}

/** The work of one server: its jobs and its long operations. */
export function workOf(jobs: readonly Job[], operations: readonly Operation[], t: T): Work {
  return summarize(linesOf(jobs, operations, t, null));
}

/** The work of every server of the fleet, from the rows of `GET /api/fleet/jobs/active`: a row is a server, with its jobs and its operations. */
export function fleetWorkOf(rows: readonly { server: string; jobs: readonly Job[]; operations: readonly Operation[] }[], t: T): Work {
  return summarize(rows.flatMap((row) => linesOf(row.jobs, row.operations, t, row.server)));
}

function summarize(lines: WorkLine[]): Work {
  return { running: lines.filter((line) => line.state === "deploying").length, queued: lines.filter((line) => line.state === "queued").length, lines };
}

// ---------------------------------------------------------------------------------------
// Certificates

export interface CertificateRow {
  domain: string;
  days: number;
  /** Whether it has passed the days Noust starts to say so: the ones that are not renewed by themselves stay in view longer. */
  expiring: boolean;
}

export interface CertificatesView {
  total: number;
  /** The ones that need a look, soonest first. */
  needing: CertificateRow[];
  /** How many are fine. */
  well: number;
  /** Of the fine ones, how many renew by themselves. */
  renewing: number;
}

/**
 * The certificates that need a look: those that expire within the days Noust warns (`CERT_WARNING_DAYS`),
 * soonest first, and how many are well. A certificate with no date (an unreadable one) is not guessed at.
 */
export function certificatesOf(certificates: readonly CertEntry[]): CertificatesView {
  const dated = certificates.flatMap((entry): CertificateRow[] =>
    entry.days_remaining === null || entry.days_remaining === undefined ? [] : [{ domain: entry.domain, days: entry.days_remaining, expiring: entry.days_remaining < CERT_WARNING_DAYS }],
  );
  const needing = dated.filter((row) => row.expiring).sort((a, b) => a.days - b.days);
  const wellOnes = certificates.filter((entry) => entry.days_remaining !== null && entry.days_remaining !== undefined && entry.days_remaining >= CERT_WARNING_DAYS);
  return { total: certificates.length, needing, well: wellOnes.length, renewing: wellOnes.filter((entry) => entry.auto_renew).length };
}

// ---------------------------------------------------------------------------------------
// Backups

/** One of the last days: whether a backup was taken, and how many. */
export interface BackupDay {
  /** `YYYY-MM-DD`, in the viewer's own time. */
  date: string;
  count: number;
}

function dayKey(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${String(date.getFullYear())}-${month}-${day}`;
}

/** The last `days` days, oldest first, each with the number of backups taken that day. */
export function backupDays(backups: BackupList["backups"], now: Date, days = 14): BackupDay[] {
  const counts = new Map<string, number>();
  for (const backup of backups) {
    const taken = parseTimestamp(backup.timestamp);
    if (taken === null) continue;
    const key = dayKey(taken);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return Array.from({ length: days }, (_, index) => {
    const day = new Date(now.getFullYear(), now.getMonth(), now.getDate() - (days - 1 - index));
    const date = dayKey(day);
    return { date, count: counts.get(date) ?? 0 };
  });
}

/** The newest backup of the list, or null when there is none. */
export function latestBackup(backups: BackupList["backups"]): { at: Date; domain: string; verified: boolean | null } | null {
  let best: { at: Date; domain: string; verified: boolean | null } | null = null;
  for (const backup of backups) {
    const at = parseTimestamp(backup.timestamp);
    if (at !== null && (best === null || at > best.at)) best = { at, domain: backup.domain, verified: backup.verified_ok ?? null };
  }
  return best;
}
