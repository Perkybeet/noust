/**
 * The databases list's search params, in a module of their own: a route's options are loaded
 * with the first page, so the address must be read without importing the list's filtering and
 * what it knows about protection and servers.
 */

import { ownerSearch } from "../teams/search";
import type { OwnerSearch } from "../teams/search";

/** "Needs attention": a database whose backups are missing, failing or paused. */
export const BACKUP_FILTERS = ["attention"] as const;

export type BackupFilter = (typeof BACKUP_FILTERS)[number];

export interface DatabasesSearch extends OwnerSearch {
  /** Free text matched against the name, the engine and the applications using it. */
  q?: string;
  engine?: string;
  backups?: BackupFilter;
  /** Only the databases that carry this tag. */
  tag?: string;
  /**
   * Opens the New database dialog on arrival (the command palette's "New database"). It is not
   * a filter and is dropped as soon as it has been used, so it never stays in the address.
   */
  new?: true;
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
  const tag = text(search["tag"]);
  return {
    ...(search["new"] === true || search["new"] === "true" || search["new"] === 1 ? { new: true as const } : {}),
    ...(q !== undefined ? { q } : {}),
    // An engine, or an instance of one in a container (`postgresql@proggest.postgres`).
    ...(engine !== undefined && /^[a-z]+(@[a-z0-9_.-]+)?$/.test(engine) ? { engine } : {}),
    ...(backups !== undefined && (BACKUP_FILTERS as readonly string[]).includes(backups) ? { backups: backups as BackupFilter } : {}),
    ...(tag !== undefined && /^[a-z0-9](?:[a-z0-9._-]{0,30}[a-z0-9])?$/.test(tag) ? { tag } : {}),
    ...ownerSearch(search),
  };
}

export function isFiltered(search: DatabasesSearch): boolean {
  return (
    search.q !== undefined ||
    search.engine !== undefined ||
    search.backups !== undefined ||
    search.tag !== undefined ||
    search.project !== undefined ||
    search.environment !== undefined
  );
}
