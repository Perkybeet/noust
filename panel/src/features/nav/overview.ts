import { area, strip } from "../../app/navCore";

/** Home, the panels of widgets. The register is app/nav.ts; this is one group of it. */
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
  () => [],
);
