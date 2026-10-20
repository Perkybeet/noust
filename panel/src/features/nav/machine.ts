import { Clock, History, ScrollText } from "lucide-react";

import { area } from "../../app/navCore";

// The machine's other three destinations, each a screen of its own with no strip. The register is
// app/nav.ts; these are groups of it.
export const CRON_SCREENS = [area("cron", "/cron", "nav.cron.label", Clock, { group: "machine", keywords: "nav.cron.keywords" })];
export const LOGS_SCREENS = [area("logs", "/logs", "logSearch.nav.label", ScrollText, { group: "machine", keywords: "logSearch.nav.keywords" })];
export const ACTIVITY_SCREENS = [area("activity", "/activity", "nav.activity.label", History, { group: "machine", keywords: "nav.activity.keywords" })];
