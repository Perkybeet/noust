/**
 * Retention limits as a policy form holds them. A limit is typed as text because "empty" means
 * "no limit", which the API takes as null; the same three functions serve every policy that
 * keeps backups (a database's, an application's data, Noust's own state).
 */

/** The form's text for a stored limit: empty for none. */
export function limitText(value: number | null | undefined): string {
  return value == null ? "" : String(value);
}

/** The API's value for a typed limit: null for empty. */
export function limitValue(text: string): number | null {
  const trimmed = text.trim();
  return trimmed === "" ? null : Number(trimmed);
}

/** Whether a typed limit is empty or a whole number from 1 to `max`. */
export function validLimit(text: string, max: number): boolean {
  const trimmed = text.trim();
  if (trimmed === "") return true;
  const value = Number(trimmed);
  return Number.isInteger(value) && value >= 1 && value <= max;
}

/** How many scheduled backups a policy may keep (the API's own bounds). */
export const MAX_COUNT = 365;
/** How many days a scheduled backup may be kept. */
export const MAX_DAYS = 3650;

/** A destination a policy sends to, as its form holds it. */
export interface DestinationDraft {
  name: string;
  count: string;
  days: string;
}

/** What the API takes for each destination of a policy. */
export function destinationBody(sends: readonly DestinationDraft[]): { name: string; retention_count: number | null; retention_days: number | null }[] {
  return sends.map((item) => ({ name: item.name, retention_count: limitValue(item.count), retention_days: limitValue(item.days) }));
}

/** Whether every destination's limits are valid. */
export function destinationsValid(sends: readonly DestinationDraft[]): boolean {
  return sends.every((item) => validLimit(item.count, MAX_COUNT) && validLimit(item.days, MAX_DAYS));
}
