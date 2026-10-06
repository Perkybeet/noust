/**
 * Reading the free-form records some backup answers carry (a timer's state, where a copy was
 * sent, a destination's flags). The API types them as open dictionaries, so each member is
 * checked where it is read instead of being printed as whatever it happens to be.
 */

export type OpenRecord = Readonly<Record<string, unknown>>;

/** A member that is text; empty when it is missing or something else. */
export function textOf(record: OpenRecord | null | undefined, key: string): string {
  const value = record?.[key];
  return typeof value === "string" ? value : "";
}

/** A member that is a number; null when it is missing or something else. */
export function numberOf(record: OpenRecord | null | undefined, key: string): number | null {
  const value = record?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Whether a member is exactly true. */
export function isTrue(record: OpenRecord | null | undefined, key: string): boolean {
  return record?.[key] === true;
}

/** Whether a member is exactly false: said so, not merely missing. */
export function isFalse(record: OpenRecord | null | undefined, key: string): boolean {
  return record?.[key] === false;
}
