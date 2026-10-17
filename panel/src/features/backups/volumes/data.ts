/**
 * What the Volumes tab reads beyond the API's shapes: one row per application that has a data
 * policy or data backups, the state of its policy in the console's words, and what a policy
 * form holds and sends. Pure, so the rules are tested without a screen.
 */

import type { Consistency, VolumeBackup, VolumePolicy, VolumePolicyBody } from "../../../api/queries/volumeBackups";
import type { Locale } from "../../../app/locale";
import type { Status } from "../../../components/ui/StatusPill";
import type { PlainKey } from "../../../i18n";
import { calendarWords } from "../../cron/data";
import { scheduleExpression, scheduleForm } from "../../databases/backups/schedule";
import type { ScheduleForm } from "../../databases/backups/schedule";
import { destinationBody, gbValue, limitText, limitValue } from "../policy/limits";
import type { DestinationDraft } from "../policy/limits";

export type VolumeState = "none" | "paused" | "failed" | "scheduled" | "ok";

export interface StateView {
  state: Status;
  label: PlainKey;
}

const VIEWS: Readonly<Record<VolumeState, StateView>> = {
  none: { state: "warning", label: "volumeBackups.state.none" },
  paused: { state: "stopped", label: "volumeBackups.state.paused" },
  failed: { state: "failed", label: "volumeBackups.state.failed" },
  scheduled: { state: "queued", label: "volumeBackups.state.scheduled" },
  ok: { state: "running", label: "volumeBackups.state.ok" },
};

/** The order the state column sorts by: what needs attention first. */
export const STATE_RANK: Readonly<Record<VolumeState, number>> = { failed: 0, none: 1, paused: 2, scheduled: 3, ok: 4 };

/** A policy's state: none, paused, failing, not run yet, or working. */
export function stateOf(policy: Pick<VolumePolicy, "configured" | "enabled" | "last_status"> | null | undefined): VolumeState {
  if (!policy?.configured) return "none";
  if (!policy.enabled) return "paused";
  if (policy.last_status === "failed") return "failed";
  if (policy.last_status === "ok") return "ok";
  return "scheduled";
}

export function viewOf(state: VolumeState): StateView {
  return VIEWS[state];
}

export interface VolumeRow {
  domain: string;
  policy: VolumePolicy | null;
  backups: VolumeBackup[];
  /** The newest backup, whichever piece of data it is of. */
  newest: VolumeBackup | null;
  state: VolumeState;
}

/** One row per application with a configured policy or any backup, alphabetical. */
export function volumeRows(policies: readonly VolumePolicy[] | undefined, backups: readonly VolumeBackup[] | undefined): VolumeRow[] {
  const byDomain = new Map<string, { policy: VolumePolicy | null; backups: VolumeBackup[] }>();
  const entry = (domain: string): { policy: VolumePolicy | null; backups: VolumeBackup[] } => {
    const found = byDomain.get(domain) ?? { policy: null, backups: [] };
    byDomain.set(domain, found);
    return found;
  };
  for (const policy of policies ?? []) if (policy.configured) entry(policy.domain).policy = policy;
  for (const backup of backups ?? []) entry(backup.domain).backups.push(backup);
  return [...byDomain.entries()]
    .map(([domain, { policy, backups: taken }]) => {
      const sorted = [...taken].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
      return { domain, policy, backups: sorted, newest: sorted[0] ?? null, state: stateOf(policy) };
    })
    .sort((a, b) => a.domain.localeCompare(b.domain));
}

const ALIASES: readonly string[] = ["hourly", "daily", "weekly", "monthly"];

/** A stored schedule in plain words: a calendar expression is read, an alias is its own preset's words. */
export function scheduleWords(schedule: string | null | undefined, locale: Locale): string {
  const stored = (schedule ?? "").trim();
  return ALIASES.includes(stored) ? calendarWords(stored, "", locale) : calendarWords("custom", stored, locale);
}

/** Total bytes of an application's data backups. */
export function totalBytes(backups: readonly Pick<VolumeBackup, "size_bytes">[]): number {
  return backups.reduce((sum, backup) => sum + backup.size_bytes, 0);
}

/** Whether a backup is the kind a retention may delete: only scheduled ones. */
export function backupKind(backup: Pick<VolumeBackup, "tags">): "scheduled" | "safety" | "manual" {
  if ((backup.tags ?? []).includes("safety")) return "safety";
  if ((backup.tags ?? []).includes("scheduled")) return "scheduled";
  return "manual";
}

/** A backup's check, as the table says it. */
export function checkOf(backup: Pick<VolumeBackup, "verified_ok" | "last_verified_at">): "checked" | "failed" | "never" {
  if (backup.verified_ok === true) return "checked";
  if (backup.verified_ok === false) return "failed";
  return "never";
}

/** What a policy form holds. */
export interface VolumeForm {
  domain: string;
  schedule: ScheduleForm;
  /** Back up everything the application declares: no keys are sent. */
  everything: boolean;
  /** The keys chosen when it is not everything. */
  targets: string[];
  extraPaths: string;
  count: string;
  days: string;
  /** The most the scheduled backups kept here may weigh together, in gigabytes; empty for no limit. */
  gb: string;
  sends: DestinationDraft[];
  consistency: Consistency;
  enabled: boolean;
}

function asConsistency(value: string | undefined): Consistency {
  return value === "pause" || value === "stop" ? value : "live";
}

/** The form's starting values: a stored policy's, or what a new one keeps unless changed. */
export function formOf(domain: string, policy: VolumePolicy | null): VolumeForm {
  if (policy === null) {
    return { domain, schedule: scheduleForm(null), everything: true, targets: [], extraPaths: "", count: "7", days: "30", gb: "", sends: [], consistency: "live", enabled: true };
  }
  return {
    domain,
    schedule: scheduleForm(policy.schedule),
    everything: (policy.targets ?? []).length === 0,
    targets: [...(policy.targets ?? [])],
    extraPaths: (policy.extra_paths ?? []).join("\n"),
    count: limitText(policy.retention_count),
    days: limitText(policy.retention_days),
    gb: limitText(policy.retention_gb),
    sends: (policy.destinations ?? []).map((item) => ({ name: item.name, count: limitText(item.retention_count), days: limitText(item.retention_days), gb: limitText(item.retention_gb) })),
    consistency: asConsistency(policy.consistency),
    enabled: policy.enabled,
  };
}

/** The directories typed, one per line, once each. */
export function parsePaths(text: string): string[] {
  return [...new Set(text.split(/\r?\n/).map((line) => line.trim()).filter((line) => line !== ""))];
}

/** A folder as the API takes it: relative to the application's data, inside it. */
export function validPath(path: string): boolean {
  return !path.startsWith("/") && !path.startsWith("~") && !path.split("/").includes("..") && !path.includes("\0");
}

/** The request a form becomes. */
export function bodyOf(form: VolumeForm): VolumePolicyBody {
  return {
    schedule: scheduleExpression(form.schedule),
    targets: form.everything ? [] : form.targets,
    extra_paths: parsePaths(form.extraPaths),
    retention_count: limitValue(form.count),
    retention_days: limitValue(form.days),
    retention_gb: gbValue(form.gb),
    destinations: destinationBody(form.sends),
    consistency: form.consistency,
    enabled: form.enabled,
  };
}
