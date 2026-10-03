/**
 * The Overview's reading of `GET /api/overview`, as pure functions so every rule is tested
 * without a page: what each key figure says and how worried it is, the attention list (the
 * server's, plus the one thing the server cannot see), and the activity timeline by day.
 */

import type { Overview, OverviewActivityEntry, OverviewAttentionItem, OverviewAttentionReason } from "../../api/queries/overview";
import { appStatus } from "../../components/page/status";
import { parseTimestamp } from "../../lib/format";
import type { AppInfo } from "../apps/data";

/** How a figure reads: calm, a warning, or a failure. Only the last two take a colour. */
export type FigureTone = "neutral" | "warn" | "fail";

/** Disk: amber under 20% free or when it fills within 30 days, red under 10% or within 7. */
export function diskTone(disk: Overview["disk"]): FigureTone {
  if (disk.error) return "neutral";
  const days = disk.forecast_full_days ?? null;
  if (disk.free_percent < 10 || (days !== null && days < 7)) return "fail";
  if (disk.free_percent < 20 || (days !== null && days < 30)) return "warn";
  return "neutral";
}

export function appsTone(apps: Overview["apps"]): FigureTone {
  return !apps.error && apps.failed > 0 ? "fail" : "neutral";
}

/** Today's deploys: red while the newest one of the day failed. */
export function deploysTone(deploys: Overview["deploys"]): FigureTone {
  if (deploys.error) return "neutral";
  if (deploys.last_status === "failed") return "fail";
  return deploys.failed > 0 ? "warn" : "neutral";
}

export function certificatesTone(certs: Overview["certificates"]): FigureTone {
  if (certs.error) return "neutral";
  if (certs.expired > 0) return "fail";
  return certs.expiring > 0 ? "warn" : "neutral";
}

export function backupsTone(backups: Overview["backups"]): FigureTone {
  if (backups.error) return "neutral";
  if (backups.failed_24h > 0) return "fail";
  return backups.unprotected_scheduled > 0 ? "warn" : "neutral";
}

export function updatesTone(updates: Overview["updates"]): FigureTone {
  if (updates.error) return "neutral";
  return (updates.security ?? 0) > 0 || updates.reboot_required === true ? "warn" : "neutral";
}

/** Whether this server runs nothing yet: the Overview is a list of first steps instead. */
export function isEmptyServer(overview: Overview): boolean {
  const { apps } = overview;
  if (apps.error) return false;
  return apps.running + apps.failed + apps.stopped + apps.static + apps.unmanaged === 0;
}

const RANK: Readonly<Record<string, number>> = { fail: 0, warn: 1 };

/**
 * The attention list: the server's, worst first, plus what only the console knows. The server
 * reads an application's state from its systemd units, so a service that runs but answers
 * nothing on its port (the apps list's `no_answer`, found by probing the port) is not in its
 * list: that one reason is added here, on the application's own item.
 */
export function attentionItems(overview: Overview, apps: readonly AppInfo[] | undefined): OverviewAttentionItem[] {
  const items = (overview.attention ?? []).map((item) => ({ ...item, reasons: [...item.reasons] }));
  for (const app of apps ?? []) {
    const status = app.status.trim().toLowerCase();
    if (status !== "no_answer" && status !== "no answer") continue;
    if (appStatus(app.status).state !== "failed") continue;
    const reason: OverviewAttentionReason = {
      kind: "state",
      severity: "fail",
      code: "no_answer",
      params: {},
      detail: null,
      when: null,
      deployment_id: null,
      actions: ["view_log", "diagnose"],
    };
    const existing = items.find((item) => item.subject["kind"] === "app" && item.subject["domain"] === app.domain);
    if (existing) {
      if (existing.reasons.some((known) => known.code === "service_failed" || known.code === "no_answer")) continue;
      existing.reasons.unshift(reason);
      existing.severity = "fail";
      continue;
    }
    items.push({ id: `app:${app.domain}`, subject: { kind: "app", domain: app.domain }, title: app.domain, severity: "fail", reasons: [reason] });
  }
  return items.sort((a, b) => (RANK[a.severity] ?? 2) - (RANK[b.severity] ?? 2) || a.title.toLowerCase().localeCompare(b.title.toLowerCase()));
}

/** How many items there are in all: the server may have cut its list. */
export function attentionTotal(overview: Overview, items: readonly OverviewAttentionItem[]): number {
  const server = overview.attention ?? [];
  return Math.max(items.length, overview.attention_total + (items.length - server.length));
}

export interface ActivityDay {
  /** `today`, `yesterday`, or the day's date at local midnight. */
  day: "today" | "yesterday" | Date;
  entries: { entry: OverviewActivityEntry; at: Date | null }[];
}

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

/** The timeline, newest first, grouped by local day; an entry without a moment counts as today. */
export function activityDays(entries: readonly OverviewActivityEntry[], now: Date, limit: number): ActivityDay[] {
  const today = startOfDay(now);
  const yesterday = today - 86_400_000;
  const days: ActivityDay[] = [];
  for (const entry of entries.slice(0, limit)) {
    const at = parseTimestamp(entry.at ?? null);
    const start = at === null ? null : startOfDay(at);
    const day: ActivityDay["day"] = start === null || start === today ? "today" : start === yesterday ? "yesterday" : new Date(start);
    const last = days.at(-1);
    const same =
      last !== undefined && (typeof last.day === "string" || typeof day === "string" ? last.day === day : last.day.getTime() === day.getTime());
    if (same) last.entries.push({ entry, at });
    else days.push({ day, entries: [{ entry, at }] });
  }
  return days;
}
