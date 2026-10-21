/**
 * What needs an operator, beyond what the server lists. The server's own list (`GET /api/overview`,
 * `managers/overview.py`) names the problems it can read from one place: an application that is
 * down, a deployment that failed, a certificate that is expiring, a unit that failed, what the
 * monitor found. The console adds what only other reads know - an alert that is firing, a probe that
 * is down, a request waiting for a second person, a Noust or a recipe with an update - as items of
 * the same shape, so the Overview's "Needs attention", the inbox and the sidebar's counters are one
 * definition read three times (NV-16): `mergeAttention` puts them in one list, worst first, and
 * `attentionCounters` is what the sidebar counts from it.
 *
 * Pure, so every rule is tested without a page. The reads that feed it are `features/inbox/useAttention`.
 */

import type { AlertRuleList } from "../../api/queries/alerts";
import type { OverviewAttentionItem, OverviewAttentionReason } from "../../api/queries/overview";
import type { ProbeList } from "../../api/queries/probes";
import { parseTimestamp } from "../../lib/format";
import type { ApprovalList } from "../approvals/api";
import { selectFiringAlerts } from "../settings/alerts/data";
import { selectFailingProbes } from "../settings/probes/data";

/** Days before expiry at which a certificate starts needing attention (spec, Task 3.11). */
export const CERT_WARNING_DAYS = 21;

export type Severity = "fail" | "warn";

const RANK: Readonly<Record<string, number>> = { fail: 0, warn: 1 };

/** A moment the API gives as Unix seconds (or a date string), in the form the server's own items use. */
function moment(value: number | string | null | undefined): string | null {
  return parseTimestamp(value)?.toISOString() ?? null;
}

/** The kinds of subject the console adds to the server's list. */
export type ExtraKind = "alert" | "probe" | "approval" | "noust" | "recipe";

/** A firing alert, as the rules list says it: one item per rule and subject, taken or not. */
export function alertItems(rules: AlertRuleList | undefined): OverviewAttentionItem[] {
  // Which alerts fire is the settings page's definition, read here and on that page alike (AL-1).
  return selectFiringAlerts(rules).map((alert): OverviewAttentionItem => {
    const severity: Severity = alert.severity === "failed" ? "fail" : "warn";
    const taken = alert.acknowledgement;
    const reason: OverviewAttentionReason = {
      kind: "alert",
      severity,
      code: taken === null ? "alert_firing" : "alert_taken",
      params: { rule: alert.rule, subject: alert.subject, since: alert.since, ...(taken === null ? {} : { by: taken.by, at: taken.at }) },
      detail: null,
      when: moment(alert.since),
      deployment_id: null,
      actions: [taken === null ? "acknowledge_alert" : "reopen_alert"],
    };
    return {
      id: `alert:${alert.rule}:${alert.subject}`,
      subject: { kind: "alert", rule: alert.rule, subject: alert.subject },
      title: alert.subject === "" ? alert.rule : `${alert.rule} · ${alert.subject}`,
      severity,
      reasons: [reason],
    };
  });
}

/** A probe that is down: what it asked and what it got, in the probe's own words. */
export function probeItems(probes: ProbeList | undefined): OverviewAttentionItem[] {
  return selectFailingProbes(probes).map((probe): OverviewAttentionItem => {
    const reason: OverviewAttentionReason = {
      kind: "probe",
      severity: "fail",
      code: "probe_down",
      params: { probe: probe.name, since: probe.since },
      detail: probe.lastError,
      when: moment(probe.since),
      deployment_id: null,
      actions: ["open_probes"],
    };
    return { id: `probe:${probe.name}`, subject: { kind: "probe", name: probe.name }, title: probe.name, severity: "fail", reasons: [reason] };
  });
}

/** The requests waiting for this person's decision: only those they may decide. */
export function approvalItems(list: ApprovalList | undefined): OverviewAttentionItem[] {
  return (list?.approvals ?? [])
    .filter((approval) => approval.state === "requested" && approval.can_decide)
    .map((approval): OverviewAttentionItem => {
      const reason: OverviewAttentionReason = {
        kind: "approval",
        severity: "warn",
        code: "approval_waiting",
        params: { id: approval.id, by: approval.requester.name },
        detail: null,
        when: moment(approval.created_at),
        deployment_id: null,
        actions: ["review_approval"],
      };
      return { id: `approval:${String(approval.id)}`, subject: { kind: "approval", id: approval.id }, title: `#${String(approval.id)}`, severity: "warn", reasons: [reason] };
    });
}

/** A newer Noust than the one this server runs; nothing when the check is off or the server is current. */
export function noustItems(info: { has_update: boolean; latest_version?: string | null; security_update?: boolean; current_version: string } | undefined): OverviewAttentionItem[] {
  if (info?.has_update !== true) return [];
  const security = info.security_update === true;
  const reason: OverviewAttentionReason = {
    kind: "noust",
    severity: "warn",
    code: security ? "noust_security_update" : "noust_update",
    params: { version: info.latest_version ?? "", current: info.current_version },
    detail: null,
    when: null,
    deployment_id: null,
    actions: ["open_about"],
  };
  return [{ id: "noust:update", subject: { kind: "noust" }, title: "Noust", severity: "warn", reasons: [reason] }];
}

/** The applications installed from a recipe whose recipe has a newer version. */
export function recipeItems(installed: { items: readonly { domain: string; recipe: string; update_available: boolean; installed_version: string; latest_version?: string | null }[] } | undefined): OverviewAttentionItem[] {
  return (installed?.items ?? [])
    .filter((item) => item.update_available)
    .map((item): OverviewAttentionItem => {
      const reason: OverviewAttentionReason = {
        kind: "recipe",
        severity: "warn",
        code: "recipe_update",
        params: { recipe: item.recipe, version: item.latest_version ?? "", current: item.installed_version },
        detail: null,
        when: null,
        deployment_id: null,
        actions: ["open_recipes"],
      };
      return { id: `recipe:${item.domain}`, subject: { kind: "recipe", domain: item.domain }, title: item.domain, severity: "warn", reasons: [reason] };
    });
}

/**
 * The server's list and what the console adds, worst first and then by title. An item the server
 * and the console both name (an application) is kept once, with the reasons of both.
 */
export function mergeAttention(base: readonly OverviewAttentionItem[], extras: readonly OverviewAttentionItem[]): OverviewAttentionItem[] {
  const merged = new Map<string, OverviewAttentionItem>();
  for (const item of [...base, ...extras]) {
    const known = merged.get(item.id);
    if (known === undefined) {
      merged.set(item.id, { ...item, reasons: [...item.reasons] });
      continue;
    }
    known.reasons.push(...item.reasons);
    if ((RANK[item.severity] ?? 2) < (RANK[known.severity] ?? 2)) known.severity = item.severity;
  }
  return [...merged.values()].sort((a, b) => (RANK[a.severity] ?? 2) - (RANK[b.severity] ?? 2) || a.title.toLowerCase().localeCompare(b.title.toLowerCase()));
}

/** What the sidebar counts beside a destination, from the same list the inbox shows. */
export interface AttentionCounters {
  /** Applications that failed: red beside Applications. */
  appsFailed: number;
  /** Certificates that expired or expire soon: beside Domains, red if any expired. */
  certificates: number;
  certificatesExpired: number;
  /** Noust's own services that failed, beyond the applications': red beside Server. */
  unitsFailed: number;
}

export function attentionCounters(items: readonly OverviewAttentionItem[]): AttentionCounters {
  let appsFailed = 0;
  let certificates = 0;
  let certificatesExpired = 0;
  let unitsFailed = 0;
  for (const item of items) {
    const kind = item.subject["kind"];
    if (kind === "app" && item.reasons.some((reason) => (reason.kind === "state" || reason.kind === "deploy") && reason.severity === "fail")) appsFailed += 1;
    if (kind === "certificate" || (kind === "app" && item.reasons.some((reason) => reason.kind === "certificate"))) {
      certificates += 1;
      if (item.reasons.some((reason) => reason.kind === "certificate" && reason.code === "certificate_expired")) certificatesExpired += 1;
    }
    // An application's own units are its state; these are Noust's others.
    if (kind === "unit" && item.reasons.some((reason) => reason.severity === "fail")) unitsFailed += 1;
  }
  return { appsFailed, certificates, certificatesExpired, unitsFailed };
}
