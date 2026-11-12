/**
 * Home's reading of `GET /api/overview`, as pure functions so every rule is tested without a
 * page: whether the server runs anything yet, and what the fleet's view adds to the list of a node
 * before 4.3. What needs attention on a server is the one list the server joins (`GET /api/attention`).
 */

import type { Overview, OverviewAttentionItem, OverviewAttentionReason } from "../../api/queries/overview";
import { appStatus } from "../../components/page/status";
import type { AppInfo } from "../apps/data";

/**
 * Stacks running outside their units. A node before 3.3 sends no `unmanaged` (a central reads
 * its overview through the proxy), and an `undefined` here turns every sum into NaN.
 */
export function unmanagedApps(apps: Overview["apps"]): number {
  return (apps as Partial<Overview["apps"]>).unmanaged ?? 0;
}

/** Every application the figure counts, whatever state it is in. */
export function appsTotal(apps: Overview["apps"]): number {
  return apps.running + apps.failed + apps.stopped + apps.static + unmanagedApps(apps);
}

/**
 * Whether this server runs nothing yet and has nothing to say: the Overview is a list of first steps instead. A first deploy
 * that failed leaves no application, so a server whose only news is that one is not empty: the first steps would hide the one
 * thing the person has to look at, on the day they matter most (item 141).
 */
export function isEmptyServer(overview: Overview): boolean {
  const { apps } = overview;
  if (apps.error) return false;
  if ((overview.attention?.length ?? 0) > 0 || overview.attention_total > 0) return false;
  return appsTotal(apps) === 0;
}

const RANK: Readonly<Record<string, number>> = { fail: 0, warn: 1 };

/**
 * A node's list with the applications that run and answer nothing added, worst first. A server of 4.3 says it itself (it keeps what the monitor
 * announced), so this is for the fleet's view of a node before 4.3, whose list cannot see a service that runs and answers nothing on its port.
 */
export function withNoAnswer(listed: readonly OverviewAttentionItem[], apps: readonly Pick<AppInfo, "domain" | "status">[]): OverviewAttentionItem[] {
  const items = listed.map((item) => ({ ...item, reasons: [...item.reasons] }));
  for (const app of apps) {
    const status = app.status.trim().toLowerCase();
    if (status !== "no_answer" && status !== "no answer") continue;
    if (appStatus(app.status).state !== "failed") continue;
    const reason: OverviewAttentionReason = {
      kind: "state",
      severity: "fail",
      code: "no_answer",
      params: {},
      detail: null,
      when: null,
      deployment_id: null,
      actions: ["view_log", "diagnose"],
    };
    const existing = items.find((item) => item.subject["kind"] === "app" && item.subject["domain"] === app.domain);
    if (existing) {
      if (existing.reasons.some((known) => known.code === "service_failed" || known.code === "no_answer")) continue;
      existing.reasons.unshift(reason);
      existing.severity = "fail";
      continue;
    }
    items.push({ id: `app:${app.domain}`, subject: { kind: "app", domain: app.domain }, title: app.domain, severity: "fail", reasons: [reason] });
  }
  return items.sort((a, b) => (RANK[a.severity] ?? 2) - (RANK[b.severity] ?? 2) || a.title.toLowerCase().localeCompare(b.title.toLowerCase()));
}
