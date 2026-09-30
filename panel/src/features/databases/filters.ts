/**
 * The databases list's filters, which live in the URL (`/databases?q=shop&engine=postgresql`)
 * so a filtered view can be bookmarked, shared and reached with the back button.
 */

import type { BackupPolicy, Database } from "../../api/queries/databases";
import { databaseId, databaseProtection } from "./protection";

/** "Needs attention": a database whose backups are missing, failing or paused. */
export const BACKUP_FILTERS = ["attention"] as const;

export type BackupFilter = (typeof BACKUP_FILTERS)[number];

export interface DatabasesSearch {
  /** Free text matched against the name, the engine and the applications using it. */
  q?: string;
  engine?: string;
  backups?: BackupFilter;
}

function text(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed.slice(0, 200);
}

/**
 * Reads the search params, dropping anything malformed instead of failing: a hand-edited or
 * stale link still opens the list, just less filtered.
 */
export function validateDatabasesSearch(search: Record<string, unknown>): DatabasesSearch {
  const q = text(search["q"]);
  const engine = text(search["engine"]);
  const backups = text(search["backups"]);
  return {
    ...(q !== undefined ? { q } : {}),
    ...(engine !== undefined && /^[a-z]+$/.test(engine) ? { engine } : {}),
    ...(backups !== undefined && (BACKUP_FILTERS as readonly string[]).includes(backups) ? { backups: backups as BackupFilter } : {}),
  };
}

export function isFiltered(search: DatabasesSearch): boolean {
  return search.q !== undefined || search.engine !== undefined || search.backups !== undefined;
}

/** The databases a search keeps, in their original order. */
export function filterDatabases(
  databases: readonly Database[],
  search: DatabasesSearch,
  policies: ReadonlyMap<string, BackupPolicy>,
): Database[] {
  const needle = search.q?.toLowerCase();
  return databases.filter((database) => {
    if (search.engine !== undefined && database.engine !== search.engine) return false;
    if (search.backups === "attention") {
      const { protection } = databaseProtection(database, policies.get(databaseId(database.engine, database.name)));
      if (protection === "protected" || protection === "scheduled") return false;
    }
    if (needle !== undefined) {
      const haystack = `${database.name} ${database.engine} ${(database.apps ?? []).join(" ")}`.toLowerCase();
      if (!haystack.includes(needle)) return false;
    }
    return true;
  });
}
