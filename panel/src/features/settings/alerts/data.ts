/**
 * Which alerts are firing, as one definition read by every place that says so: this page, the
 * inbox's "Needs you", Overview's "Needs attention" and the sidebar's counter (docs/DESIGN.md,
 * AL-1). Pure, so the order and the meaning of "firing" are tested without a page.
 */

import type { AlertRule, AlertRuleList } from "../../../api/queries/alerts";

type RuleState = NonNullable<AlertRule["states"]>[number];

/** Who acknowledged an alert and when (Unix seconds). */
export interface Acknowledgement {
  by: string;
  at: number;
}

/** One rule firing for one subject: the application it is about, or the server when empty. */
export interface FiringAlert {
  rule: string;
  /** The application's domain; empty for the server's own. */
  subject: string;
  severity: "warning" | "failed";
  /** What it reads now, in the unit of its metric; null when no reading is known. */
  value: number | null;
  unit: string;
  /** Since when it has been past the limit, Unix seconds. */
  since: number;
  acknowledgement: Acknowledgement | null;
}

/** An acknowledgement needs both who and when: half of one is not one. */
export function acknowledgement(entry: Pick<RuleState, "acknowledged_by" | "acknowledged_at">): Acknowledgement | null {
  const { acknowledged_by: by, acknowledged_at: at } = entry;
  return by !== null && by !== undefined && by !== "" && at !== null && at !== undefined ? { by, at } : null;
}

/**
 * The alerts that are firing now, worst first: failures before warnings, then those nobody has
 * acknowledged before those somebody has, then the longest-running. A rule that is switched off
 * decides nothing, so what it last decided is not listed.
 */
export function selectFiringAlerts(list: AlertRuleList | undefined): FiringAlert[] {
  const found = (list?.items ?? []).flatMap((rule) =>
    rule.enabled
      ? (rule.states ?? [])
          .filter((entry) => entry.state === "firing")
          .map(
            (entry): FiringAlert => ({
              rule: rule.name,
              subject: entry.subject,
              severity: rule.severity === "failed" ? "failed" : "warning",
              value: entry.last_value ?? null,
              unit: rule.unit,
              since: entry.since,
              acknowledgement: acknowledgement(entry),
            }),
          )
      : [],
  );
  return found.sort(
    (a, b) =>
      Number(b.severity === "failed") - Number(a.severity === "failed") ||
      Number(a.acknowledgement !== null) - Number(b.acknowledgement !== null) ||
      a.since - b.since,
  );
}
