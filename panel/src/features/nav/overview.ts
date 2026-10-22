import { area, strip, tab } from "../../app/navCore";

/** The Overview and the Map under it (D-6). The register is app/nav.ts; this is one group of it. */
export const OVERVIEW_SCREENS = strip(
  area("overview", "/", "nav.overview.label", { group: "runs", shortcut: ["g", "o"], goTo: "nav.overview.goTo", keywords: "nav.overview.keywords" }),
  (overview) => [tab(overview, "map", "map.title", { command: "shell.commands.openMap", keywords: "shell.commands.openMapKeywords" })],
);
