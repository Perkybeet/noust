/**
 * What needs an operator is one list that the server joins and ranks (`GET /api/attention`, built by
 * `managers/attention.py`): the applications, the machine's own facts, the findings of Security, the alerts
 * and the probes, and what is each person's - the requests waiting for their decision, a newer Noust, the
 * recipes behind. The bell, the Overview's "Needs attention", the Server page's slice and the sidebar's
 * counters read that one list, so they cannot disagree (NV-16); the console joins nothing.
 *
 * What is left here is what the sidebar counts from the list, pure so that each rule is tested without a page.
 */

import type { OverviewAttentionItem } from "../../api/queries/overview";

/** Days before expiry at which a certificate starts needing attention (spec, Task 3.11). */
export const CERT_WARNING_DAYS = 21;

export type Severity = "fail" | "warn";

/** What the sidebar counts beside a destination, from the same list the inbox shows. */
export interface AttentionCounters {
  /** Applications that failed: red beside Applications. */
  appsFailed: number;
  /** Certificates that expired or expire soon: beside Domains, red if any expired. */
  certificates: number;
  certificatesExpired: number;
  /** Noust's own services that failed, beyond the applications': red beside Server. */
  unitsFailed: number;
  /** What is wrong with the machine itself and is a failure (a full disk, broken packages): red beside Server too. */
  machineFailed: number;
  /** The findings of the hardening checks that are critical: red beside Security. */
  findingsFailed: number;
  /** Alerts that fire and probes that are down: red beside Alerts. */
  alerts: number;
}

export function attentionCounters(items: readonly OverviewAttentionItem[]): AttentionCounters {
  let appsFailed = 0;
  let certificates = 0;
  let certificatesExpired = 0;
  let unitsFailed = 0;
  let machineFailed = 0;
  let findingsFailed = 0;
  let alerts = 0;
  for (const item of items) {
    const kind = item.subject["kind"];
    if (kind === "app" && item.reasons.some((reason) => (reason.kind === "state" || reason.kind === "deploy") && reason.severity === "fail")) appsFailed += 1;
    if (kind === "certificate" || (kind === "app" && item.reasons.some((reason) => reason.kind === "certificate"))) {
      certificates += 1;
      if (item.reasons.some((reason) => reason.kind === "certificate" && reason.code === "certificate_expired")) certificatesExpired += 1;
    }
    // An application's own units are its state; these are Noust's others.
    if (kind === "unit" && item.reasons.some((reason) => reason.severity === "fail")) unitsFailed += 1;
    if (kind === "machine" && item.severity === "fail") machineFailed += 1;
    if (kind === "finding" && item.severity === "fail") findingsFailed += 1;
    if (kind === "alert" || kind === "probe") alerts += 1;
  }
  return { appsFailed, certificates, certificatesExpired, unitsFailed, machineFailed, findingsFailed, alerts };
}
