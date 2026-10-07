import type { T } from "../../../i18n";

/**
 * How long a policy keeps its backups, in words: "The last 7 backups, for up to 30 days". For the
 * backups of data and of Noust itself; a database's dumps have their own words
 * (`databases/backups/schedule.ts`), which say "dumps".
 *
 * Args:
 *   t: The translator.
 *   count: How many are kept, or null for no limit.
 *   days: How many days they are kept, or null for no limit.
 */
export function backupRetentionWords(t: T, count: number | null | undefined, days: number | null | undefined): string {
  if (count != null && days != null) return t("backups.retention.both", { count, days: String(days) });
  if (count != null) return t("backups.retention.count", { count });
  if (days != null) return t("backups.retention.days", { count: days });
  return t("backups.retention.all");
}
