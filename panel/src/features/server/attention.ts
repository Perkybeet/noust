/**
 * What needs attention on the machine: the Server page's slice of the one list the server joins (`GET /api/attention`).
 * The machine's own facts (updates, a reboot, a disk, the clock, the swap, failed services), Noust's own services that
 * failed and the open findings of Security are rows of that list, so what this page counts is part of what the bell and
 * Home count, and the three cannot say 2, 8 and 10 on the same afternoon (item 161).
 *
 * Pure, so each rule is tested without a page.
 */

import type { OverviewAttentionItem } from "../../api/queries/overview";
import type { Status } from "../../components/ui/StatusPill";

/** The kinds of row that are the machine's: what is wrong with it, what Noust runs on it that failed and what its checks found. */
const MACHINE_KINDS: ReadonlySet<string> = new Set(["machine", "unit", "finding"]);

/** The rows of the one list that are about this machine, in the list's own order (the worst first). */
export function serverItems(items: readonly OverviewAttentionItem[]): OverviewAttentionItem[] {
  return items.filter((item) => MACHINE_KINDS.has(String(item.subject["kind"])));
}

export interface Verdict {
  state: Status;
  label: "healthy" | "attention" | "critical";
}

/** The server's state for the header: the worst thing that needs attention on it. */
export function verdictOf(items: readonly OverviewAttentionItem[]): Verdict {
  if (items.some((item) => item.severity === "fail")) return { state: "failed", label: "critical" };
  if (items.length > 0) return { state: "warning", label: "attention" };
  return { state: "running", label: "healthy" };
}
