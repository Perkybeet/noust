import { area, strip, tab } from "../../app/navCore";

/**
 * Alerts: the rules on the recorded metrics and which of them are firing, then the probes asked from
 * outside and the public status page. They were two sections of Settings in 4.0; what warns an
 * operator is an area of its own, and the old addresses redirect (app/redirects.ts). The register is
 * app/nav.ts; this is one group of it.
 */
export const ALERTS_SCREENS = strip(
  area("alerts", "/alerts", "nav.alerts.label", {
    scope: "both",
    group: "happens",
    shortcut: ["g", "e"],
    goTo: "nav.alerts.goTo",
    keywords: "nav.settingsTabs.alerts.keywords",
    rootLabel: "nav.settingsTabs.alerts.label",
    command: "nav.settingsTabs.alerts.command",
  }),
  (alerts) => [
    tab(alerts, "probes", "nav.settingsTabs.probes.label", {
      exact: true,
      keywords: "nav.settingsTabs.probes.keywords",
      command: "nav.settingsTabs.probes.command",
    }),
  ],
);
