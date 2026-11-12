import type { OverviewAttentionItem } from "../../api/queries/overview";
import type { Watch, Watching } from "../../api/queries/watching";

/** Specimens of the server's answers, for the design review of the opening of Alerts and of the one list (items 159 and 161). */

const REACH = [
  { name: "telegram", type: "telegram", limited: false },
  { name: "email", type: "email", limited: false },
];

function watch(id: string, group: string, params: Record<string, number> = {}, overrides: Partial<Watch> = {}): Watch {
  return { id, group, state: "on", events: [], params, reach: REACH, offer: null, asks_outside: false, ...overrides };
}

export const WATCHING: Watching = {
  watcher: { state: "running", fix: null },
  notifications: { enabled: true, paused_until: null, channels: [{ name: "telegram", type: "telegram" }, { name: "email", type: "email" }] },
  watches: [
    watch("apps_down", "applications"),
    watch("deploys", "applications"),
    watch("certificates", "domains", { days: 14 }),
    watch("domain_expiry", "domains", {}, { state: "off", offer: { key: "monitor.domain_expiry", value: true }, asks_outside: true }),
    watch("disk", "server", { percent: 90 }),
    watch("updates", "server", { hours: 24 }),
    watch("access_changes", "server"),
    watch("backups", "backups"),
    watch("backups_missing", "backups", { days: 2 }),
    watch("tokens", "noust", { days: 7 }),
    watch("releases", "noust"),
    watch("recipes", "noust", {}, { state: "off" }),
  ],
};

/** No channel configured: what Noust watches reaches nobody. */
export const WATCHING_NO_CHANNEL: Watching = {
  ...WATCHING,
  notifications: { enabled: true, paused_until: null, channels: [] },
  watches: (WATCHING.watches ?? []).map((entry) => ({ ...entry, reach: [] })),
};

/** The master switch is off. */
export const WATCHING_OFF: Watching = { ...WATCHING_NO_CHANNEL, notifications: { enabled: false, paused_until: null, channels: [] } };

/** The monitor does not run, and one channel hears less than the rest. */
export const WATCHING_NO_MONITOR: Watching = {
  ...WATCHING,
  watcher: { state: "disabled", fix: "noust monitor enable" },
  notifications: { ...WATCHING.notifications, channels: [{ name: "telegram", type: "telegram" }, { name: "email", type: "email" }, { name: "pager", type: "pagerduty" }] },
  watches: (WATCHING.watches ?? []).map((entry, index) => ({
    ...entry,
    reach: index % 3 === 0 ? [...REACH, { name: "pager", type: "pagerduty", limited: true }] : REACH,
  })),
};

const SINCE = "2026-11-12T08:12:00+00:00";

function item(id: string, subject: Record<string, unknown>, title: string, origin: string, severity: "fail" | "warn", reasons: Partial<OverviewAttentionItem["reasons"][number]>[]): OverviewAttentionItem {
  return {
    id,
    subject,
    title,
    origin,
    severity,
    reasons: reasons.map((reason) => ({ kind: origin, origin, severity, code: "x", params: {}, detail: null, when: null, deployment_id: null, actions: [], ...reason })),
  };
}

/** One list from every origin, worst first: what the bell, Home and the Server page draw from the same answer. */
export const ATTENTION: readonly OverviewAttentionItem[] = [
  item("app:shop.example.com", { kind: "app", domain: "shop.example.com" }, "shop.example.com", "state", "fail", [
    { kind: "state", code: "service_failed", actions: ["view_log", "diagnose"] },
    { kind: "deploy", code: "deploy_failed", detail: "npm ERR! code ELIFECYCLE", when: SINCE, deployment_id: 41, actions: ["view_deployment"] },
  ]),
  item("machine:disk", { kind: "machine", fact: "disk" }, "Disk", "state", "fail", [
    { kind: "machine", code: "disk_full", params: { mount: "/var", percent: 96.4, free: 3_200_000_000 }, actions: ["free_space"] },
  ]),
  item("security:fw.docker_bypass", { kind: "finding", check: "fw.docker_bypass", group: "firewall" }, "Docker publishes ports around the firewall", "security", "fail", [
    { kind: "security", code: "security_finding", params: { check: "fw.docker_bypass", group: "firewall", severity: "critical" }, detail: "Port 5432 (shop_postgres) is published on 0.0.0.0 by Docker.", actions: ["review_finding"] },
  ]),
  item("probe:blog", { kind: "probe", name: "blog-home" }, "blog-home", "alert", "fail", [
    { kind: "probe", code: "probe_down", detail: "HTTP 503 Service Unavailable", when: "2026-11-12T10:40:00+00:00", actions: ["open_probes"] },
  ]),
  item("certificate:blog.example.com", { kind: "certificate", domain: "blog.example.com" }, "blog.example.com", "state", "warn", [
    { kind: "certificate", code: "certificate_expires_in", params: { days: 12 }, actions: ["renew_certificate"] },
  ]),
  item("machine:security_updates", { kind: "machine", fact: "security_updates" }, "Security updates", "state", "warn", [
    { kind: "machine", code: "security_updates", params: { count: 4 }, actions: ["open_updates"] },
  ]),
  item("alert:cpu-high:", { kind: "alert", rule: "cpu-high", subject: "" }, "cpu-high", "alert", "warn", [
    { kind: "alert", code: "alert_firing", params: { rule: "cpu-high", subject: "", since: 1_790_000_000 }, when: "2026-11-12T09:05:00+00:00", actions: ["acknowledge_alert"] },
  ]),
  item("security:ssh.password_auth", { kind: "finding", check: "ssh.password_auth", group: "ssh" }, "SSH accepts passwords", "security", "warn", [
    { kind: "security", code: "security_finding", params: { check: "ssh.password_auth", group: "ssh", severity: "warning" }, detail: "passwordauthentication is yes.", actions: ["review_finding"] },
  ]),
  item("approval:7", { kind: "approval", id: 7 }, "#7", "state", "warn", [{ kind: "approval", code: "approval_waiting", params: { id: 7, by: "ana" }, when: "2026-11-12T11:30:00+00:00", actions: ["review_approval"] }]),
];
