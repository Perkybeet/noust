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

/** The most gigabytes a policy may be asked to keep (the API's own bound). */
export const MAX_GB = 100_000;

/** The API's value for a typed size in gigabytes: null for empty, a decimal allowed. */
export function gbValue(text: string): number | null {
  const trimmed = text.trim();
  return trimmed === "" ? null : Number(trimmed.replace(",", "."));
}

/** Whether a typed size is empty or a number above zero up to `max` gigabytes. */
export function validGb(text: string, max: number = MAX_GB): boolean {
  const trimmed = text.trim();
  if (trimmed === "") return true;
  const value = Number(trimmed.replace(",", "."));
  return Number.isFinite(value) && value > 0 && value <= max;
}

/** A destination a policy sends to, as its form holds it. */
export interface DestinationDraft {
  name: string;
  count: string;
  days: string;
  /** How many gigabytes the dumps there may weigh together; empty for no limit. */
  gb?: string;
}

/**
 * What the API takes for each destination of a policy. A size limit is sent only by the policies
 * that have one (a database's): the others' destinations do not know the field.
 */
export function destinationBody(
  sends: readonly DestinationDraft[],
): { name: string; retention_count: number | null; retention_days: number | null; retention_gb?: number | null }[] {
  return sends.map((item) => ({
    name: item.name,
    retention_count: limitValue(item.count),
    retention_days: limitValue(item.days),
    ...(item.gb !== undefined ? { retention_gb: gbValue(item.gb) } : {}),
  }));
}

/** Whether every destination's limits are valid. */
export function destinationsValid(sends: readonly DestinationDraft[]): boolean {
  return sends.every((item) => validLimit(item.count, MAX_COUNT) && validLimit(item.days, MAX_DAYS) && validGb(item.gb ?? ""));
}
