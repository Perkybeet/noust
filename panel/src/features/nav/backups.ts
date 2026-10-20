import { Archive } from "lucide-react";

import { area, strip, tab } from "../../app/navCore";

/** Backups and its four tabs. The register is app/nav.ts; this is one group of it. */
export const BACKUPS_SCREENS = strip(
  area("backups", "/backups", "nav.backups.label", Archive, {
    group: "runs",
    shortcut: ["g", "k"],
    goTo: "nav.backups.goTo",
    keywords: "nav.backups.keywords",
    rootLabel: "backups.tabs.backups",
  }),
  (backups) => [
    tab(backups, "schedules", "backups.tabs.schedules"),
    tab(backups, "volumes", "volumeBackups.tab"),
    tab(backups, "noust", "selfBackup.tab"),
    tab(backups, "destinations", "backups.tabs.destinations"),
  ],
);
