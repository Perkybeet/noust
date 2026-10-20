import { Clock, History, ScrollText } from "lucide-react";

import { area } from "../../app/navCore";

// Cron is scheduled work, like Backups: it is with what runs. Logs and Activity are what happened,
// each a screen of its own with no strip. The register is app/nav.ts; these are groups of it.
// Activity also answers `g d` (the shell keeps it: inside an application it means "go to deployments").
export const CRON_SCREENS = [area("cron", "/cron", "nav.cron.label", Clock, { group: "runs", shortcut: ["g", "c"], goTo: "nav.cron.goTo", keywords: "nav.cron.keywords" })];
export const LOGS_SCREENS = [
  area("logs", "/logs", "logSearch.nav.label", ScrollText, { group: "happens", shortcut: ["g", "l"], goTo: "nav.logs.goTo", keywords: "logSearch.nav.keywords" }),
];
export const ACTIVITY_SCREENS = [
  area("activity", "/activity", "nav.activity.label", History, { group: "happens", shortcut: ["g", "y"], goTo: "nav.activity.goTo", keywords: "nav.activity.keywords" }),
];
