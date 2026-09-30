import type { Locale } from "../../app/locale";
import { formatClock, formatDate, parseTimestamp } from "../../lib/format";

/**
 * A version by when it was made, the way a person tells versions apart: "Sep 30, 00:26", with
 * the year when it is not this one. The full timestamp is in the version's id, shown beside it.
 * Null when the moment is unknown.
 */
export function versionTitle(when: string | null | undefined, locale: Locale, now: Date = new Date()): string | null {
  const moment = parseTimestamp(when ?? null);
  if (moment === null) return null;
  const day = formatDate(moment, { year: moment.getFullYear() !== now.getFullYear() }, locale);
  return `${day}, ${formatClock(moment, locale)}`;
}
