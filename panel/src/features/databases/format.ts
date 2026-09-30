/**
 * The one reading of this area that `lib/format.ts` rounds too far: a cache hit ratio lives
 * between 90 and 100 percent, where the decimal is the whole story (99.2% and 100% are not the
 * same cache), so it always keeps one.
 */

import type { Locale } from "../../app/locale";

export function formatHitRatio(value: number, locale: Locale): string {
  if (!Number.isFinite(value)) return "-";
  return new Intl.NumberFormat(locale, { style: "percent", minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(value / 100);
}
