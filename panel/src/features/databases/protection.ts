/**
 * Whether a database is backed up, from its policy and its dumps: the one reading the list, the
 * database's header and its Backups tab share, so the three never disagree.
 */

import type { BackupPolicy, Database, DatabaseBackup, PolicyList } from "../../api/queries/databases";
import type { Status } from "../../components/ui/StatusPill";
import type { PlainKey } from "../../i18n";

export type Protection = "protected" | "failed" | "scheduled" | "paused" | "unprotected" | "missing";

export interface ProtectionView {
  protection: Protection;
  state: Status;
  label: PlainKey;
}

const VIEWS: Readonly<Record<Protection, ProtectionView>> = {
  protected: { protection: "protected", state: "running", label: "databases.protection.protected" },
  failed: { protection: "failed", state: "failed", label: "databases.protection.failed" },
  scheduled: { protection: "scheduled", state: "queued", label: "databases.protection.scheduled" },
  paused: { protection: "paused", state: "stopped", label: "databases.protection.paused" },
  unprotected: { protection: "unprotected", state: "warning", label: "databases.protection.unprotected" },
  missing: { protection: "missing", state: "failed", label: "databases.protection.missing" },
};

/** A policy's state, as the backups of one database read it. */
export function policyProtection(policy: Pick<BackupPolicy, "configured" | "enabled" | "last_status"> | undefined | null): Protection {
  if (!policy?.configured) return "unprotected";
  if (!policy.enabled) return "paused";
  if (policy.last_status === "failed") return "failed";
  if (policy.last_status === "ok") return "protected";
  return "scheduled";
}

export function protectionView(protection: Protection): ProtectionView {
  return VIEWS[protection];
}

/** `engine/name`, the key every per-database map of this area uses. */
export function databaseId(engine: string, name: string): string {
  return `${engine}/${name}`;
}

/** Each database's policy, by `engine/name`. */
export function policiesById(list: PolicyList | undefined): ReadonlyMap<string, BackupPolicy> {
  return new Map((list?.policies ?? []).map((policy) => [databaseId(policy.engine, policy.database), policy]));
}

/** Each database's newest dump, by `engine/name`. */
export function newestDumps(dumps: readonly DatabaseBackup[] | undefined): ReadonlyMap<string, DatabaseBackup> {
  const newest = new Map<string, DatabaseBackup>();
  for (const dump of dumps ?? []) {
    const id = databaseId(dump.engine, dump.database);
    const current = newest.get(id);
    if (current === undefined || Date.parse(dump.created) > Date.parse(current.created)) newest.set(id, dump);
  }
  return newest;
}

/** One database of the list, read with its policy. */
export function databaseProtection(database: Database, policy: BackupPolicy | undefined): ProtectionView {
  if (database.missing) return VIEWS.missing;
  return VIEWS[policyProtection(policy)];
}

/** The order the Backups column sorts by: what needs attention first. */
export const PROTECTION_RANK: Readonly<Record<Protection, number>> = {
  missing: 0,
  failed: 1,
  unprotected: 2,
  paused: 3,
  scheduled: 4,
  protected: 5,
};
