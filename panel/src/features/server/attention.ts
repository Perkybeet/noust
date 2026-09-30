/**
 * What needs attention on the server, worst first: the one list the Overview tab shows and
 * the header's state is judged from, so the two never disagree.
 *
 * It is read from the summary (`GET /api/server/summary`: updates, reboot, support, disks,
 * clock, swap, failed units) and the last hardening checks (`GET /api/server/security`). A
 * check that says the same as a summary fact (security updates pending, a reboot due) is left
 * to the fact, which carries the numbers.
 *
 * Items hold data, not text: `attentionText` in the component that renders them says each one
 * in the active language.
 */

import type { Status } from "../../components/ui/StatusPill";
import type { SecurityCheck, SecurityOverview, ServerSummary } from "./queries";

export type Severity = "critical" | "warning";

/** Where an item's action takes the operator: a tab, or the reboot dialog. */
export type AttentionAction =
  | { kind: "tab"; to: "/server/updates" | "/server/storage" | "/server/system" | "/server/security"; view?: string }
  | { kind: "services"; state: "failed" }
  | { kind: "reboot" };

export type AttentionItem = { id: string; severity: Severity; action: AttentionAction } & (
  | { kind: "securityUpdates"; count: number }
  | { kind: "packagesBroken" }
  | { kind: "rebootRequired"; since: string | null; packages: readonly string[] }
  | { kind: "osUnsupported"; name: string; date: string | null }
  | { kind: "osEnding"; name: string; days: number; date: string | null }
  | { kind: "diskFull"; mount: string; percent: number; free: number | null }
  | { kind: "clockUnsynced"; timezone: string | null }
  | { kind: "noSwap" }
  | { kind: "failedUnits"; units: readonly string[] }
  | { kind: "check"; check: SecurityCheck }
);

/** Checks the summary already reports with its own numbers: listed once, from the summary. */
const COVERED_BY_SUMMARY: ReadonlySet<string> = new Set([
  "upd.security_pending",
  "upd.pkg_broken",
  "upd.reboot_required",
  "os.eol",
  "time.unsynced",
  "mem.no_swap",
  "disk.full",
  "sys.degraded",
]);

const RANK: Readonly<Record<Severity, number>> = { critical: 0, warning: 1 };

function fromSummary(summary: ServerSummary): AttentionItem[] {
  const items: AttentionItem[] = [];
  const updates = summary.updates;
  if (updates.broken === true) {
    items.push({ id: "packages-broken", kind: "packagesBroken", severity: "critical", action: { kind: "tab", to: "/server/updates" } });
  }
  if ((updates.security ?? 0) > 0) {
    items.push({
      id: "security-updates",
      kind: "securityUpdates",
      count: updates.security ?? 0,
      severity: "critical",
      action: { kind: "tab", to: "/server/updates" },
    });
  }
  const eol = summary.os.eol;
  if (eol.status === "expired") {
    items.push({ id: "os-eol", kind: "osUnsupported", name: summary.os.name, date: eol.end_date ?? null, severity: "critical", action: { kind: "tab", to: "/server/system" } });
  } else if (eol.status === "warn" && eol.days_left != null) {
    items.push({
      id: "os-eol",
      kind: "osEnding",
      name: summary.os.name,
      days: eol.days_left,
      date: eol.end_date ?? null,
      severity: "warning",
      action: { kind: "tab", to: "/server/system" },
    });
  }
  const disk = summary.disk;
  if ((disk.status === "critical" || disk.status === "warn") && disk.worst_mount != null && disk.worst_percent != null) {
    items.push({
      id: "disk",
      kind: "diskFull",
      mount: disk.worst_mount,
      percent: disk.worst_percent,
      free: disk.free_bytes ?? null,
      severity: disk.status === "critical" ? "critical" : "warning",
      action: { kind: "tab", to: "/server/storage" },
    });
  }
  if (summary.reboot.required === true) {
    items.push({
      id: "reboot",
      kind: "rebootRequired",
      since: summary.reboot.since ?? null,
      packages: summary.reboot.packages ?? [],
      severity: "warning",
      action: { kind: "reboot" },
    });
  }
  if (summary.time.synchronized === false) {
    items.push({ id: "clock", kind: "clockUnsynced", timezone: summary.time.timezone ?? null, severity: "warning", action: { kind: "tab", to: "/server/system" } });
  }
  if (summary.swap.recommended === true && (summary.swap.total_bytes ?? 0) === 0) {
    items.push({ id: "swap", kind: "noSwap", severity: "warning", action: { kind: "tab", to: "/server/storage" } });
  }
  const failed = summary.system.failed_units ?? [];
  if (failed.length > 0) {
    items.push({ id: "failed-units", kind: "failedUnits", units: failed, severity: "warning", action: { kind: "services", state: "failed" } });
  }
  return items;
}

/** A check's severity as "needs attention", or null when it is not worth a line here. */
export function checkSeverity(check: Pick<SecurityCheck, "severity" | "status">): Severity | null {
  if (check.status !== "fail" && check.status !== "warn") return null;
  if (check.severity === "critical") return "critical";
  if (check.severity === "warning") return "warning";
  return null;
}

/** The Security view a check's finding is read and fixed in. */
export function checkView(group: string): string {
  switch (group) {
    case "ssh":
      return "ssh";
    case "firewall":
      return "firewall";
    case "fail2ban":
      return "bans";
    default:
      return "checks";
  }
}

function fromChecks(security: SecurityOverview): AttentionItem[] {
  return security.attention.flatMap((check): AttentionItem[] => {
    const severity = checkSeverity(check);
    if (severity === null || COVERED_BY_SUMMARY.has(check.id)) return [];
    return [{ id: `check:${check.id}`, kind: "check", check, severity, action: { kind: "tab", to: "/server/security", view: checkView(check.group) } }];
  });
}

/** Everything that needs attention, the most serious first; the summary's facts before the checks. */
export function attentionItems(summary: ServerSummary | undefined, security: SecurityOverview | undefined): AttentionItem[] {
  const items = [...(summary ? fromSummary(summary) : []), ...(security ? fromChecks(security) : [])];
  // A stable sort: within a severity the order above holds.
  return items.sort((a, b) => RANK[a.severity] - RANK[b.severity]);
}

export interface Verdict {
  state: Status;
  label: "healthy" | "attention" | "critical";
}

/** The server's state for the header: the worst thing that needs attention. */
export function verdictOf(items: readonly AttentionItem[]): Verdict {
  if (items.some((item) => item.severity === "critical")) return { state: "failed", label: "critical" };
  if (items.length > 0) return { state: "warning", label: "attention" };
  return { state: "running", label: "healthy" };
}
