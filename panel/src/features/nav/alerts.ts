import { area, strip, tab } from "../../app/navCore";

/**
 * Alerts: what warns an operator before a person does. The rules on the metrics Noust records and
 * which of them are firing, the checks the monitor makes without a rule, the addresses asked from
 * outside, the public status page and the measurements themselves. They were two sections of Settings
 * in 4.0; what warns an operator is an area of its own, and the old addresses redirect
 * (app/redirects.ts). The register is app/nav.ts; this is one group of it.
 *
 * Across the servers the area opens at the fleet's alerts (`fleetTo`, features/nav/fleet.ts): the
 * rules of every server, the ones firing first, each naming its server.
 */
export const ALERTS_SCREENS = strip(
  area("alerts", "/alerts", "nav.alerts.label", {
    scope: "both",
    group: "happens",
    fleetTo: "/fleet/alerts",
    shortcut: ["g", "e"],
    goTo: "nav.alerts.goTo",
    keywords: "nav.settingsTabs.alerts.keywords",
    rootLabel: "nav.settingsTabs.alerts.label",
    command: "nav.settingsTabs.alerts.command",
  }),
  (alerts) => [
    tab(alerts, "checks", "nav.settingsTabs.checks.label", {
      exact: true,
      feature: "process_monitor",
      keywords: "nav.settingsTabs.checks.keywords",
      command: "nav.settingsTabs.checks.command",
    }),
    tab(alerts, "probes", "nav.settingsTabs.probes.label", {
      exact: true,
      feature: "process_monitor",
      keywords: "nav.settingsTabs.probes.keywords",
      command: "nav.settingsTabs.probes.command",
    }),
    tab(alerts, "status", "nav.settingsTabs.statusPage.label", {
      exact: true,
      feature: "status_page",
      keywords: "nav.settingsTabs.statusPage.keywords",
      command: "nav.settingsTabs.statusPage.command",
    }),
    tab(alerts, "metrics", "nav.settingsTabs.metrics.label", {
      exact: true,
      feature: "process_monitor",
      keywords: "nav.settingsTabs.metrics.keywords",
      command: "nav.settingsTabs.metrics.command",
    }),
  ],
);
