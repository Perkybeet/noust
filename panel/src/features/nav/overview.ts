import { area, strip, tab } from "../../app/navCore";

/** Home and the Map under it (D-6). The register is app/nav.ts; this is one group of it. */
export const OVERVIEW_SCREENS = strip(
  area("overview", "/", "nav.overview.label", {
    scope: "both",
    group: "home",
    fleetTo: "/fleet",
    shortcut: ["g", "o"],
    goTo: "nav.overview.goTo",
    keywords: "nav.overview.keywords",
    // The first tab of its strip is named as the page is, until the page is renamed with the area (4.1, O5-INICIO-APPS).
    rootLabel: "overview.title",
  }),
  (overview) => [tab(overview, "map", "map.title", { command: "shell.commands.openMap", keywords: "shell.commands.openMapKeywords" })],
);
