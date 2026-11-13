import { area, strip, tab } from "../../app/navCore";

/** Backups and its four tabs. The register is app/nav.ts; this is one group of it. */
export const BACKUPS_SCREENS = strip(
  area("backups", "/backups", "nav.backups.label", {
    scope: "both",
    group: "safe",
    feature: "application_backups",
    fleetTo: "/fleet/backups",
    shortcut: ["g", "k"],
    goTo: "nav.backups.goTo",
    keywords: "nav.backups.keywords",
    rootLabel: "backups.tabs.backups",
  }),
  (backups) => [
    tab(backups, "schedules", "backups.tabs.schedules", { feature: "application_backups" }),
    tab(backups, "volumes", "volumeBackups.tab", { feature: "volume_backups" }),
    tab(backups, "noust", "selfBackup.tab"),
    tab(backups, "destinations", "backups.tabs.destinations"),
  ],
);
