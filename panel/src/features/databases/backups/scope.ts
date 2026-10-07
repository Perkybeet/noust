/**
 * What a backup policy covers and leaves out: one database or every database of an engine, and
 * the names it skips. The server validates every name where it is spelled into a dump command
 * (never joined into a string); this is the same shape, so a typo is said before the policy is
 * saved and not after the first night it fails.
 */

import type { BackupPolicy } from "../../../api/queries/databases";
import { engineFamilyOf } from "../engineKey";

export type PolicyScope = "database" | "all";

/** What an exclusion names: whole databases, PostgreSQL schemas, or tables (collections in MongoDB). */
export type ExclusionKind = "databases" | "schemas" | "tables";

/** Most names one kind of exclusion may list. */
export const MAX_EXCLUSIONS = 100;

const PATTERNS: Readonly<Record<ExclusionKind, RegExp>> = {
  databases: /^[A-Za-z0-9_][A-Za-z0-9_$-]{0,127}$/,
  schemas: /^[A-Za-z0-9_][A-Za-z0-9_$]{0,62}$/,
  tables: /^[A-Za-z0-9_*?][A-Za-z0-9_$.*?-]{0,127}$/,
};

/** Whether the engine has several databases a policy can cover at once: a Redis snapshot is one. */
export function coversEveryDatabase(engine: string): boolean {
  return engineFamilyOf(engine) !== "redis";
}

/**
 * What a policy of this scope can leave out on this engine: databases only for the policy over
 * all of them, schemas on PostgreSQL, tables on PostgreSQL and MySQL and collections on MongoDB.
 */
export function exclusionKinds(engine: string, scope: PolicyScope): ExclusionKind[] {
  const family = engineFamilyOf(engine);
  const kinds: ExclusionKind[] = [];
  if (scope === "all") kinds.push("databases");
  if (family === "postgresql") kinds.push("schemas");
  if (family === "postgresql" || family === "mysql" || family === "mongodb") kinds.push("tables");
  return kinds;
}

export interface ParsedNames {
  names: string[];
  invalid: string[];
  tooMany: boolean;
}

/** Reads names separated by commas, spaces or lines, once each. */
export function parseNames(text: string, kind: ExclusionKind): ParsedNames {
  const entries = [...new Set(text.split(/[\s,;]+/).filter((entry) => entry !== ""))];
  return {
    names: entries.filter((entry) => PATTERNS[kind].test(entry)),
    invalid: entries.filter((entry) => !PATTERNS[kind].test(entry)),
    tooMany: entries.length > MAX_EXCLUSIONS,
  };
}

/** The names a saved policy leaves out, one per line for the text box. */
export function exclusionText(policy: Pick<BackupPolicy, "exclusions"> | undefined, kind: ExclusionKind): string {
  return (policy?.exclusions?.[kind] ?? []).join("\n");
}

/** Everything a policy leaves out, as the lines the card and the list say it in. */
export function leftOutLines(policy: Pick<BackupPolicy, "exclusions"> | undefined): { kind: ExclusionKind; names: string[] }[] {
  return (["databases", "schemas", "tables"] as const)
    .map((kind) => ({ kind, names: [...(policy?.exclusions?.[kind] ?? [])] }))
    .filter((line) => line.names.length > 0);
}
