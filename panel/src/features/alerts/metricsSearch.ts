import { DEFAULT_RANGE, isRange } from "../overview/ranges";
import type { MetricRange } from "../overview/ranges";

export interface MetricsSearch {
  /** The charts' time range; the last 24 hours when absent. */
  window?: MetricRange;
}

/** A route's `validateSearch`: a range this console offers that is not the default, or nothing. */
export function validateMetricsSearch(search: Record<string, unknown>): MetricsSearch {
  const window = search["window"];
  return isRange(window) && window !== DEFAULT_RANGE ? { window } : {};
}
